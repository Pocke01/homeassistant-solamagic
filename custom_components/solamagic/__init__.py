from __future__ import annotations
import logging, binascii
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType
from .const import DOMAIN, CONF_ADDRESS, CONF_COMMAND_CHAR, CONF_WRITE_MODE
from .client import SolamagicClient

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[str] = ["climate", "sensor"]  # Bara climate och sensor!

def _b(hexstr: str) -> bytes:
    s = hexstr.replace(" ", "").replace("-", "")
    return binascii.unhexlify(s)

# Service schemas
WRITE_HANDLE_SCHEMA = vol.Schema({
    vol.Required("entry_id"): str,
    vol.Required("payload_hex"): str,
    vol.Optional("response", default=False): bool,
    vol.Optional("repeat", default=2): vol.Coerce(int),
    vol.Optional("delay_ms", default=120): vol.Coerce(int),
})

WRITE_HANDLE_ANY_SCHEMA = vol.Schema({
    vol.Required("entry_id"): str,
    vol.Required("handle"): vol.Coerce(int),
    vol.Required("payload_hex"): str,
    vol.Optional("response", default=True): bool,
    vol.Optional("repeat", default=1): vol.Coerce(int),
    vol.Optional("delay_ms", default=100): vol.Coerce(int),
})

WRITE_UUID_SCHEMA = vol.Schema({
    vol.Required("entry_id"): str,
    vol.Required("char_uuid"): str,
    vol.Required("payload_hex"): str,
    vol.Optional("response", default=False): bool,
})

SET_LEVEL_SCHEMA = vol.Schema({
    vol.Required("entry_id"): str,
    vol.Required("level"): vol.Any(
        vol.In([0, 33, 66, 100]),
        vol.In(["0", "33", "66", "100"])
    ),
})

DISCONNECT_SCHEMA = vol.Schema({
    vol.Required("entry_id"): str,
})

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    address: str = entry.data["address"]

    # Använd handle-läge som standard (beprövat fungerande via proxy)
    write_mode = entry.options.get(
        CONF_WRITE_MODE,
        entry.data.get(CONF_WRITE_MODE, "handle")
    )

    cmd_char = entry.options.get(
        CONF_COMMAND_CHAR,
        entry.data.get(CONF_COMMAND_CHAR)
    )

    client = SolamagicClient(hass, address, write_mode, cmd_char)
    hass.data[DOMAIN][entry.entry_id] = client

    _LOGGER.info(
        "Setup Solamagic %s (entry_id=%s, write_mode=%s)",
        address, entry.entry_id, write_mode
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Service handlers
    async def _svc_write_handle(call: ServiceCall) -> None:
        c = hass.data[DOMAIN].get(call.data["entry_id"])
        if not c:
            _LOGGER.error("write_handle: no client for entry_id=%s",
                         call.data["entry_id"])
            return
        await c.write_handle_raw(
            _b(call.data["payload_hex"]),
            call.data["response"],
            call.data["repeat"],
            call.data["delay_ms"]
        )

    async def _svc_write_handle_any(call: ServiceCall) -> None:
        c = hass.data[DOMAIN].get(call.data["entry_id"])
        if not c:
            _LOGGER.error("write_handle_any: no client for entry_id=%s",
                         call.data["entry_id"])
            return
        await c.write_handle_any(
            call.data["handle"],
            _b(call.data["payload_hex"]),
            call.data["response"],
            call.data["repeat"],
            call.data["delay_ms"]
        )

    async def _svc_write_uuid(call: ServiceCall) -> None:
        c = hass.data[DOMAIN].get(call.data["entry_id"])
        if not c:
            _LOGGER.error("write_uuid: no client for entry_id=%s",
                         call.data["entry_id"])
            return
        await c.write_uuid_raw(
            call.data["char_uuid"],
            _b(call.data["payload_hex"]),
            call.data["response"]
        )

    async def _svc_set_level(call: ServiceCall) -> None:
        """
        Unified set_level service.
        Sets heater to 0%, 33%, 66%, or 100%.
        """
        c = hass.data[DOMAIN].get(call.data["entry_id"])
        if not c:
            _LOGGER.error("set_level: no client for entry_id=%s",
                         call.data["entry_id"])
            return
        lvl = call.data['level']
        if isinstance(lvl, str):
            lvl = int(lvl)
        await c.set_level(lvl)

    async def _svc_disconnect(call: ServiceCall) -> None:
        c = hass.data[DOMAIN].get(call.data["entry_id"])
        if not c:
            _LOGGER.error("disconnect: no client for entry_id=%s",
                         call.data["entry_id"])
            return
        await c.disconnect()

    # Registrera services (en gång per integration)
    if not hass.services.has_service(DOMAIN, "write_handle"):
        hass.services.async_register(
            DOMAIN, "write_handle", _svc_write_handle,
            schema=WRITE_HANDLE_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, "write_handle_any"):
        hass.services.async_register(
            DOMAIN, "write_handle_any", _svc_write_handle_any,
            schema=WRITE_HANDLE_ANY_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, "write_uuid"):
        hass.services.async_register(
            DOMAIN, "write_uuid", _svc_write_uuid,
            schema=WRITE_UUID_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, "set_level"):
        hass.services.async_register(
            DOMAIN, "set_level", _svc_set_level,
            schema=SET_LEVEL_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, "disconnect"):
        hass.services.async_register(
            DOMAIN, "disconnect", _svc_disconnect,
            schema=DISCONNECT_SCHEMA
        )

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client: SolamagicClient | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if client:
        await client.disconnect()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok