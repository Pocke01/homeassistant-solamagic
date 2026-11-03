from __future__ import annotations
import logging
import asyncio
import binascii
import time

from typing import Callable

from homeassistant.core import HomeAssistant

from .bluetooth import SolamagicBleClient
from .const import (
    CHAR_ALT_F002,
    CHAR_CMD_F001,
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

# How long to wait for the heater to echo a command back (seconds)
COMMAND_CONFIRM_TIMEOUT = 1.0

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
        self.last_command: float = 0.0  # time.monotonic() of the latest user command

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

    def _mark_command(self) -> None:
        """Remember that a command is (or was just) being sent, so polling stays out of the way."""
        self.last_command = time.monotonic()

    async def _ensure_initialized(self) -> None:
        """Make sure the link is connected and unlocked.

        The unlock (token write + notifications) is tied to the BLE connection
        itself, so a dropped link - including a heater losing power - can never
        leave a stale "initialised" flag behind.
        """
        await self._ble.ensure_ready()

    async def set_level(self, pct: int) -> bool:
        """
        Set heater level to 0/33/66/100%.

        Commands are 2-byte Write Commands (no response) to the command
        characteristic: 33% = 01 21, 66% = 01 42, 100% = 01 64, and OFF = 00 21
        repeated ~21 times with ~16 ms spacing (from the sniffer).

        The only acknowledgement is the heater echoing the command back as a
        notification. The state is therefore reported only once that echo has
        arrived; the echo is itself a [power, level] frame, so the BLE layer has
        already pushed the new level to the entities by then.

        Returns:
            True if the heater acknowledged the command, False if it stayed silent.

        Raises:
            ValueError: If pct is not one of the valid values (0, 33, 66, 100)
        """
        if pct not in (0, 33, 66, 100):
            raise ValueError("pct must be one of 0, 33, 66, 100")

        self._mark_command()
        # Connect and unlock (no-op when the link is already up and unlocked)
        await self._ensure_initialized()

        address = self._entry.data.get("address")
        _LOGGER.info("[%s] Setting heater to %d%%", address, pct)

        cmd, repeat = {
            0: (CMD_OFF, CMD_OFF_REPEAT_COUNT),
            33: (CMD_ON_33, 1),
            66: (CMD_ON_66, 1),
            100: (CMD_ON_100, 1),
        }[pct]

        # Arm the stale-notification filter before writing so the echo is recognised.
        self._ble.set_expected_level(pct)

        if await self._send_command_confirmed(cmd, repeat=repeat):
            _LOGGER.info("[%s] Heater acknowledged %d%%", address, pct)
            return True
        else:
            # Leave the state alone: reporting success is what makes a heater that
            # silently ignores commands look like it works.
            _LOGGER.warning(
                "[%s] Heater did not acknowledge %d%% (%s) - state left unchanged",
                address, pct, bytes(cmd).hex(),
            )
            return False

    async def _send_command_confirmed(
        self, cmd: bytes, *, repeat: int = 1, timeout: float | None = None
    ) -> bool:
        """
        Write ``cmd`` and wait for the heater to echo it back.

        The listener is installed before the first write because the echo can
        arrive within milliseconds. Returns True if the echo arrived in time.
        """
        expected = bytes(cmd)
        confirmed = asyncio.Event()

        def confirmation_checker(data: bytes) -> None:
            if data == expected:
                confirmed.set()

        old_callback = self._ble._confirmation_callback
        self._ble._confirmation_callback = confirmation_checker
        try:
            for i in range(repeat):
                await self._ble.write_handle_raw(cmd, response=False, repeat=1, delay_ms=0)
                if i + 1 < repeat:
                    await asyncio.sleep(CMD_OFF_DELAY_MS / 1000)
            try:
                await asyncio.wait_for(
                    confirmed.wait(), COMMAND_CONFIRM_TIMEOUT if timeout is None else timeout
                )
                return True
            except asyncio.TimeoutError:
                return False
        finally:
            self._ble._confirmation_callback = old_callback

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
        self._mark_command()
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
        self._mark_command()
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
        self._mark_command()
        await self._ensure_initialized()
        await self._ble.write_uuid_simple(char_uuid, data, response=response)

    async def disconnect(self) -> None:
        """
        Disconnect from device. Unlock runs again on the next connection.
        """
        await self._ble.disconnect()
