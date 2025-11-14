from __future__ import annotations
import asyncio, logging, binascii
from typing import Optional, Any, Callable
from bleak_retry_connector import establish_connection, close_stale_connections, BleakClientWithServiceCache
from homeassistant.core import HomeAssistant, callback
from homeassistant.components import bluetooth
from homeassistant.exceptions import HomeAssistantError
from .const import HANDLE_CMD, HANDLE_NTF1, HANDLE_NTF2, HANDLE_INIT, INIT_PAYLOAD
_LOGGER = logging.getLogger(__name__)

# Konfigurerbar disconnect timeout (sekunder)
# Öka/minska efter behov - standard 3 minuter
DISCONNECT_TIMEOUT = 180  # 180 = 3 min, 300 = 5 min, 60 = 1 min

def _as_ha_error(err: Any, prefix: str) -> HomeAssistantError:
    try:
        msg = str(err)
    except Exception:
        msg = repr(err)
    return HomeAssistantError(f"{prefix}: {msg}")

def _hex(b: bytes) -> str:
    return binascii.hexlify(b).decode()

class SolamagicBleClient:
    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self.hass = hass
        self.address = address.upper()
        self._client: Optional[BleakClientWithServiceCache] = None
        self._lock = asyncio.Lock()
        self._status_callback: Optional[Callable[[int], None]] = None
        self._confirmation_callback: Optional[Callable[[bytes], None]] = None
        self._disconnect_timer: Optional[asyncio.TimerHandle] = None
        self._disconnect_timeout = DISCONNECT_TIMEOUT

    def set_status_callback(self, callback: Callable[[int], None]) -> None:
        """Registrera callback för status-uppdateringar"""
        self._status_callback = callback

    def _reset_disconnect_timer(self) -> None:
        """
        Återställ disconnect-timer.
        Anropas vid varje aktivitet för att förlänga anslutningen.
        """
        if self._disconnect_timer:
            self._disconnect_timer.cancel()

        if self._client and self._client.is_connected:
            _LOGGER.debug(
                "Resetting disconnect timer (%d seconds)",
                self._disconnect_timeout
            )
            self._disconnect_timer = self.hass.loop.call_later(
                self._disconnect_timeout,
                lambda: asyncio.create_task(self._auto_disconnect())
            )

    async def _auto_disconnect(self) -> None:
        """
        Automatisk disconnect efter inaktivitet.
        Detta frigör anslutningen för xHeatlink-appen.
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
        except Exception as err:
            raise _as_ha_error(err, "Bluetooth device lookup failed")

    async def _ensure_connected(self) -> BleakClientWithServiceCache:
        if self._client and self._client.is_connected:
            # Återställ timer vid återanvändning av befintlig anslutning
            self._reset_disconnect_timer()
            return self._client

        dev = await self._ble_device()

        try:
            await close_stale_connections(self.address)
        except Exception as err:
            _LOGGER.debug("close_stale_connections warning: %r", err)

        _LOGGER.info("Connecting to %s...", self.address)

        try:
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                dev,
                self.address,
                disconnected_callback=self._handle_disconnect
            )
        except Exception as err:
            raise _as_ha_error(err, "Bluetooth connect failed")

        _LOGGER.info("Connected to %s", self.address)

        # Paus efter anslutning
        await asyncio.sleep(0.3)

        # Starta notifications på alla handles
        for h in (HANDLE_CMD, HANDLE_NTF1, HANDLE_NTF2):
            try:
                await self._client.start_notify(h, self._notification_handler)
                _LOGGER.info(f"✓ Started notify on handle {h:#06x}")
            except Exception as e:
                _LOGGER.warning(f"Could not start notify on {h:#06x}: {e}")

        # Starta disconnect-timer
        self._reset_disconnect_timer()

        return self._client

    def _parse_status(self, data: bytes) -> Optional[int]:
        """
        Parse status från handle 0x0032 notifications.

        Status-data är 20 bytes, format:
        14 20 03 7E XX 00 00 00 00 00 00 00 00 00 [L1] 00 [P] [L2] 00 00
                                                    ^^     ^^  ^^
                                                  byte 14  16  17

        Nuvarande nivå finns på byte 15-16 (0-indexerat):
        - byte15=0x00, byte16=0x21 = OFF (power=0, level=33)
        - byte15=0x01, byte16=0x21 = 33% (power=1, level=33)
        - byte15=0x01, byte16=0x42 = 66% (power=1, level=66)
        - byte15=0x01, byte16=0x64 = 100% (power=1, level=100)

        Exempel från logg:
        OFF:  1420037ed6000000000000000021000021000000
                                        ^^^^^^^^
                                        byte 14-17: 00 00 21 00
                                             power=byte15=0x00

        66%:  1422037e60000000000000000021000142000000
                                        ^^^^^^^^
                                        byte 14-17: 00 01 42 00
                                             power=byte15=0x01, level=byte16=0x42
        """
        if len(data) < 17:
            return None

        # Byte 15-16 (0-indexerat) innehåller power och level
        power = data[15]
        level = data[16]

        _LOGGER.debug(f"Status bytes: power=0x{power:02x}, level=0x{level:02x}")

        # Mappa till procent
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
        Hantera notifications från enheten.

        Handle 0x0028: Kommando-bekräftelse (2 bytes) - samma som kommandot
        Handle 0x0032: Status-data (20 bytes, innehåller nuvarande nivå)
        Handle 0x002F: Status byte (3 bytes)

        VIKTIGT:
        - Kommando-bekräftelser (2 bytes) kommer OMEDELBART efter kommando
        - Status-data (20 bytes) kommer NÄR VÄRMAREN FAKTISKT ÄNDRAR NIVÅ

        Detta betyder att efter ett kommando får vi:
        1. Bekräftelse (2 bytes) - direkt
        2. Status-data (20 bytes) - SENARE när värmaren ändrat nivå

        Men i praktiken skickar värmaren inte alltid status-data separat!
        Därför måste vi uppdatera status baserat på bekräftelsen.
        """
        data_bytes = bytes(data)
        data_hex = _hex(data_bytes)
        data_len = len(data_bytes)

        # Återställ timer vid notification (indikerar aktivitet)
        self._reset_disconnect_timer()

        # Hantera olika notification-typer baserat på data-längd
        if data_len == 2:
            # Detta är kommando-bekräftelse från handle 0x0028
            _LOGGER.info(f"✓ Command confirmed: {data_hex}")

            # Notifiera confirmation callback om den finns
            if self._confirmation_callback:
                try:
                    self._confirmation_callback(data_bytes)
                except Exception as e:
                    _LOGGER.error(f"Confirmation callback error: {e}")

        elif data_len >= 15:
            # Detta är status från handle 0x0032
            _LOGGER.debug(f"📊 Status notification ({data_len} bytes): {data_hex}")

            # Parse status och notifiera callback
            level = self._parse_status(data_bytes)
            if level is not None:
                _LOGGER.info(f"📡 Heater status from notification: {level}%")
                if self._status_callback:
                    try:
                        self._status_callback(level)
                    except Exception as e:
                        _LOGGER.error(f"Status callback error: {e}")
            else:
                _LOGGER.debug(f"Could not parse level from status data")

        elif data_len == 3:
            # Detta är från handle 0x002F (status byte)
            _LOGGER.debug(f"📡 Status byte from 0x002F: {data_hex}")

        else:
            # Andra notifications
            _LOGGER.debug(f"📡 Notification ({data_len} bytes): {data_hex}")

    @callback
    def _handle_disconnect(self, client: BleakClientWithServiceCache) -> None:
        _LOGGER.info("Disconnected from %s", self.address)
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None
        self._client = None

    async def write_cccd(self, handle: int, value: bytes) -> None:
        """
        Skriv till CCCD (Client Characteristic Configuration Descriptor).
        """
        async with self._lock:
            client = await self._ensure_connected()

            _LOGGER.debug(f"Writing CCCD handle={handle:#06x}: {_hex(value)}")

            try:
                await client.write_gatt_descriptor(handle, value)
                _LOGGER.debug(f"CCCD write successful (descriptor method)")
            except Exception as e1:
                _LOGGER.debug(f"Descriptor write failed: {e1}, trying char method...")
                try:
                    await client.write_gatt_char(handle, value, response=True)
                    _LOGGER.debug(f"CCCD write successful (char method)")
                except Exception as e2:
                    _LOGGER.warning(f"Both CCCD write methods failed: desc={e1}, char={e2}")
                    pass

    async def write_init_sequence(self) -> None:
        """
        Skriv initialization sekvensen till handle 0x001F.
        """
        async with self._lock:
            client = await self._ensure_connected()

            _LOGGER.info("Writing initialization sequence to handle 0x%04X", HANDLE_INIT)
            _LOGGER.debug("Init payload: %s", _hex(INIT_PAYLOAD))

            try:
                await client.write_gatt_char(HANDLE_INIT, INIT_PAYLOAD, response=True)
                _LOGGER.info("✓ Initialization sequence successful")
                await asyncio.sleep(0.1)
            except Exception as e:
                _LOGGER.error("Failed to write initialization sequence: %s", e)
                raise _as_ha_error(e, "Initialization failed")

    async def write_handle_raw(self, data: bytes, response: bool=False,
                              repeat: int=1, delay_ms: int=100) -> None:
        """
        Skriv till handle 0x0028 (command characteristic).
        """
        async with self._lock:
            client = await self._ensure_connected()

            for i in range(max(1, repeat)):
                _LOGGER.debug(
                    "Write #%d to handle 0x%04X, resp=%s: %s",
                    i+1, HANDLE_CMD, response, _hex(data)
                )

                try:
                    await client.write_gatt_char(HANDLE_CMD, data, response=response)
                except Exception as e:
                    _LOGGER.error(f"Write failed on attempt {i+1}: {e}")
                    if i == 0:
                        raise

                if i+1 < repeat:
                    await asyncio.sleep(max(0, delay_ms)/1000)

    async def write_handle_any(self, handle: int, data: bytes,
                              response: bool=True, repeat: int=1,
                              delay_ms: int=100) -> None:
        """Skriv till godtyckligt handle"""
        async with self._lock:
            client = await self._ensure_connected()

            for i in range(max(1, repeat)):
                _LOGGER.debug(
                    "Write #%d to handle 0x%04X, resp=%s: %s",
                    i+1, handle, response, _hex(data)
                )

                try:
                    await client.write_gatt_char(handle, data, response=response)
                except Exception as e:
                    _LOGGER.error(f"Write to handle {handle:#06x} failed: {e}")
                    if i == 0:
                        raise

                if i+1 < repeat:
                    await asyncio.sleep(max(0, delay_ms)/1000)

            # Återställ timer efter skrivning
            self._reset_disconnect_timer()

    async def write_uuid_simple(self, char_uuid: str, data: bytes,
                               response: bool = False) -> None:
        """Skriv till characteristic via UUID"""
        async with self._lock:
            client = await self._ensure_connected()

            _LOGGER.debug(
                "Write to UUID %s, resp=%s: %s",
                char_uuid, response, _hex(data)
            )

            try:
                await client.write_gatt_char(char_uuid, data, response=response)
                _LOGGER.debug("UUID write successful")
            except Exception as err:
                _LOGGER.error("Failed to write to UUID %s: %r", char_uuid, err)
                raise _as_ha_error(err, "Bluetooth UUID write failed")

    async def disconnect(self) -> None:
        async with self._lock:
            if self._disconnect_timer:
                self._disconnect_timer.cancel()
                self._disconnect_timer = None

            if self._client and self._client.is_connected:
                try:
                    _LOGGER.info("Disconnecting from %s", self.address)
                    await self._client.disconnect()
                except Exception as e:
                    _LOGGER.debug("Disconnect error: %r", e)
            self._client = None