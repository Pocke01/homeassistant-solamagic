"""Climate entity for Solamagic 2000BT heaters."""
from __future__ import annotations
import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, get_device_info

_LOGGER = logging.getLogger(__name__)

# Preset modes (power levels)
PRESET_LOW = "low"  # 33%
PRESET_MEDIUM = "medium"  # 66%
PRESET_HIGH = "high"  # 100%

# Map presets to percentage
PRESET_TO_LEVEL = {
    PRESET_LOW: 33,
    PRESET_MEDIUM: 66,
    PRESET_HIGH: 100,
}

# Map percentage to presets
LEVEL_TO_PRESET = {
    33: PRESET_LOW,
    66: PRESET_MEDIUM,
    100: PRESET_HIGH,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solamagic climate entity from config entry."""
    client = hass.data[DOMAIN][entry.entry_id]
    name = entry.title or entry.data.get("address") or "Solamagic"
    async_add_entities([SolamagicClimate(client, name, entry.entry_id)], True)


class SolamagicClimate(ClimateEntity):
    """
    Representation of a Solamagic heater as a climate entity.

    Provides control via HVAC modes (OFF/HEAT) and preset modes (33%/66%/100%).
    """

    _attr_has_entity_name = True  # Use device name as base
    _attr_name = None  # None = use device name directly (no suffix)
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_preset_modes = [PRESET_LOW, PRESET_MEDIUM, PRESET_HIGH]
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, client, name: str, unique_id: str) -> None:
        """
        Initialize the climate entity.

        Args:
            client: SolamagicClient instance
            name: Entity name
            unique_id: Unique identifier for this entity
        """
        self._client = client
        # Don't set _attr_name - let it use device name from device_info
        self._attr_unique_id = f"{unique_id}-climate"
        self._address = getattr(client._ble, "address", None)
        self._entry_id = unique_id
        self._entry_title = name  # Save for device_info

        # Current state
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_preset_mode = PRESET_HIGH
        self._current_level = 0

        # Register callback for status updates from heater
        self._client._ble.set_status_callback(self._handle_status_update)

        _LOGGER.debug("[%s] Initialized Solamagic climate entity: %s (unique_id=%s)", self._address, self._entry_title, self._attr_unique_id)

    @property
    def device_info(self):
        """Return device information for device registry."""
        return get_device_info(self._address, self._entry_title)

    async def async_added_to_hass(self) -> None:
        """Set up listener for sensor state changes when added to hass."""
        await super().async_added_to_hass()

        # Listen to power sensor state changes
        # This ensures climate updates even after disconnect/reconnect
        sensor_entity_id = f"sensor.{self._entry_title.lower().replace(' ', '_').replace('-', '_')}_power_level"

        @callback
        def sensor_state_changed(event):
            """Handle sensor state change."""
            new_state = event.data.get("new_state")
            if new_state and new_state.state not in (None, "unknown", "unavailable"):
                try:
                    level = int(float(new_state.state))
                    _LOGGER.debug("[%s] Climate updating from sensor state change: %d%%", self._address, level)
                    self._handle_status_update(level)
                except (ValueError, TypeError) as e:
                    _LOGGER.debug("[%s] Could not parse sensor state: %s", self._address, e)

        # Track sensor state changes
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [sensor_entity_id],
                sensor_state_changed
            )
        )

        _LOGGER.debug("[%s] Climate entity now listening to sensor: %s", self._address, sensor_entity_id)

    @property
    def available(self) -> bool:
        """
        Return if entity is available.

        Entity is available if:
        - Currently connected to the device, OR
        - We have a last known state (not initial state)

        This allows the entity to remain available between connections
        while showing last known state.
        """
        # Check if actively connected
        try:
            if (hasattr(self._client._ble, '_client') and
                self._client._ble._client and
                self._client._ble._client.is_connected):
                return True
        except Exception:  # Broad catch OK: availability check, safe fallback
            pass

        # Available if we have any known state (not just initial 0)
        # This allows showing last state even when disconnected
        return hasattr(self, '_current_level')

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        attrs = {
            "power_level": self._current_level,
            "power_level_pct": f"{self._current_level}%",
        }

        # Add MAC address as attribute
        if self._address:
            attrs["mac_address"] = self._address

        return attrs

    @callback
    def _handle_status_update(self, level: int) -> None:
        """
        Handle status update from heater.

        Called when heater sends status notification or when we update
        status after sending a command.

        Args:
            level: Power level in percent (0, 33, 66, 100)
        """
        old_level = self._current_level
        self._current_level = level

        # Update HVAC mode
        old_mode = self._attr_hvac_mode
        self._attr_hvac_mode = HVACMode.OFF if level == 0 else HVACMode.HEAT

        # Update preset mode based on level
        if level > 0:
            self._attr_preset_mode = LEVEL_TO_PRESET.get(level, PRESET_HIGH)

        # Log and update state if something changed
        if old_level != level or old_mode != self._attr_hvac_mode:
            _LOGGER.info("[%s] Climate status updated: %d%% (mode=%s, preset=%s)", self._address, level, self._attr_hvac_mode, self._attr_preset_mode)
            # Only update state if entity is initialized
            if self.hass is not None:
                self.async_write_ha_state()
            else:
                _LOGGER.debug(
                    "Climate not fully initialized yet, "
                    "state will update on next poll"
                )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """
        Set new target HVAC mode.

        OFF -> Turn off heater (0%)
        HEAT -> Turn on with last preset (or HIGH if never used)

        Args:
            hvac_mode: Target HVAC mode (OFF or HEAT)
        """
        _LOGGER.debug("[%s] Setting HVAC mode to: %s", self._address, hvac_mode)

        if hvac_mode == HVACMode.OFF:
            # Turn off
            await self._client.set_level(0)
            self._current_level = 0
            self._attr_hvac_mode = HVACMode.OFF

        elif hvac_mode == HVACMode.HEAT:
            # Turn on with current preset
            level = PRESET_TO_LEVEL.get(self._attr_preset_mode, 100)
            await self._client.set_level(level)
            self._current_level = level
            self._attr_hvac_mode = HVACMode.HEAT

        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """
        Set new preset mode (low/medium/high).

        This automatically sets HVAC mode to HEAT and selects power level.

        Args:
            preset_mode: Target preset (low/medium/high)
        """
        if preset_mode not in PRESET_TO_LEVEL:
            _LOGGER.error("[%s] Invalid preset mode: %s", self._address, preset_mode)
            return

        level = PRESET_TO_LEVEL[preset_mode]
        _LOGGER.debug("[%s] Setting preset mode to: %s (%d%%)", self._address, preset_mode, level)

        await self._client.set_level(level)

        self._current_level = level
        self._attr_preset_mode = preset_mode
        self._attr_hvac_mode = HVACMode.HEAT

        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn the entity on (same as setting HEAT mode)."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn the entity off (same as setting OFF mode)."""
        await self.async_set_hvac_mode(HVACMode.OFF)