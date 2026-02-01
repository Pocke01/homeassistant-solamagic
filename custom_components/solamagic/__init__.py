"""The Solamagic integration."""
from __future__ import annotations
import binascii
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .client import SolamagicClient
from .const import (
    CONF_COMMAND_CHAR,
    CONF_INIT_TOKEN,
    CONF_WRITE_MODE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[str] = ["climate", "sensor"]

# Integration is configured via config entries only (UI setup)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

def _b(hexstr: str) -> bytes:
    """Convert hex string to bytes."""
    s = hexstr.replace(" ", "").replace("-", "")
    return binascii.unhexlify(s)


def _get_entry_id_from_call(
    hass: HomeAssistant, call: ServiceCall
) -> str | None:
    """
    Extract entry_id from service call.

    Supports both direct entry_id and device_id lookup.

    Args:
        hass: Home Assistant instance
        call: Service call containing either entry_id or device_id

    Returns:
        Config entry ID if found, None otherwise
    """
    # Direct entry_id provided
    entry_id = call.data.get("entry_id")
    if isinstance(entry_id, str):
        return entry_id

    # Device_id provided - look up entry_id
    device_id = call.data.get("device_id")
    if device_id:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(device_id)
        if device:
            # Find entry_id from device's config entries
            for entry_id in device.config_entries:
                if entry_id in hass.data.get(DOMAIN, {}):
                    return entry_id

    return None


# Service schemas with device picker support
WRITE_HANDLE_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Optional("device_id"): str,
    vol.Required("payload_hex"): str,
    vol.Optional("response", default=False): bool,
    vol.Optional("repeat", default=2): vol.Coerce(int),
    vol.Optional("delay_ms", default=120): vol.Coerce(int),
})

WRITE_HANDLE_ANY_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Optional("device_id"): str,
    vol.Required("handle"): vol.Coerce(int),
    vol.Required("payload_hex"): str,
    vol.Optional("response", default=True): bool,
    vol.Optional("repeat", default=1): vol.Coerce(int),
    vol.Optional("delay_ms", default=100): vol.Coerce(int),
})

WRITE_UUID_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Optional("device_id"): str,
    vol.Required("char_uuid"): str,
    vol.Required("payload_hex"): str,
    vol.Optional("response", default=False): bool,
})

SET_LEVEL_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Optional("device_id"): str,
    vol.Required("level"): vol.Any(
        vol.In([0, 33, 66, 100]),
        vol.In(["0", "33", "66", "100"])
    ),
})

DISCONNECT_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Optional("device_id"): str,
})

