from __future__ import annotations
import logging
import asyncio
import binascii

from typing import Callable

from homeassistant.core import HomeAssistant

from .bluetooth import SolamagicBleClient
from .const import (
    CCCD_ENABLE_DELAY_MS,
    CHAR_ALT_F002,
    CHAR_CMD_F001,
    CMD_CONFIRMATION_DELAY_MS,
    CMD_OFF,
    CMD_OFF_DELAY_MS,
    CMD_OFF_REPEAT_COUNT,
    CMD_ON_100,
    CMD_ON_33,
    CMD_ON_66,
    CONF_DISCONNECT_TIMEOUT,
    CONF_INIT_TOKEN,
    CONF_WRITE_MODE,
    DEFAULT_DISCONNECT_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

class SolamagicClient:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 write_mode: str = "handle",
                 command_char: str | None = None) -> None:
        self._hass = hass
        self._entry = entry
        address: str = entry.data["address"]
        disconnect_timeout = entry.options.get(
            CONF_DISCONNECT_TIMEOUT,
            entry.data.get(CONF_DISCONNECT_TIMEOUT, DEFAULT_DISCONNECT_TIMEOUT)
        )
        # Token stored by earlier versions (read from the device). Kept as an
        # override; a mismatch with the computed token is logged by the BLE layer.
        stored_token: bytes | None = None
        init_hex = entry.data.get(CONF_INIT_TOKEN)
        if init_hex:
            try:
                stored_token = binascii.unhexlify(init_hex)
            except Exception:
                stored_token = None
        self._ble = SolamagicBleClient(hass, address, disconnect_timeout, stored_token)
        self._cmd_char = command_char or CHAR_CMD_F001
        self._alt_char = CHAR_ALT_F002
        self._write_mode = write_mode
        self._reappear_listeners: list[Callable[[], None]] = []

    def add_reappear_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` when the heater is seen again after being away."""
        self._reappear_listeners.append(listener)

        def _remove() -> None:
            if listener in self._reappear_listeners:
                self._reappear_listeners.remove(listener)

        return _remove

    def notify_reappeared(self) -> None:
        """Tell listeners (e.g. the sensor) to refresh now."""
        for listener in list(self._reappear_listeners):
            try:
                listener()
            except Exception as err:  # Broad catch OK: user callback
                _LOGGER.error("[%s] Reappear listener error: %s", self._entry.data.get("address"), err)

    async def _ensure_initialized(self) -> None:
        """Make sure the link is connected and unlocked.

        The unlock (token write + notifications) is tied to the BLE connection
        itself, so a dropped link - including a heater losing power - can never
        leave a stale "initialised" flag behind.
        """
        await self._ble.ensure_ready()

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
                _LOGGER.debug("[%s] Command confirmed: %s", self._entry.data.get("address"), data.hex())

        # Save old callback and set ours
        old_callback = self._ble._confirmation_callback
        self._ble._confirmation_callback = confirmation_checker

        try:
            # Wait for confirmation
            while (asyncio.get_event_loop().time() - start_time) < timeout:
                if confirmed:
                    return True
                await asyncio.sleep(CCCD_ENABLE_DELAY_MS / 1000)

            _LOGGER.warning("[%s] Timeout waiting for confirmation of %s", self._entry.data.get("address"), expected_cmd.hex())
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

        Args:
            pct: Power level percentage (0, 33, 66, or 100)

        Raises:
            ValueError: If pct is not one of the valid values (0, 33, 66, 100)
        """
        if pct not in (0, 33, 66, 100):
            raise ValueError("pct must be one of 0, 33, 66, 100")

        # CRITICAL: Run initialization sequence on first command
        await self._ensure_initialized()

        _LOGGER.info("[%s] Setting heater to %d%%", self._entry.data.get("address"), pct)

        if pct == 0:
            # OFF: Send 00 21 many times (21 commands according to sniffer)
            _LOGGER.debug("[%s] Sending OFF command (00 21) x 21", self._entry.data.get("address"))
            for i in range(CMD_OFF_REPEAT_COUNT):
                await self._ble.write_handle_raw(CMD_OFF, response=False, repeat=1, delay_ms=0)
                await asyncio.sleep(CMD_OFF_DELAY_MS / 1000)  # ~16ms delay between commands

            _LOGGER.info("[%s] OFF sequence complete", self._entry.data.get("address"))

            # Wait briefly for confirmation
            await asyncio.sleep(CMD_CONFIRMATION_DELAY_MS / 1000)

            # Set expected level to filter stale notifications
            self._ble.set_expected_level(0)

            # Update status directly (heater doesn't send separate notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(0)
                    _LOGGER.info("[%s] Updated status to 0%% (OFF confirmed)", self._entry.data.get("address"))
                except Exception as e:  # Broad catch OK: user callback, log and continue
                    _LOGGER.error("[%s] Status callback error: %s", self._entry.data.get("address"), e)

        elif pct == 33:
            # 33%: Send 01 21 once
            _LOGGER.debug("[%s] Sending 33%% command (01 21)", self._entry.data.get("address"))
            await self._ble.write_handle_raw(CMD_ON_33, response=False, repeat=1, delay_ms=0)
            _LOGGER.info("[%s] 33%% command sent", self._entry.data.get("address"))

            # Wait briefly for confirmation
            await asyncio.sleep(CMD_CONFIRMATION_DELAY_MS / 1000)

            # Set expected level to filter stale notifications
            self._ble.set_expected_level(33)

            # Update status directly (heater doesn't send separate notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(33)
                    _LOGGER.info("[%s] Updated status to 33%% (command confirmed)", self._entry.data.get("address"))
                except Exception as e:  # Broad catch OK: user callback, log and continue
                    _LOGGER.error("[%s] Status callback error: %s", self._entry.data.get("address"), e)

        elif pct == 66:
            # 66%: Send 01 42 once
            _LOGGER.debug("[%s] Sending 66%% command (01 42)", self._entry.data.get("address"))
            await self._ble.write_handle_raw(CMD_ON_66, response=False, repeat=1, delay_ms=0)
            _LOGGER.info("[%s] 66%% command sent", self._entry.data.get("address"))

            # Wait briefly for confirmation
            await asyncio.sleep(CMD_CONFIRMATION_DELAY_MS / 1000)

            # Set expected level to filter stale notifications
            self._ble.set_expected_level(66)

            # Update status directly (heater doesn't send separate notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(66)
                    _LOGGER.info("[%s] Updated status to 66%% (command confirmed)", self._entry.data.get("address"))
                except Exception as e:  # Broad catch OK: user callback, log and continue
                    _LOGGER.error("[%s] Status callback error: %s", self._entry.data.get("address"), e)

        elif pct == 100:
            # 100%: Send 01 64 once
            _LOGGER.debug("[%s] Sending 100%% command (01 64)", self._entry.data.get("address"))
            await self._ble.write_handle_raw(CMD_ON_100, response=False, repeat=1, delay_ms=0)
            _LOGGER.info("[%s] 100%% command sent", self._entry.data.get("address"))

            # Wait briefly for confirmation
            await asyncio.sleep(CMD_CONFIRMATION_DELAY_MS / 1000)

            # Set expected level to filter stale notifications
            self._ble.set_expected_level(100)

            # Update status directly (heater doesn't send separate notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(100)
                    _LOGGER.info("[%s] Updated status to 100%% (command confirmed)", self._entry.data.get("address"))
                except Exception as e:  # Broad catch OK: user callback, log and continue
                    _LOGGER.error("[%s] Status callback error: %s", self._entry.data.get("address"), e)

    async def off(self) -> None:
        """
        Turn off the heater.

        This is a convenience method that calls set_level(0).
        """
        await self.set_level(0)

    # Service API (used by __init__.py services)
    async def write_handle_raw(self, data: bytes, response: bool=False,
                              repeat: int=1, delay_ms: int=100) -> None:
        """
        Direct handle writing for services.

        Used by solamagic.write_handle service.

        Args:
            data: Raw bytes to write to handle
            response: Whether to wait for response (default: False)
            repeat: Number of times to repeat command (default: 1)
            delay_ms: Delay between repeats in milliseconds (default: 100)
        """
        await self._ensure_initialized()
        await self._ble.write_handle_raw(data, response=response,
                                         repeat=repeat, delay_ms=delay_ms)

    async def write_handle_any(self, handle: int, data: bytes, response: bool=False,
                              repeat: int=1, delay_ms: int=100) -> None:
        """
        Write to arbitrary handle.

        Used by solamagic.write_handle_any service.

        Args:
            handle: GATT handle number (decimal)
            data: Raw bytes to write
            response: Whether to wait for response (default: False)
            repeat: Number of times to repeat command (default: 1)
            delay_ms: Delay between repeats in milliseconds (default: 100)
        """
        await self._ensure_initialized()
        await self._ble.write_handle_any(handle, data, response=response,
                                         repeat=repeat, delay_ms=delay_ms)

    async def write_uuid_raw(self, char_uuid: str, data: bytes,
                            response: bool=False) -> None:
        """
        Write via UUID.

        Used by solamagic.write_uuid service.

        Args:
            char_uuid: Characteristic UUID string
            data: Raw bytes to write
            response: Whether to wait for response (default: False)
        """
        await self._ensure_initialized()
        await self._ble.write_uuid_simple(char_uuid, data, response=response)

    async def disconnect(self) -> None:
        """
        Disconnect from device. Unlock runs again on the next connection.
        """
        await self._ble.disconnect()
