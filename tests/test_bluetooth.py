"""Behaviour of the BLE layer: connect sequence, races, token source, availability."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak import BleakError
from homeassistant.exceptions import HomeAssistantError

from custom_components.solamagic.bluetooth import SolamagicBleClient, _as_ha_error
from custom_components.solamagic.protocol import compute_init_token

from .fakes import FakeBleakClient, FakeServices, service_info, services_with_enable

ADDR = "D0:65:4C:8B:6C:36"
TOKEN = compute_init_token(ADDR)  # FFFFFFFD9434000000


def make_client(stored_token: bytes | None = None) -> SolamagicBleClient:
    hass = MagicMock()
    client = SolamagicBleClient(hass, ADDR, 180, stored_token)
    client.update_ble_device(object())  # a device known from advertisements
    return client


def connect_with(fake_factory):
    """Patch establish_connection so it returns the fake built by ``fake_factory``."""
    holder: dict = {}

    async def _establish(cls, device, name, disconnected_callback=None, **kw):
        fake = fake_factory(disconnected_callback)
        holder["fake"] = fake
        return fake

    return holder, patch("custom_components.solamagic.bluetooth.establish_connection", _establish), patch(
        "custom_components.solamagic.bluetooth.close_stale_connections", AsyncMock()
    )


async def test_token_written_first_then_notifications_in_app_order():
    client = make_client()
    holder, p1, p2 = connect_with(lambda cb: FakeBleakClient(services_with_enable(0x1F), cb))
    with p1, p2:
        await client.ensure_ready()
    calls = holder["fake"].calls
    assert calls[0] == ("write", 0x1F, TOKEN, True)
    assert [c for c in calls[1:]] == [("notify", 0x2F), ("notify", 0x32), ("notify", 0x28)]
    assert not any(c[0] == "read" for c in calls), "no read round-trip before unlocking"
    assert client.ready is True


async def test_offset_comes_from_enable_characteristic_uuid():
    client = make_client()
    holder, p1, p2 = connect_with(lambda cb: FakeBleakClient(services_with_enable(0x1E), cb))
    with p1, p2:
        await client.ensure_ready()
    calls = holder["fake"].calls
    assert client.handle_offset == -1
    assert calls[0][:2] == ("write", 0x1E)
    assert [c[1] for c in calls[1:]] == [0x2E, 0x31, 0x27]


async def test_falls_back_to_probing_when_uuid_missing():
    client = make_client()

    def factory(cb):
        fake = FakeBleakClient(FakeServices([]), cb)
        fake.read_values[0x1E] = bytes.fromhex("FFFFFFFD9434000000")
        return fake

    holder, p1, p2 = connect_with(factory)
    with p1, p2:
        await client.ensure_ready()
    assert client.handle_offset == -1
    assert any(c[0] == "write" and c[1] == 0x1E for c in holder["fake"].calls)


async def test_disconnect_during_setup_raises_instead_of_returning_none():
    """Regression: _ensure_connected used to return None -> 'NoneType has no write_gatt_char'."""
    client = make_client()

    def factory(cb):
        fake = FakeBleakClient(services_with_enable(0x1F), cb)
        fake.on_start_notify = lambda handle: fake.drop_link()
        return fake

    holder, p1, p2 = connect_with(factory)
    with p1, p2:
        with pytest.raises(HomeAssistantError, match="(?i)connection lost"):
            await client.ensure_ready()
    assert client.ready is False
    assert client._client is None


async def test_write_failure_message_is_not_swapped():
    client = make_client()

    def factory(cb):
        fake = FakeBleakClient(services_with_enable(0x1F), cb)
        fake.write_error = BleakError("boom")
        return fake

    holder, p1, p2 = connect_with(factory)
    with p1, p2:
        with pytest.raises(HomeAssistantError) as err:
            await client.ensure_ready()
    assert str(err.value).startswith("Init failed: ")
    assert "boom" in str(err.value)
    assert client.ready is False


def test_as_ha_error_argument_order():
    assert str(_as_ha_error(ValueError("x"), "Prefix")) == "Prefix: x"


async def test_stored_token_wins_but_mismatch_is_logged(caplog):
    stored = bytes.fromhex("FFFFFFFD1111000000")
    client = make_client(stored_token=stored)
    holder, p1, p2 = connect_with(lambda cb: FakeBleakClient(services_with_enable(0x1F), cb))
    with caplog.at_level(logging.WARNING), p1, p2:
        await client.ensure_ready()
    assert holder["fake"].calls[0] == ("write", 0x1F, stored, True)
    assert "does not match" in caplog.text


async def test_matching_stored_token_is_silent(caplog):
    client = make_client(stored_token=TOKEN)
    holder, p1, p2 = connect_with(lambda cb: FakeBleakClient(services_with_enable(0x1F), cb))
    with caplog.at_level(logging.WARNING), p1, p2:
        await client.ensure_ready()
    assert "does not match" not in caplog.text


async def test_disconnect_resets_ready_and_notifies_listeners():
    client = make_client()
    seen = []
    client.add_disconnect_listener(lambda: seen.append(1))
    holder, p1, p2 = connect_with(lambda cb: FakeBleakClient(services_with_enable(0x1F), cb))
    with p1, p2:
        await client.ensure_ready()
        assert client.ready
        holder["fake"].drop_link()
    assert client.ready is False
    assert seen == [1]


async def test_second_ensure_ready_reuses_connection():
    client = make_client()
    holder, p1, p2 = connect_with(lambda cb: FakeBleakClient(services_with_enable(0x1F), cb))
    with p1, p2:
        await client.ensure_ready()
        n = len(holder["fake"].calls)
        await client.ensure_ready()
    assert len(holder["fake"].calls) == n


async def test_write_cccd_swallows_bleak3_valueerror():
    """Bleak >= 3 raises ValueError for direct CCCD writes; must not escape."""
    client = make_client()
    holder, p1, p2 = connect_with(lambda cb: FakeBleakClient(services_with_enable(0x1F), cb))
    with p1, p2:
        await client.write_cccd(0x29, b"\x01\x00")  # must not raise


def test_availability_transitions():
    client = make_client()
    assert client.update_advertisement(service_info(connectable=False)) is False
    assert client.advert_connectable is False
    assert client.update_advertisement(service_info(connectable=True)) is True  # first connectable
    assert client.update_advertisement(service_info(connectable=True)) is False  # already available
    client.mark_unavailable()
    assert client.available is False and client._cached_device is None
    assert client.update_advertisement(service_info(connectable=True)) is True  # reappeared


def test_advertisement_diagnostics_recorded():
    client = make_client()
    client.update_advertisement(service_info(mfr={89: bytes.fromhex("0c00000100")}))
    assert client.manufacturer_data == {89: "0c00000100"}
    assert client.last_seen is not None