SCAN_INIT_HANDLES_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Optional("device_id"): str,
    vol.Optional("start_handle", default=15): vol.Coerce(int),
    vol.Optional("end_handle", default=40): vol.Coerce(int),
})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Solamagic component."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up Solamagic from a config entry."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    address: str = entry.data["address"]

    # Use handle mode as default (proven to work via proxy)
    write_mode = entry.options.get(
        CONF_WRITE_MODE,
        entry.data.get(CONF_WRITE_MODE, "handle")
    )

    cmd_char = entry.options.get(
        CONF_COMMAND_CHAR,
        entry.data.get(CONF_COMMAND_CHAR)
    )

    client = SolamagicClient(hass, entry, write_mode, cmd_char)
    hass.data[DOMAIN][entry.entry_id] = client

    _LOGGER.info("Setup Solamagic %s (entry_id=%s, write_mode=%s)", address, entry.entry_id, write_mode)

    # --- NEW: Log init-token at setup time (from handle 0x001F) and store  ---
    try:
        init_value = await client._ble.read_init_token()
        if init_value and any(init_value):
            hex_value = init_value.hex()
            _LOGGER.info("[%s] Init-token read during setup from handle 0x001F: %s", address, hex_value)

            # Spara i config entry om det inte redan är samma värde
            if entry.data.get(CONF_INIT_TOKEN) != hex_value:
                new_data = {**entry.data, CONF_INIT_TOKEN: hex_value}
                hass.config_entries.async_update_entry(entry, data=new_data)
                _LOGGER.info("[%s] Stored init-token in config entry: %s", address, hex_value)
    except Exception as err:
        _LOGGER.error("[%s] Failed to read/store init-token during setup: %s", address, err)
    # --- END NEW ---

    await hass.config_entries.async_forward_entry_setups(
        entry, PLATFORMS
    )

    # Service handlers
    async def _svc_write_handle(call: ServiceCall) -> None:
        """Handle write_handle service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        if not entry_id:
            _LOGGER.error(
                "write_handle service failed: No device specified. "
                "User must select a device or provide entry_id"
            )
            raise HomeAssistantError(
                "No device specified. Please select a device or provide entry_id."
            )

        client = hass.data[DOMAIN].get(entry_id)
        if not client:
            _LOGGER.error(
                "write_handle service failed: Device '%s' not found. "
                "Device may have been removed or is not configured",
                entry_id
            )
            raise HomeAssistantError(
                f"Device with entry_id '{entry_id}' not found. "
                "The device may have been removed or is not configured."
            )

        _LOGGER.debug(
            "write_handle service called: payload=%s, entry_id=%s",
            call.data["payload_hex"], entry_id
        )

        try:
            await client.write_handle_raw(
                _b(call.data["payload_hex"]),
                call.data["response"],
                call.data["repeat"],
                call.data["delay_ms"]
            )
        except Exception as err:
            _LOGGER.error(
                "write_handle service failed during Bluetooth operation: %s",
                err, exc_info=True
            )
            raise HomeAssistantError(
                f"Failed to write to device: {err}"
            ) from err

    async def _svc_write_handle_any(call: ServiceCall) -> None:
        """Handle write_handle_any service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        if not entry_id:
            _LOGGER.error(
                "write_handle_any service failed: No device specified. "
                "User must select a device or provide entry_id"
            )
            raise HomeAssistantError(
                "No device specified. Please select a device or provide entry_id."
            )

        client = hass.data[DOMAIN].get(entry_id)
        if not client:
            _LOGGER.error(
                "write_handle_any service failed: Device '%s' not found. "
                "Device may have been removed or is not configured",
                entry_id
            )
            raise HomeAssistantError(
                f"Device with entry_id '{entry_id}' not found. "
                "The device may have been removed or is not configured."
            )

        _LOGGER.debug(
            "write_handle_any service called: handle=0x%04X, payload=%s, entry_id=%s",
            call.data["handle"], call.data["payload_hex"], entry_id
        )

        try:
            await client.write_handle_any(
                call.data["handle"],
                _b(call.data["payload_hex"]),
                call.data["response"],
                call.data["repeat"],
                call.data["delay_ms"]
            )
        except Exception as err:
            _LOGGER.error(
                "write_handle_any service failed during Bluetooth operation: %s",
                err, exc_info=True
            )
            raise HomeAssistantError(
                f"Failed to write to device handle: {err}"
            ) from err

    async def _svc_write_uuid(call: ServiceCall) -> None:
        """Handle write_uuid service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        if not entry_id:
            _LOGGER.error(
                "write_uuid service failed: No device specified. "
                "User must select a device or provide entry_id"
            )
            raise HomeAssistantError(
                "No device specified. Please select a device or provide entry_id."
            )

        client = hass.data[DOMAIN].get(entry_id)
        if not client:
            _LOGGER.error(
                "write_uuid service failed: Device '%s' not found. "
                "Device may have been removed or is not configured",
                entry_id
            )
            raise HomeAssistantError(
                f"Device with entry_id '{entry_id}' not found."
            )

        _LOGGER.debug(
            "write_uuid service called: uuid=%s, payload=%s, entry_id=%s",
            call.data["char_uuid"], call.data["payload_hex"], entry_id
        )

        try:
            await client.write_uuid_raw(
                call.data["char_uuid"],
                _b(call.data["payload_hex"]),
                call.data["response"]
            )
        except Exception as err:
            _LOGGER.error(
                "write_uuid service failed during Bluetooth operation: %s",
                err, exc_info=True
            )
            raise HomeAssistantError(
                f"Failed to write to device UUID: {err}"
            ) from err

    async def _svc_set_level(call: ServiceCall) -> None:
        """
        Handle set_level service call.

        Sets heater to 0%, 33%, 66%, or 100%.
        """
        entry_id = _get_entry_id_from_call(hass, call)
        if not entry_id:
            _LOGGER.error(
                "set_level service failed: No device specified. "
                "User must select a device or provide entry_id"
            )
            raise HomeAssistantError(
                "No device specified. Please select a device or provide entry_id."
            )

        client = hass.data[DOMAIN].get(entry_id)
        if not client:
            _LOGGER.error(
                "set_level service failed: Device '%s' not found. "
                "Device may have been removed or is not configured",
                entry_id
            )
            raise HomeAssistantError(
                f"Device with entry_id '{entry_id}' not found."
            )

        lvl = call.data['level']
        if isinstance(lvl, str):
            lvl = int(lvl)

        _LOGGER.debug(
            "set_level service called: level=%d%%, entry_id=%s",
            lvl, entry_id
        )

        try:
            await client.set_level(lvl)
        except Exception as err:
            _LOGGER.error(
                "set_level service failed during Bluetooth operation: %s",
                err, exc_info=True
            )
            raise HomeAssistantError(
                f"Failed to set heater level: {err}"
            ) from err

    async def _svc_disconnect(call: ServiceCall) -> None:
        """Handle disconnect service call."""
        entry_id = _get_entry_id_from_call(hass, call)
        if not entry_id:
            _LOGGER.error(
                "disconnect service failed: No device specified. "
                "User must select a device or provide entry_id"
            )
            raise HomeAssistantError(
                "No device specified. Please select a device or provide entry_id."
            )

        client = hass.data[DOMAIN].get(entry_id)
        if not client:
            _LOGGER.error(
                "disconnect service failed: Device '%s' not found. "
                "Device may have been removed or is not configured",
                entry_id
            )
            raise HomeAssistantError(
                f"Device with entry_id '{entry_id}' not found."
            )

        _LOGGER.debug("disconnect service called: entry_id=%s", entry_id)

        try:
            await client.disconnect()
        except Exception as err:
            _LOGGER.error(
                "disconnect service failed: %s",
                err, exc_info=True
            )
            raise HomeAssistantError(
                f"Failed to disconnect from device: {err}"
            ) from err


    async def _svc_scan_init_handles(call: ServiceCall) -> None:
        """
        Diagnostic service to scan for init handle.

        Tries to read from handles in the specified range and logs results.
        This helps identify the correct init handle for different heater models.
        """
        entry_id = _get_entry_id_from_call(hass, call)
        if not entry_id:
            raise HomeAssistantError(
                "No device specified. Please select a device."
            )

        client = hass.data[DOMAIN].get(entry_id)
        if not client:
            raise HomeAssistantError(
                f"Device with entry_id '{entry_id}' not found."
            )

        start = call.data.get("start_handle", 15)
        end = call.data.get("end_handle", 40)

        _LOGGER.info(
            "[%s] 🔍 Starting init handle scan from %d to %d...",
            client._ble.address, start, end
        )

        try:
            # Ensure we're connected
            ble_client = await client._ble._ensure_connected()

            results = []

            for handle in range(start, end + 1):
                try:
                    _LOGGER.debug(
                        "[%s] Trying to read handle 0x%04X (%d)...",
                        client._ble.address, handle, handle
                    )

                    # Re-ensure connected before each read (poll may disconnect us)
                    ble_client = await client._ble._ensure_connected()

                    # Try to read this handle
                    value = await ble_client.read_gatt_char(handle)

                    if value and len(value) > 0:
                        hex_value = value.hex()
                        _LOGGER.info(
                            "[%s] ✅ Handle 0x%04X (%d) readable: %s (length: %d bytes)",
                            client._ble.address, handle, handle, hex_value, len(value)
                        )

                        results.append({
                            "handle": handle,
                            "hex": f"0x{handle:04X}",
                            "value": hex_value,
                            "length": len(value)
                        })

                        # If it looks like an init token (9 bytes starting with FF)
                        if len(value) == 9 and value[0] == 0xFF:
                            _LOGGER.warning(
                                "[%s] 🎯 POTENTIAL INIT TOKEN FOUND at handle 0x%04X (%d): %s",
                                client._ble.address, handle, handle, hex_value
                            )
                    else:
                        _LOGGER.debug(
                            "[%s] Handle 0x%04X (%d): Empty value",
                            client._ble.address, handle, handle
                        )

                except Exception as e:
                    # Expected for most handles - they won't be readable
                    _LOGGER.debug(
                        "[%s] Handle 0x%04X (%d): Not readable (%s)",
                        client._ble.address, handle, handle, str(e)
                    )

            # Summary
            if results:
                _LOGGER.warning(
                    "[%s] 📊 SCAN COMPLETE - Found %d readable handles:",
                    client._ble.address, len(results)
                )
                for r in results:
                    _LOGGER.warning(
                        "[%s]   - Handle %s (%d): %s (%d bytes)",
                        client._ble.address, r["hex"], r["handle"], r["value"], r["length"]
                    )
            else:
                _LOGGER.warning(
                    "[%s] 📊 SCAN COMPLETE - No readable handles found in range %d-%d",
                    client._ble.address, start, end
                )
                _LOGGER.warning(
                    "[%s] Try expanding the range or check if device is properly connected",
                    client._ble.address
                )

        except Exception as err:
            _LOGGER.error(
                "[%s] Scan failed: %s",
                client._ble.address, err, exc_info=True
            )
            raise HomeAssistantError(
                f"Failed to scan handles: {err}"
            ) from err



    # Register services (once per integration)
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

    if not hass.services.has_service(DOMAIN, "scan_init_handles"):
        hass.services.async_register(
            DOMAIN, "scan_init_handles", _svc_scan_init_handles,
            schema=SCAN_INIT_HANDLES_SCHEMA
        )

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Unload a config entry."""
    client: SolamagicClient | None = hass.data.get(
        DOMAIN, {}
    ).pop(entry.entry_id, None)

    if client:
        await client.disconnect()

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    return unload_ok