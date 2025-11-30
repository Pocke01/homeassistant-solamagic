from __future__ import annotations
import asyncio
import binascii
import logging
from typing import Any, Callable

from bleak import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    close_stale_connections,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    HANDLE_CMD,
    HANDLE_INIT,
    HANDLE_NTF1,
    HANDLE_NTF2,
    INIT_PAYLOAD,
    STATUS_LEVEL_BYTE,
    STATUS_MIN_LENGTH,
    STATUS_POWER_BYTE,
)
_LOGGER = logging.getLogger(__name__)

# Configurable disconnect timeout (seconds)
# Increase/decrease as needed - default 3 minutes
DISCONNECT_TIMEOUT = 180  # 180 = 3 min, 300 = 5 min, 60 = 1 min

def _as_ha_error(err: Any, prefix: str) -> HomeAssistantError:
    try:
        msg = str(err)
    except Exception:  # Catch-all needed: str() can fail on malformed errors
        msg = repr(err)
    return HomeAssistantError(f"{prefix}: {msg}")

def _hex(b: bytes) -> str:
    return binascii.hexlify(b).decode("utf-8")

class SolamagicBleClient:
    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self.hass = hass
        self.address = address.upper()
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._status_callback: Callable[[int], None] | None = None
        self._confirmation_callback: Callable[[bytes], None] | None = None
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._disconnect_timeout = DISCONNECT_TIMEOUT
        self._expected_level: int | None = None  # Expected level after command
        self._expected_level_time: float = 0  # When we set expected level

    def set_expected_level(self, level: int) -> None:
        """
        Set expected level after sending command.

        This helps filter out stale status notifications that arrive
        after we've already updated to the commanded level.

        Args:
            level: Expected power level in percent (0, 33, 66, or 100)
        """
        import time
        self._expected_level = level
        self._expected_level_time = time.time()
        _LOGGER.debug("[%s] Set expected level: %d%% (will ignore different values for 1 second)", self.address, level)

    def set_status_callback(self, callback: Callable[[int], None]) -> None:
        """
        Register callback for status updates.

        Args:
            callback: Function that accepts power level (int) as argument
        """
        self._status_callback = callback

    def _schedule_auto_disconnect(self) -> None:
        """
        Schedule auto-disconnect timer.

        IMPORTANT: This method is called OUTSIDE the lock to avoid deadlock.
        The timer callback may need to acquire the lock, so we must not
        hold the lock when creating the timer.
        """
        if self._disconnect_timer:
            self._disconnect_timer.cancel()

        if self._client and self._client.is_connected:
            _LOGGER.debug(
                "Scheduling disconnect timer (%d seconds)",
                self._disconnect_timeout
            )
            # FIX: Use proper method reference instead of lambda
            # This avoids task leak and makes cleanup easier
            self._disconnect_timer = self.hass.loop.call_later(
                self._disconnect_timeout,
                self._auto_disconnect_callback
            )

    def _auto_disconnect_callback(self) -> None:
        """
        Callback for auto-disconnect timer.

        This method is called by the event loop and creates a task
        for the actual disconnect operation.
        """
        self.hass.async_create_task(self._auto_disconnect())

    async def _auto_disconnect(self) -> None:
        """
        Automatic disconnect after inactivity.
        This releases the connection so the xHeatlink app can connect.
        """
        _LOGGER.info(
            "Auto-disconnecting after %d seconds of inactivity (allows app access)",
            self._disconnect_timeout
        )
        await self.disconnect()

    async def _ble_device(self):
        try:
            dev = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if not dev:
                raise HomeAssistantError(
                    f"BLE device not found or not connectable: {self.address}"
                )
            return dev
        except (BleakError, TimeoutError) as err:
            raise _as_ha_error(err, "Bluetooth device lookup failed")

    async def _ensure_connected(self) -> BleakClientWithServiceCache:
        if self._client and self._client.is_connected:
            # Reset timer when reusing existing connection
            # FIX: Schedule timer OUTSIDE lock context
            self._schedule_auto_disconnect()
            return self._client

        dev = await self._ble_device()

        try:
            await close_stale_connections(self.address)
        except Exception as err:  # Broad catch OK: cleanup operation, log and continue
            _LOGGER.debug("[%s] close_stale_connections warning: %s", self.address, err)

        _LOGGER.info("Connecting to %s...", self.address)

        try:
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                dev,
                self.address,
                disconnected_callback=self._handle_disconnect
            )
        except (BleakError, TimeoutError, OSError) as err:
            raise _as_ha_error(err, "Bluetooth connect failed")

        _LOGGER.info("Connected to %s", self.address)

        # Short pause after connection
        await asyncio.sleep(0.3)

        # Start notifications on all handles
        for h in (HANDLE_CMD, HANDLE_NTF1, HANDLE_NTF2):
            try:
                await self._client.start_notify(h, self._notification_handler)
                _LOGGER.info("[%s] Started notify on handle %#06x", self.address, h)
            except (BleakError, AttributeError) as e:
                _LOGGER.warning("[%s] Could not start notify on %#06x: %s", self.address, h, e)

        # FIX: Schedule timer OUTSIDE lock context (after connection established)
        self._schedule_auto_disconnect()

        return self._client

    def _parse_status(self, data: bytes) -> int | None:
        """
        Parse status from handle 0x0032 notifications.

        Status data is 20 bytes, format:
        14 20 03 7E XX 00 00 00 00 00 00 00 00 00 [L1] 00 [P] [L2] 00 00
                                                    ^^     ^^  ^^
                                                  byte 14  16  17

        Current level is in bytes 15-16 (0-indexed):
        - byte15=0x00, byte16=0x21 = OFF (power=0, level=33)
        - byte15=0x01, byte16=0x21 = 33% (power=1, level=33)
        - byte15=0x01, byte16=0x42 = 66% (power=1, level=66)
        - byte15=0x01, byte16=0x64 = 100% (power=1, level=100)
        """
        if len(data) < STATUS_MIN_LENGTH:
            return None

        # Use constants instead of magic numbers
        power = data[STATUS_POWER_BYTE]
        level = data[STATUS_LEVEL_BYTE]

        _LOGGER.debug("[%s] Status bytes: power=%#04x, level=%#04x", self.address, power, level)

        # Map to percentage
        if power == 0x00:
            return 0  # OFF
        elif power == 0x01:
            if level == 0x21:  # 33 decimal
                return 33
            elif level == 0x42:  # 66 decimal
                return 66
            elif level == 0x64:  # 100 decimal
                return 100

        return None

    def _notification_handler(self, sender, data: bytearray) -> None:
        """
        Handle notifications from the device.

        Handle 0x0028: Command confirmation (2 bytes) - same as command
        Handle 0x0032: Status data (20 bytes, contains current level)
        Handle 0x002F: Status byte (3 bytes)

        IMPORTANT:
        - Command confirmations (2 bytes) come IMMEDIATELY after command
        - Status data (20 bytes) comes WHEN HEATER ACTUALLY CHANGES LEVEL

        This means after a command we get:
        1. Confirmation (2 bytes) - immediately
        2. Status data (20 bytes) - LATER when heater changes level

        But in practice, the heater doesn't always send status data separately!
        Therefore we must update status based on the confirmation.
        """
        data_bytes = bytes(data)
        data_hex = _hex(data_bytes)
        data_len = len(data_bytes)

        # FIX: Schedule timer reset OUTSIDE lock context
        # We're not in a lock here, so this is safe
        self._schedule_auto_disconnect()

        # Handle different notification types based on data length
        if data_len == 2:
            # This is command confirmation from handle 0x0028
            _LOGGER.info("[%s] Command confirmed: %s", self.address, data_hex)

            # Notify confirmation callback if exists
            if self._confirmation_callback:
                try:
                    self._confirmation_callback(data_bytes)
                except Exception as e:  # Broad catch OK: user callback, log and continue
                    _LOGGER.error("[%s] Confirmation callback error: %s", self.address, e)

        elif data_len >= 15:
            # This is status from handle 0x0032
            _LOGGER.debug("[%s] Status notification (%d bytes): %s", self.address, data_len, data_hex)

            # Parse status and notify callback
            level = self._parse_status(data_bytes)
            if level is not None:
                # Check if we should ignore this notification
                # (it might be stale if we just sent a command)
                import time
                time_since_expected = time.time() - self._expected_level_time

                if (self._expected_level is not None and
                    time_since_expected < 1.0 and
                    level != self._expected_level):
                    _LOGGER.debug(
                        "Ignoring stale notification: %d%% "
                        "(expected %d%%, sent %.1fs ago)",
                        level, self._expected_level, time_since_expected
                    )
                    return  # Ignore this stale notification

                _LOGGER.info("[%s] Heater status from notification: %d%%", self.address, level)

                # Clear expected level if this matches or enough time passed
                if level == self._expected_level or time_since_expected >= 1.0:
                    self._expected_level = None

                if self._status_callback:
                    try:
                        self._status_callback(level)
                    except Exception as e:  # Broad catch OK: user callback, log and continue
                        _LOGGER.error("[%s] Status callback error: %s", self.address, e)
            else:
                _LOGGER.debug("[%s] Could not parse level from status data", self.address)

        elif data_len == 3:
            # This is from handle 0x002F (status byte)
            _LOGGER.debug("[%s] Status byte from 0x002F: %s", self.address, data_hex)

        else:
            # Other notifications
            _LOGGER.debug("[%s] Notification (%d bytes): %s", self.address, data_len, data_hex)

    @callback
    def _handle_disconnect(self, client: BleakClientWithServiceCache) -> None:
        _LOGGER.info("Disconnected from %s", self.address)
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None
        self._client = None

    async def write_cccd(self, handle: int, value: bytes) -> None:
        """
        Write to CCCD (Client Characteristic Configuration Descriptor).

        Args:
            handle: CCCD handle number (decimal)
            value: Bytes to write (typically 0x0100 to enable notifications)
        """
        async with self._lock:
            client = await self._ensure_connected()

            _LOGGER.debug("[%s] Writing CCCD handle=%#06x: %s", self.address, handle, _hex(value))

            try:
                await client.write_gatt_descriptor(handle, value)
                _LOGGER.debug("[%s] CCCD write successful (descriptor method)", self.address)
            except (BleakError, AttributeError) as e1:
                _LOGGER.debug("[%s] Descriptor write failed: %s, trying char method...", self.address, e1)
                try:
                    await client.write_gatt_char(handle, value, response=True)
                    _LOGGER.debug("[%s] CCCD write successful (char method)", self.address)
                except (BleakError, AttributeError) as e2:
                    # FIX: Log warning instead of silent pass
                    _LOGGER.warning(
                        "Both CCCD write methods failed for handle %#06x: desc=%s, char=%s",
                        handle, e1, e2
                    )
                    # Don't raise - allow initialization to continue with other CCCDs

    async def write_init_sequence(self, fallback_init: bytes | None = None) -> bytes:
        """
        Write init sequence to handle 0x001F.

        Flow:
        1. Read current value from HANDLE_INIT (0x001F)
        2. If value is all zeroes and we have fallback_init → use fallback
        3. Otherwise → use the value we read
        4. Write back the selected value
        5. Return the bytes that were actually written
        """
        async with self._lock:
            client = await self._ensure_connected()

            # 1. Read current init from device
            try:
                read_value: bytes = await client.read_gatt_char(HANDLE_INIT)
                _LOGGER.info(
                    "[%s] Read init value from handle 0x%04X: %s",
                    self.address,
                    HANDLE_INIT,
                    _hex(read_value),
                )
            except (BleakError, AttributeError) as e:
                _LOGGER.error("[%s] Failed to read init value: %s", self.address, e)
                read_value = b""

            all_zero = read_value and all(b == 0x00 for b in read_value)

            # 2-3. Choose which value we should actually write
            if all_zero and fallback_init:
                init_value = fallback_init
                _LOGGER.warning(
                    "[%s] Init value from device is all zeroes, using stored fallback: %s",
                    self.address,
                    _hex(init_value),
                )
            elif read_value:
                init_value = read_value
                _LOGGER.info(
                    "[%s] Echoing init value back to handle 0x%04X: %s",
                    self.address,
                    HANDLE_INIT,
                    _hex(init_value),
                )
            elif fallback_init and read_value != fallback_init:
                _LOGGER.info(
                    "[%s] Device init token changed (%s). Overriding with stored token: %s",
                    self.address,
                    _hex(read_value),
                    _hex(fallback_init),
                )
                init_value = fallback_init
            else:
                # Last resort: use static INIT_PAYLOAD
                init_value = INIT_PAYLOAD
                _LOGGER.warning(
                    "[%s] No init value read; falling back to static INIT_PAYLOAD: %s",
                    self.address,
                    _hex(init_value),
                )

            # 4. Write the init value
            _LOGGER.info(
                "[%s] Writing initialization sequence to handle 0x%04X",
                self.address,
                HANDLE_INIT,
            )
            _LOGGER.debug("[%s] Init payload: %s", self.address, _hex(init_value))

            try:
                await client.write_gatt_char(
                    HANDLE_INIT, init_value, response=True
                )
                _LOGGER.info("[%s] Initialization sequence successful", self.address)
                await asyncio.sleep(0.1)
            except (BleakError, AttributeError) as e:
                _LOGGER.error("[%s] Failed to write initialization sequence: %s", self.address, e)
                raise _as_ha_error("Init failed", e)

            # 5. Return what we used
            return init_value

    async def write_handle_raw(self, data: bytes, response: bool=False,
                              repeat: int=1, delay_ms: int=100) -> None:
        """
        Write to handle 0x0028 (command characteristic).

        Args:
            data: Raw bytes to write
            response: Whether to wait for response (default: False)
            repeat: Number of times to repeat command (default: 1)
            delay_ms: Delay between repeats in milliseconds (default: 100)
        """
        async with self._lock:
            client = await self._ensure_connected()

            for i in range(max(1, repeat)):
                _LOGGER.debug(
                    "Write #%d to handle %#06x, resp=%s: %s",
                    i+1, HANDLE_CMD, response, _hex(data)
                )

                try:
                    await client.write_gatt_char(HANDLE_CMD, data, response=response)
                except (BleakError, AttributeError) as e:
                    _LOGGER.error("[%s] Write failed on attempt %d: %s", self.address, i+1, e)
                    if i == 0:
                        raise

                if i+1 < repeat:
                    await asyncio.sleep(max(0, delay_ms)/1000)

    async def write_handle_any(self, handle: int, data: bytes,
                              response: bool=True, repeat: int=1,
                              delay_ms: int=100) -> None:
        """
        Write to arbitrary handle.

        Args:
            handle: GATT handle number (decimal)
            data: Raw bytes to write
            response: Whether to wait for response (default: True)
            repeat: Number of times to repeat command (default: 1)
            delay_ms: Delay between repeats in milliseconds (default: 100)
        """
        async with self._lock:
            client = await self._ensure_connected()

            for i in range(max(1, repeat)):
                _LOGGER.debug(
                    "Write #%d to handle %#06x, resp=%s: %s",
                    i+1, handle, response, _hex(data)
                )

                try:
                    await client.write_gatt_char(handle, data, response=response)
                except (BleakError, AttributeError) as e:
                    _LOGGER.error("[%s] Write to handle %#06x failed: %s", self.address, handle, e)
                    if i == 0:
                        raise

                if i+1 < repeat:
                    await asyncio.sleep(max(0, delay_ms)/1000)

        # FIX: Schedule timer AFTER releasing lock
        self._schedule_auto_disconnect()

    async def write_uuid_simple(self, char_uuid: str, data: bytes,
                               response: bool = False) -> None:
        """
        Write to characteristic via UUID.

        Args:
            char_uuid: Characteristic UUID string
            data: Raw bytes to write
            response: Whether to wait for response (default: False)
        """
        async with self._lock:
            client = await self._ensure_connected()

            _LOGGER.debug(
                "Write to UUID %s, resp=%s: %s",
                char_uuid, response, _hex(data)
            )

            try:
                await client.write_gatt_char(char_uuid, data, response=response)
                _LOGGER.debug("[%s] UUID write successful", self.address)
            except (BleakError, AttributeError, ValueError) as err:
                _LOGGER.error("[%s] Failed to write to UUID %s: %r", self.address, char_uuid, err)
                raise _as_ha_error(err, "Bluetooth UUID write failed")

    async def disconnect(self) -> None:
        """
        Disconnect from the Bluetooth device.

        Cancels any pending auto-disconnect timer and cleanly closes
        the Bluetooth connection.
        """
        async with self._lock:
            if self._disconnect_timer:
                self._disconnect_timer.cancel()
                self._disconnect_timer = None

            if self._client and self._client.is_connected:
                try:
                    _LOGGER.info("Disconnecting from %s", self.address)
                    await self._client.disconnect()
                except Exception as e:  # Broad catch OK: disconnect cleanup, log and continue
                    _LOGGER.debug("[%s] Disconnect error: %r", self.address, e)
            self._client = None