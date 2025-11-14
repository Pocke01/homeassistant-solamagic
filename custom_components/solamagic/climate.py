from __future__ import annotations
import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Preset modes (effektnivåer)
PRESET_LOW = "low"      # 33%
PRESET_MEDIUM = "medium"  # 66%
PRESET_HIGH = "high"    # 100%

# Mappa presets till procent
PRESET_TO_LEVEL = {
    PRESET_LOW: 33,
    PRESET_MEDIUM: 66,
    PRESET_HIGH: 100,
}

# Mappa procent till presets
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
    """Set up Solamagic climate entity."""
    client = hass.data[DOMAIN][entry.entry_id]
    name = entry.title or entry.data.get("address") or "Solamagic"
    async_add_entities([SolamagicClimate(client, name, entry.entry_id)], True)


class SolamagicClimate(ClimateEntity):
    """Representation of a Solamagic heater as a climate entity."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.PRESET_MODE
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_preset_modes = [PRESET_LOW, PRESET_MEDIUM, PRESET_HIGH]
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, client, name: str, unique_id: str) -> None:
        """Initialize the climate entity."""
        self._client = client
        self._attr_name = name
        self._attr_unique_id = f"{unique_id}-climate"
        self._address = getattr(client._ble, "address", None)

        # Current state
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_preset_mode = PRESET_HIGH
        self._current_level = 0

        # Registrera callback för status-uppdateringar från värmaren
        self._client._ble.set_status_callback(self._handle_status_update)

        _LOGGER.debug(
            "Initialized Solamagic climate entity: %s (unique_id=%s)",
            name, self._attr_unique_id
        )

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._address or self.unique_id)},
            "manufacturer": "Solamagic",
            "name": self._attr_name,
            "model": "BT2000",
            "suggested_area": "Outdoor",
        }

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        return {
            "power_level": self._current_level,
            "power_level_pct": f"{self._current_level}%",
        }

    @callback
    def _handle_status_update(self, level: int) -> None:
        """
        Hantera status-uppdatering från värmaren.
        Anropas när värmaren skickar status-notification.

        Args:
            level: Effektnivå i procent (0, 33, 66, 100)
        """
        old_level = self._current_level
        self._current_level = level

        # Uppdatera HVAC mode
        old_mode = self._attr_hvac_mode
        self._attr_hvac_mode = HVACMode.OFF if level == 0 else HVACMode.HEAT

        # Uppdatera preset mode baserat på nivå
        if level > 0:
            self._attr_preset_mode = LEVEL_TO_PRESET.get(level, PRESET_HIGH)

        # Logga och uppdatera state om något har ändrats
        if old_level != level or old_mode != self._attr_hvac_mode:
            _LOGGER.info(
                "Climate status updated: %s%% (mode=%s, preset=%s)",
                level, self._attr_hvac_mode, self._attr_preset_mode
            )
            # Uppdatera state bara om entity är initialiserad
            if self.hass is not None:
                self.async_write_ha_state()
            else:
                _LOGGER.debug("Climate not fully initialized yet, state will update on next poll")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """
        Set new target hvac mode.

        OFF -> Stäng av värmaren (0%)
        HEAT -> Sätt på med senaste preset (eller HIGH om aldrig använd)
        """
        _LOGGER.debug("Setting HVAC mode to: %s", hvac_mode)

        if hvac_mode == HVACMode.OFF:
            # Stäng av
            await self._client.set_level(0)
            self._current_level = 0
            self._attr_hvac_mode = HVACMode.OFF

        elif hvac_mode == HVACMode.HEAT:
            # Sätt på med nuvarande preset
            level = PRESET_TO_LEVEL.get(self._attr_preset_mode, 100)
            await self._client.set_level(level)
            self._current_level = level
            self._attr_hvac_mode = HVACMode.HEAT

        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """
        Set new preset mode (low/medium/high).

        Detta sätter automatiskt HVAC mode till HEAT och väljer effektnivå.
        """
        if preset_mode not in PRESET_TO_LEVEL:
            _LOGGER.error("Invalid preset mode: %s", preset_mode)
            return

        level = PRESET_TO_LEVEL[preset_mode]
        _LOGGER.debug("Setting preset mode to: %s (%d%%)", preset_mode, level)

        await self._client.set_level(level)

        self._current_level = level
        self._attr_preset_mode = preset_mode
        self._attr_hvac_mode = HVACMode.HEAT

        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn the entity on (samma som set HEAT mode)."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn the entity off (samma som set OFF mode)."""
        await self.async_set_hvac_mode(HVACMode.OFF)