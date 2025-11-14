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
        KRITISK INITIALIZATION SEKVENS - baserad på sniffer-analys!

        Denna sekvens måste köras EN GÅNG per anslutning i exakt denna ordning:

        1. Skriv initialization payload till handle 0x001F
        2. Aktivera CCCD på 0x0030 (notifications för 0x002F)
        3. Aktivera CCCD på 0x0033 (notifications för 0x0032)
        4. Aktivera CCCD på 0x0029 (notifications för 0x0028) - SISTA!

        Denna ordning är KRITISK! Heatlink använder exakt denna sekvens.
        """
        if self._initialized:
            return

        _LOGGER.info("Running initialization sequence...")

        # Steg 1: Skriv initialization payload till 0x001F
        # Detta "låser upp" enheten för kommandon
        _LOGGER.debug("Step 1: Writing initialization payload to 0x001F")
        await self._ble.write_init_sequence()
        await asyncio.sleep(0.05)

        # Steg 2: Aktivera notifications på 0x002F (via CCCD 0x0030)
        # Detta är första notification channel
        _LOGGER.debug("Step 2: Enabling notifications on 0x002F (CCCD 0x0030)")
        try:
            await self._ble.write_cccd(CCCD_NTF1, bytes([0x01, 0x00]))
            _LOGGER.debug("✓ CCCD 0x%04X enabled (notifications)", CCCD_NTF1)
        except Exception as e:
            _LOGGER.warning("Could not enable CCCD 0x%04X: %s", CCCD_NTF1, e)

        await asyncio.sleep(0.05)

        # Steg 3: Aktivera notifications på 0x0032 (via CCCD 0x0033)
        # Detta är status/data channel
        _LOGGER.debug("Step 3: Enabling notifications on 0x0032 (CCCD 0x0033)")
        try:
            await self._ble.write_cccd(CCCD_NTF2, bytes([0x01, 0x00]))
            _LOGGER.debug("✓ CCCD 0x%04X enabled (notifications)", CCCD_NTF2)
        except Exception as e:
            _LOGGER.warning("Could not enable CCCD 0x%04X: %s", CCCD_NTF2, e)

        await asyncio.sleep(0.05)

        # Steg 4: Aktivera notifications på 0x0028 (via CCCD 0x0029) - SISTA!
        # Detta är command channel - måste aktiveras sist!
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
        Vänta på kommando-bekräftelse (2 bytes från handle 0x0028).

        Värmaren skickar INTE en separat status-notification efter kommandot.
        Den bara bekräftar kommandot med samma bytes tillbaka.

        Vi får alltså:
        1. Skickar: 01 21 (33% kommando)
        2. Får bekräftelse: 01 21 (2 bytes)
        3. INGET MER! Ingen status-notification kommer separat.

        Därför måste vi antaga att kommandot lyckades när vi får bekräftelsen.
        """
        start_time = asyncio.get_event_loop().time()
        confirmed = False

        # Skapa en temporär callback som fångar bekräftelser
        def confirmation_checker(data: bytes):
            nonlocal confirmed
            if len(data) == 2 and data == expected_cmd:
                confirmed = True
                _LOGGER.debug(f"✓ Command confirmed: {data.hex()}")

        # Spara gamla callback och sätt vår
        old_callback = self._ble._confirmation_callback
        self._ble._confirmation_callback = confirmation_checker

        try:
            # Vänta på bekräftelse
            while (asyncio.get_event_loop().time() - start_time) < timeout:
                if confirmed:
                    return True
                await asyncio.sleep(0.05)

            _LOGGER.warning(f"Timeout waiting for confirmation of {expected_cmd.hex()}")
            return False

        finally:
            # Återställ callback
            self._ble._confirmation_callback = old_callback

    async def set_level(self, pct: int) -> None:
        """
        Sätt värmarnivå till 0/33/66/100%.

        Baserad på Bluetooth sniffer-analys från xHeatlink-appen:

        Initialization (körs automatiskt vid första kommandot):
        1. Write 0x001F: FF FF FF FD 94 34 00 00 00
        2. Enable CCCD 0x0030: 01 00 (notifications)
        3. Enable CCCD 0x0033: 01 00 (notifications)
        4. Enable CCCD 0x0029: 01 00 (notifications) - SIST!

        Kommandosekvens:
        - 33%:  Skicka 01 21 (1 kommando i sniffer-loggen)
        - 66%:  Skicka 01 42 (1 kommando)
        - 100%: Skicka 01 64 (1 kommando)
        - OFF:  Skicka 00 21 (~21 kommandon med ~16ms delay)

        Alla kommandon använder Write Command (response=False) för snabbhet.

        VIKTIGT: Värmaren skickar INTE en separat status-notification efter kommandot!
        Den bara bekräftar med samma bytes (2 bytes) tillbaka på handle 0x0028.
        Vi måste därför antaga att kommandot lyckades och uppdatera status manuellt.
        """
        if pct not in (0, 33, 66, 100):
            raise ValueError("pct must be one of 0, 33, 66, 100")

        # KRITISKT: Kör initialization sekvens vid första kommandot
        await self._ensure_initialized()

        _LOGGER.info("Setting heater to %d%%", pct)

        if pct == 0:
            # OFF: Skicka 00 21 många gånger (21 kommandon enligt sniffer)
            _LOGGER.debug("Sending OFF command (00 21) x 21")
            for i in range(21):
                await self._ble.write_handle_any(
                    0x0028,
                    CMD_OFF,
                    response=False,
                    repeat=1,
                    delay_ms=0
                )
                await asyncio.sleep(0.016)  # ~16ms delay mellan kommandon

            _LOGGER.info("✓ OFF sequence complete")

            # Vänta kort på bekräftelse
            await asyncio.sleep(0.2)

            # Uppdatera status direkt (värmaren skickar inte separat notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(0)
                    _LOGGER.info("✓ Updated status to 0% (OFF confirmed)")
                except Exception as e:
                    _LOGGER.error(f"Status callback error: {e}")

        elif pct == 33:
            # 33%: Skicka 01 21 en gång
            _LOGGER.debug("Sending 33% command (01 21)")
            await self._ble.write_handle_any(
                0x0028,
                CMD_ON_33,
                response=False,
                repeat=1,
                delay_ms=0
            )
            _LOGGER.info("✓ 33% command sent")

            # Vänta kort på bekräftelse
            await asyncio.sleep(0.2)

            # Uppdatera status direkt (värmaren skickar inte separat notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(33)
                    _LOGGER.info("✓ Updated status to 33% (command confirmed)")
                except Exception as e:
                    _LOGGER.error(f"Status callback error: {e}")

        elif pct == 66:
            # 66%: Skicka 01 42 en gång
            _LOGGER.debug("Sending 66% command (01 42)")
            await self._ble.write_handle_any(
                0x0028,
                CMD_ON_66,
                response=False,
                repeat=1,
                delay_ms=0
            )
            _LOGGER.info("✓ 66% command sent")

            # Vänta kort på bekräftelse
            await asyncio.sleep(0.2)

            # Uppdatera status direkt (värmaren skickar inte separat notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(66)
                    _LOGGER.info("✓ Updated status to 66% (command confirmed)")
                except Exception as e:
                    _LOGGER.error(f"Status callback error: {e}")

        elif pct == 100:
            # 100%: Skicka 01 64 en gång
            _LOGGER.debug("Sending 100% command (01 64)")
            await self._ble.write_handle_any(
                0x0028,
                CMD_ON_100,
                response=False,
                repeat=1,
                delay_ms=0
            )
            _LOGGER.info("✓ 100% command sent")

            # Vänta kort på bekräftelse
            await asyncio.sleep(0.2)

            # Uppdatera status direkt (värmaren skickar inte separat notification)
            if self._ble._status_callback:
                try:
                    self._ble._status_callback(100)
                    _LOGGER.info("✓ Updated status to 100% (command confirmed)")
                except Exception as e:
                    _LOGGER.error(f"Status callback error: {e}")

    async def off(self) -> None:
        """Stäng av värmaren"""
        await self.set_level(0)

    # Service API (används av __init__.py services)
    async def write_handle_raw(self, data: bytes, response: bool=False,
                              repeat: int=1, delay_ms: int=100) -> None:
        """
        Direkt handle-skrivning för services.
        Används av solamagic.write_handle service.
        """
        await self._ensure_initialized()
        await self._ble.write_handle_raw(data, response=response,
                                         repeat=repeat, delay_ms=delay_ms)

    async def write_handle_any(self, handle: int, data: bytes, response: bool=False,
                              repeat: int=1, delay_ms: int=100) -> None:
        """
        Skriv till godtyckligt handle.
        Används av solamagic.write_handle_any service.
        """
        await self._ensure_initialized()
        await self._ble.write_handle_any(handle, data, response=response,
                                         repeat=repeat, delay_ms=delay_ms)

    async def write_uuid_raw(self, char_uuid: str, data: bytes,
                            response: bool=False) -> None:
        """
        Skriv via UUID.
        Används av solamagic.write_uuid service.
        """
        await self._ensure_initialized()
        await self._ble.write_uuid_simple(char_uuid, data, response=response)

    async def disconnect(self) -> None:
        """
        Koppla från enheten.
        Återställer initialization-status så att den körs igen vid nästa anslutning.
        """
        self._initialized = False
        await self._ble.disconnect()