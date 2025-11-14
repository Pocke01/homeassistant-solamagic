"""Config flow for Solamagic integration."""
from __future__ import annotations
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .const import (
    DOMAIN,
    CONF_ADDRESS,
    CONF_NAME,
    CONF_COMMAND_CHAR,
    CONF_DEFAULT_ON_LEVEL,
    CONF_WRITE_MODE,
    CHAR_CMD_F001,
)

DEFAULTS = {
    CONF_COMMAND_CHAR: CHAR_CMD_F001,
    CONF_DEFAULT_ON_LEVEL: 100,
    CONF_WRITE_MODE: "handle",
}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solamagic."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle Bluetooth discovery."""
        address = discovery_info.address
        name = discovery_info.name or "Solamagic BT2000"
        rssi = getattr(discovery_info, "rssi", None)

        # Set unique ID to prevent duplicates
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        # Save discovery info for confirm step
        self._discovery_info = discovery_info

        # Set placeholders for UI
        self.context["title_placeholders"] = {
            "name": name,
            "address": address,
            "rssi": f"{rssi} dBm" if rssi else "Unknown",
        }

        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm setup of discovered device."""
        assert self._discovery_info is not None

        placeholders = self.context.get("title_placeholders", {})

        if user_input is not None:
            # Create entry with discovered info
            title = f"{placeholders.get('name', 'Solamagic')}"
            data = {
                CONF_ADDRESS: self._discovery_info.address,
                CONF_NAME: placeholders.get("name", "Solamagic"),
                **DEFAULTS,
            }
            return self.async_create_entry(title=title, data=data)

        # Show confirmation form
        return self.async_show_form(
            step_id="confirm",
            description_placeholders=placeholders,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual setup."""
        errors = {}

        if user_input is not None:
            # Validate input
            address = user_input[CONF_ADDRESS].upper().strip()

            # Set unique ID
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            # Create entry
            title = user_input.get(CONF_NAME) or f"Solamagic ({address[-8:]})"
            data = {
                CONF_ADDRESS: address,
                CONF_NAME: user_input.get(CONF_NAME, "Solamagic"),
                CONF_COMMAND_CHAR: user_input.get(CONF_COMMAND_CHAR, CHAR_CMD_F001),
                CONF_DEFAULT_ON_LEVEL: user_input.get(CONF_DEFAULT_ON_LEVEL, 100),
                CONF_WRITE_MODE: user_input.get(CONF_WRITE_MODE, "handle"),
            }
            return self.async_create_entry(title=title, data=data)

        # Show manual config form
        schema = vol.Schema({
            vol.Required(CONF_ADDRESS): str,
            vol.Optional(CONF_NAME, default="Solamagic"): str,
            vol.Optional(CONF_COMMAND_CHAR, default=CHAR_CMD_F001): str,
            vol.Optional(CONF_DEFAULT_ON_LEVEL, default=100): vol.In([33, 66, 100]),
            vol.Optional(CONF_WRITE_MODE, default="handle"): vol.In(["handle", "uuid"]),
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Solamagic."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Get current values
        data = self.config_entry.data
        options = self.config_entry.options

        schema = vol.Schema({
            vol.Optional(
                CONF_COMMAND_CHAR,
                default=options.get(
                    CONF_COMMAND_CHAR,
                    data.get(CONF_COMMAND_CHAR, CHAR_CMD_F001)
                )
            ): str,
            vol.Optional(
                CONF_DEFAULT_ON_LEVEL,
                default=options.get(
                    CONF_DEFAULT_ON_LEVEL,
                    data.get(CONF_DEFAULT_ON_LEVEL, 100)
                )
            ): vol.In([33, 66, 100]),
            vol.Optional(
                CONF_WRITE_MODE,
                default=options.get(
                    CONF_WRITE_MODE,
                    data.get(CONF_WRITE_MODE, "handle")
                )
            ): vol.In(["handle", "uuid"]),
        })

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )


async def async_get_options_flow(
    config_entry: config_entries.ConfigEntry
) -> OptionsFlowHandler:
    """Get the options flow handler."""
    return OptionsFlowHandler(config_entry)