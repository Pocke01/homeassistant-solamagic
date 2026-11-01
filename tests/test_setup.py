"""async_setup_entry wires the advertisement callbacks to the client correctly."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components import bluetooth as ha_bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, CONNECTABLE
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solamagic import async_setup_entry
from custom_components.solamagic.const import DOMAIN

from .fakes import service_info

ADDR = "D0:65:4C:8B:6C:36"


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"address": ADDR}, title="Heater")
    entry.add_to_hass(hass)
    register = MagicMock(return_value=MagicMock())
    track = MagicMock(return_value=MagicMock())
    with patch.object(ha_bluetooth, "async_register_callback", register), patch.object(
        ha_bluetooth, "async_track_unavailable", track
    ), patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()):
        assert await async_setup_entry(hass, entry)
    return hass.data[DOMAIN][entry.entry_id], register, track


async def test_callback_sees_non_connectable_adverts(hass):
    client, register, _ = await _setup(hass)
    matcher = register.call_args.args[2]
    assert matcher[ADDRESS] == ADDR
    assert matcher[CONNECTABLE] is False


async def test_advertisement_updates_client_and_signals_reappearance(hass):
    client, register, _ = await _setup(hass)
    on_advert = register.call_args.args[1]
    hits = []
    client.add_reappear_listener(lambda: hits.append(1))

    on_advert(service_info(connectable=False), None)
    assert hits == [] and client._ble.advert_connectable is False

    on_advert(service_info(connectable=True), None)
    assert hits == [1] and client._ble.available is True

    on_advert(service_info(connectable=True), None)
    assert hits == [1], "already available: no repeated refresh"


async def test_unavailable_then_return_triggers_refresh(hass):
    client, register, track = await _setup(hass)
    on_advert = register.call_args.args[1]
    on_unavailable = track.call_args.args[1]
    hits = []
    client.add_reappear_listener(lambda: hits.append(1))

    on_advert(service_info(connectable=True), None)
    on_unavailable(service_info())
    assert client._ble.available is False and client._ble._cached_device is None

    on_advert(service_info(connectable=True), None)
    assert hits == [1, 1]


async def test_setup_does_not_connect(hass):
    """HA start must not require the heater to be powered."""
    entry = MockConfigEntry(domain=DOMAIN, data={"address": ADDR}, title="Heater")
    entry.add_to_hass(hass)
    with patch.object(ha_bluetooth, "async_register_callback", MagicMock()), patch.object(
        ha_bluetooth, "async_track_unavailable", MagicMock()
    ), patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()), patch(
        "custom_components.solamagic.bluetooth.establish_connection", AsyncMock()
    ) as connect:
        assert await async_setup_entry(hass, entry)
    connect.assert_not_called()
