"""Climate and power sensor both receive status; polling must not displace them (issue #14)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solamagic.bluetooth import SolamagicBleClient
from custom_components.solamagic.climate import SolamagicClimate
from custom_components.solamagic.client import SolamagicClient
from custom_components.solamagic.const import DOMAIN
from custom_components.solamagic.sensor import SolamagicPowerSensor

ADDR = "D0:65:4C:8B:6C:36"


def make_parts(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"address": ADDR}, title="Heater")
    entry.add_to_hass(hass)
    client = SolamagicClient(hass, entry)
    client._ble = SolamagicBleClient(hass, ADDR, 180)
    climate = SolamagicClimate(client, "Heater", entry)
    sensor = SolamagicPowerSensor(client, "Heater", entry)
    for ent in (climate, sensor):
        ent.hass = hass
        ent.entity_id = f"{'climate' if ent is climate else 'sensor'}.heater_test"
        ent.async_write_ha_state = MagicMock()
    return client, climate, sensor


async def test_one_status_reaches_climate_and_sensor(hass):
    client, climate, sensor = make_parts(hass)
    await climate.async_added_to_hass()
    await sensor.async_added_to_hass()
    client._ble._notification_handler(None, bytearray([0x01, 0x42]))
    assert sensor.native_value == 66
    assert climate.extra_state_attributes["power_level"] == 66
    await sensor.async_will_remove_from_hass()
    await climate.async_will_remove_from_hass()


async def test_poll_does_not_displace_entity_listeners(hass):
    client, climate, sensor = make_parts(hass)
    await climate.async_added_to_hass()
    await sensor.async_added_to_hass()
    before = len(client._ble._status_callbacks)

    client._ensure_initialized = AsyncMock()
    client.disconnect = AsyncMock()
    seen_during_poll = {}

    async def fake_init():
        seen_during_poll["listeners"] = len(client._ble._status_callbacks)
        client._ble._notification_handler(None, bytearray([0x01, 0x21]))

    client._ensure_initialized = fake_init
    await sensor._async_poll_status(None)

    assert seen_during_poll["listeners"] == before + 1, "collector added alongside, nothing removed"
    assert len(client._ble._status_callbacks) == before, "collector removed again afterwards"
    assert climate.extra_state_attributes["power_level"] == 33
    await sensor.async_will_remove_from_hass()
    await climate.async_will_remove_from_hass()


async def test_climate_does_not_claim_success_when_heater_is_silent(hass):
    import pytest
    from homeassistant.components.climate import HVACMode
    from homeassistant.exceptions import HomeAssistantError

    client, climate, _ = make_parts(hass)
    client.set_level = AsyncMock(return_value=False)
    with pytest.raises(HomeAssistantError):
        await climate.async_set_hvac_mode(HVACMode.HEAT)
    assert climate.hvac_mode == HVACMode.OFF

    client.set_level = AsyncMock(return_value=True)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    assert climate.hvac_mode == HVACMode.HEAT
