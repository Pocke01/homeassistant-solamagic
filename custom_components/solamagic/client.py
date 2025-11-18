from __future__ import annotations
import logging
import asyncio
from homeassistant.core import HomeAssistant
from .bluetooth import SolamagicBleClient
from .const import (
    CMD_OFF, CMD_ON_33, CMD_ON_66, CMD_ON_100,
    CHAR_CMD_F001, CHAR_ALT_F002, CONF_WRITE_MODE,
    CCCD_CMD, CCCD_NTF1, CCCD_NTF2
)

_LOGGER = logging.getLogger(__name__)

class SolamagicClient:
    def __init__(self, hass: HomeAssistant, address: str, write_mode: str = "handle",
                 command_char: str | None = None) -> None:
        self._ble = SolamagicBleClient(hass, address)
        self._cmd_char = command_char or CHAR_CMD_F001
        self._alt_char = CHAR_ALT_F002
        self._write_mode = write_mode
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """
        CRITICAL INITIALIZATION SEQUENCE - based on sniffer analysis!

        This sequence must be run ONCE per connection in exactly this order:

        1. Write initialization payload to handle 0x001F
        2. Enable CCCD on 0x0030 (notifications for 0x002F)
        3. Enable CCCD on 0x0033 (notifications for 0x0032)
        4. Enable CCCD on 0x0029 (notifications for 0x0028) - LAST!

        This order is CRITICAL! Heatlink uses exactly this sequence.
        """
        if self._initialized:
            return

        _LOGGER.info("Running initialization sequence...")

        # Step 1: Write initialization payload to 0x001F
        # This "unlocks" the device for commands
        _LOGGER.debug("Step 1: Writing initialization payload to 0x001F")
        await self._ble.write_init_sequence()
        await asyncio.sleep(0.05)

        # Step 2: Enable notifications on 0x002F (via CCCD 0x0030)
        # This is the first notification channel
        _LOGGER.debug("Step 2: Enabling notifications on 0x002F (CCCD 0x0030)")
        try:
            await self._ble.write_cccd(CCCD_NTF1, bytes([0x01, 0x00]))
            _LOGGER.debug("✓ CCCD 0x%04X enabled (notifications)", CCCD_NTF1)
        except Exception as e:
            _LOGGER.warning("Could not enable CCCD 0x%04X: %s", CCCD_NTF1, e)

        await asyncio.sleep(0.05)

        # Step 3: Enable notifications on 0x0032 (via CCCD 0x0033)
        # This is the status/data channel
        _LOGGER.debug("Step 3: Enabling notifications on 0x0032 (CCCD 0x0033)")
        try:
            await self._ble.write_cccd(CCCD_NTF2, bytes([0x01, 0x00]))
            _LOGGER.debug("✓ CCCD 0x%04X enabled (notifications)", CCCD_NTF2)
        except Exception as e:
            _LOGGER.warning("Could not enable CCCD 0x%04X: %s", CCCD_NTF2, e)

        await asyncio.sleep(0.05)

        # Step 4: Enable notifications on 0x0028 (via CCCD 0x0029) - LAST!
        # This is the command channel - must be enabled last!
        _LOGGER.debug("Step 4: Enabling notifications on 0x0028 (CCCD 0x0029) - LAST!")
        try:
            await self._ble.write_cccd(CCCD_CMD, bytes([0x01, 0x00]))
            _LOGGER.debug("✓ CCCD 0x%04X enabled (notifications)", CCCD_CMD)
        except Exception as e:
            _LOGGER.warning("Could not enable CCCD 0x%04X: %s", CCCD_CMD, e)

        await asyncio.sleep(0.1)
        self._initialized = True
        _LOGGER.info("✓ Initialization sequence complete!")

    async def _wait_for_confirmation(self, expected_cmd: bytes, timeout: float = 1.0) -> bool:
        """
        Wait for command confirmation (2 bytes from handle 0x0028).

        The heater does NOT send a separate status notification after the command.
        It only confirms the command by sending the same bytes back.

        What we get:
        1. Send: 01 21 (33% command)
        2. Receive confirmation: 01 21 (2 bytes)
        3. NOTHING MORE! No status notification comes separately.

        Therefore we must assume the command succeeded when we get the confirmation.
        """
        start_time = asyncio.get_event_loop().time()
        confirmed = False

        # Create a temporary callback that captures confirmations
        def confirmation_checker(data: bytes):
            nonlocal confirmed
            if len(data) == 2 and data == expected_cmd:
                confirmed = True
                _LOGGER.debug(f"✓ Command confirmed: {data.hex()}")

        # Save old callback and set ours
        old_callback = self._ble._confirmation_callback
        self._ble._confirmation_callback = confirmation_checker

        try:
            # Wait for confirmation
            while (asyncio.get_event_loop().time() - start_time) < timeout:
                if confirmed:
                    return True
                await asyncio.sleep(0.05)

            _LOGGER.warning(f"Timeout waiting for confirmation of {expected_cmd.hex()}")
            return False

        finally:
            # Restore callback
            self._ble._confirmation_callback = old_callback

    async def set_level(self, pct: int) -> None:
        """
        Set heater level to 0/33/66/100%.

        Based on Bluetooth sniffer analysis from the xHeatlink app:

        Initialization (runs automatically on first command):
        1. Write 0x001F: FF FF FF FD 94 34 00 00 00
        2. Enable CCCD 0x0030: 01 00 (notifications)
        3. Enable CCCD 0x0033: 01 00 (notifications)
        4. Enable CCCD 0x0029: 01 00 (notifications) - LAST!

        Command sequence:
        - 33%:  Send 01 21 (1 command in sniffer log)
        - 66%:  Send 01 42 (1 command)
        - 100%: Send 01 64 (1 command)
        - OFF:  Send 00 21 (~21 commands with ~16ms delay)

        All commands use Write Command (response=False) for speed.

        IMPORTANT: The heater does NOT send a separate status notification after the command!
        It only confirms with the same bytes (2 bytes) back on handle 0x0028.
        We must therefore assume the command succeeded and update status manually.
        """
        if pct not in (0, 33, 66, 100):
            raise ValueError("pct must be one of 0, 33, 66, 100")

        # CRITICAL: Run initialization sequence on first command
        await self._ensure_initialized()

        _LOGGER.info("Setting heater to %d%%", pct)

        if pct == 0:
            # OFF: Send 00 21 many times (21 commands according to sniffer)
            _LOGGER.debug("Sending OFF command (00 21) x 21")
            for i in range(21):
                await self._ble.write_handle_any(
                    0x0028,
                    CMD_OFF,
                    response=False,
                    repeat=1,
                    delay_ms=0
                )
                await asyncio.sleep(0.016)  # ~16ms delay between commands

            _LOGGER.info("✓ OFF sequence complete")

            # Wait briefly for confirmation
            await asyncio.sleep(0.2)

            # Update status directly (heater doesn't send separate notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(0)
                    _LOGGER.info("✓ Updated status to 0% (OFF confirmed)")
                except Exception as e:
                    _LOGGER.error(f"Status callback error: {e}")

        elif pct == 33:
            # 33%: Send 01 21 once
            _LOGGER.debug("Sending 33% command (01 21)")
            await self._ble.write_handle_any(
                0x0028,
                CMD_ON_33,
                response=False,
                repeat=1,
                delay_ms=0
            )
            _LOGGER.info("✓ 33% command sent")

            # Wait briefly for confirmation
            await asyncio.sleep(0.2)

            # Update status directly (heater doesn't send separate notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(33)
                    _LOGGER.info("✓ Updated status to 33% (command confirmed)")
                except Exception as e:
                    _LOGGER.error(f"Status callback error: {e}")

        elif pct == 66:
            # 66%: Send 01 42 once
            _LOGGER.debug("Sending 66% command (01 42)")
            await self._ble.write_handle_any(
                0x0028,
                CMD_ON_66,
                response=False,
                repeat=1,
                delay_ms=0
            )
            _LOGGER.info("✓ 66% command sent")

            # Wait briefly for confirmation
            await asyncio.sleep(0.2)

            # Update status directly (heater doesn't send separate notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(66)
                    _LOGGER.info("✓ Updated status to 66% (command confirmed)")
                except Exception as e:
                    _LOGGER.error(f"Status callback error: {e}")

        elif pct == 100:
            # 100%: Send 01 64 once
            _LOGGER.debug("Sending 100% command (01 64)")
            await self._ble.write_handle_any(
                0x0028,
                CMD_ON_100,
                response=False,
                repeat=1,
                delay_ms=0
            )
            _LOGGER.info("✓ 100% command sent")

            # Wait briefly for confirmation
            await asyncio.sleep(0.2)

            # Update status directly (heater doesn't send separate notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(100)
                    _LOGGER.info("✓ Updated status to 100% (command confirmed)")
                except Exception as e:
                    _LOGGER.error(f"Status callback error: {e}")

    async def off(self) -> None:
        """Turn off the heater"""
        await self.set_level(0)

    # Service API (used by __init__.py services)
    async def write_handle_raw(self, data: bytes, response: bool=False,
                              repeat: int=1, delay_ms: int=100) -> None:
        """
        Direct handle writing for services.
        Used by solamagic.write_handle service.
        """
        await self._ensure_initialized()
        await self._ble.write_handle_raw(data, response=response,
                                         repeat=repeat, delay_ms=delay_ms)

    async def write_handle_any(self, handle: int, data: bytes, response: bool=False,
                              repeat: int=1, delay_ms: int=100) -> None:
        """
        Write to arbitrary handle.
        Used by solamagic.write_handle_any service.
        """
        await self._ensure_initialized()
        await self._ble.write_handle_any(handle, data, response=response,
                                         repeat=repeat, delay_ms=delay_ms)

    async def write_uuid_raw(self, char_uuid: str, data: bytes,
                            response: bool=False) -> None:
        """
        Write via UUID.
        Used by solamagic.write_uuid service.
        """
        await self._ensure_initialized()
        await self._ble.write_uuid_simple(char_uuid, data, response=response)

    async def disconnect(self) -> None:
        """
        Disconnect from device.
        Resets initialization status so it runs again on next connection.
        """
        self._initialized = False
        await self._ble.disconnect()