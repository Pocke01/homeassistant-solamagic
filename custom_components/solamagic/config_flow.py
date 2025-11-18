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


def _format_device_name(address: str) -> str:
    """
    Create device name from MAC address.
    Same logic as get_device_info() in const.py.

    Args:
        address: MAC address (e.g., "D0:65:4C:8B:6C:36")

    Returns:
        Formatted name (e.g., "BT2000-8B6C36")
    """
    if address:
        # Take last 6 characters of MAC (e.g., "8B6C36")
        short_mac = address.replace(":", "")[-6:].upper()
        return f"BT2000-{short_mac}"
    return "Solamagic BT2000"


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solamagic."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """
        Handle Bluetooth discovery.

        Called when a Solamagic heater is discovered via Bluetooth.

        Args:
            discovery_info: Bluetooth discovery information

        Returns:
            Flow result leading to confirmation step
        """
        address = discovery_info.address
        rssi = getattr(discovery_info, "rssi", None)

        # Create nice name based on MAC
        device_name = _format_device_name(address)

        # Set unique ID to prevent duplicates
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        # Save discovery info for confirm step
        self._discovery_info = discovery_info

        # Set placeholders for UI
        self.context["title_placeholders"] = {
            "name": device_name,
            "address": address,
            "rssi": f"{rssi} dBm" if rssi else "Unknown",
        }

        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """
        Confirm setup of discovered device.

        Args:
            user_input: User confirmation input (None on first call)

        Returns:
            Flow result creating the config entry
        """
        assert self._discovery_info is not None

        placeholders = self.context.get("title_placeholders", {})

        if user_input is not None:
            # Create entry with discovered info
            # Use device name from placeholders (e.g., "BT2000-8B6C36")
            title = placeholders.get("name", "Solamagic BT2000")
            data = {
                CONF_ADDRESS: self._discovery_info.address,
                CONF_NAME: title,
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
        """
        Handle manual setup.

        Args:
            user_input: User-provided configuration (None on first call)

        Returns:
            Flow result either showing form or creating entry
        """
        errors = {}

        if user_input is not None:
            # Validate input
            address = user_input[CONF_ADDRESS].upper().strip()

            # Set unique ID
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            # Create entry
            # If user specified a name, use it; otherwise create from MAC
            if (
                user_input.get(CONF_NAME)
                and user_input.get(CONF_NAME) != "Solamagic"
            ):
                title = user_input.get(CONF_NAME)
            else:
                title = _format_device_name(address)

            data = {
                CONF_ADDRESS: address,
                CONF_NAME: title,
                CONF_COMMAND_CHAR: user_input.get(
                    CONF_COMMAND_CHAR, CHAR_CMD_F001
                ),
                CONF_DEFAULT_ON_LEVEL: user_input.get(
                    CONF_DEFAULT_ON_LEVEL, 100
                ),
                CONF_WRITE_MODE: user_input.get(CONF_WRITE_MODE, "handle"),
            }
            return self.async_create_entry(title=title, data=data)

        # Show manual config form
        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): str,
                vol.Optional(CONF_NAME, default="Solamagic"): str,
                vol.Optional(
                    CONF_COMMAND_CHAR, default=CHAR_CMD_F001
                ): str,
                vol.Optional(CONF_DEFAULT_ON_LEVEL, default=100): vol.In(
                    [33, 66, 100]
                ),
                vol.Optional(CONF_WRITE_MODE, default="handle"): vol.In(
                    ["handle", "uuid"]
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Solamagic."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """
        Initialize options flow.

        Args:
            config_entry: The config entry being modified
        """
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """
        Manage the options.

        Args:
            user_input: Updated options (None on first call)

        Returns:
            Flow result showing form or creating entry
        """
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Get current values
        data = self.config_entry.data
        options = self.config_entry.options

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_COMMAND_CHAR,
                    default=options.get(
                        CONF_COMMAND_CHAR,
                        data.get(CONF_COMMAND_CHAR, CHAR_CMD_F001),
                    ),
                ): str,
                vol.Optional(
                    CONF_DEFAULT_ON_LEVEL,
                    default=options.get(
                        CONF_DEFAULT_ON_LEVEL,
                        data.get(CONF_DEFAULT_ON_LEVEL, 100),
                    ),
                ): vol.In([33, 66, 100]),
                vol.Optional(
                    CONF_WRITE_MODE,
                    default=options.get(
                        CONF_WRITE_MODE, data.get(CONF_WRITE_MODE, "handle")
                    ),
                ): vol.In(["handle", "uuid"]),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )


async def async_get_options_flow(
    config_entry: config_entries.ConfigEntry,
) -> OptionsFlowHandler:
    """
    Get the options flow handler.

    Args:
        config_entry: The config entry to get options flow for

    Returns:
        Options flow handler instance
    """
    return OptionsFlowHandler(config_entry)