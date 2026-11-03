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
FIRMWARE_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
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
    assert [c for c in calls[1:4]] == [("notify", 0x2F), ("notify", 0x32), ("notify", 0x28)]
    # Only the firmware revision is read, and only after the unlock and notifications
    assert [c for c in calls[4:]] == [("read", FIRMWARE_UUID)]
    assert not any(c[0] == "read" for c in calls[:4]), "no read round-trip before unlocking"
    assert client.ready is True


async def test_offset_comes_from_enable_characteristic_uuid():
    client = make_client()
    holder, p1, p2 = connect_with(lambda cb: FakeBleakClient(services_with_enable(0x1E), cb))
    with p1, p2:
        await client.ensure_ready()
    calls = holder["fake"].calls
    assert client.handle_offset == -1
    assert calls[0][:2] == ("write", 0x1E)
    assert [c[1] for c in calls[1:4]] == [0x2E, 0x31, 0x27]


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


# --- status listeners and the 2-byte command channel frame (issues #10, #14) ---------


def test_several_status_listeners_all_receive_levels():
    client = make_client()
    a, b = [], []
    client.add_status_callback(a.append)
    client.add_status_callback(b.append)
    client._notification_handler(None, bytearray([0x01, 0x42]))
    assert a == [66] and b == [66]


def test_unsubscribe_removes_only_that_listener_and_is_idempotent():
    client = make_client()
    a, b = [], []
    remove_a = client.add_status_callback(a.append)
    client.add_status_callback(b.append)
    remove_a()
    remove_a()
    client._notification_handler(None, bytearray([0x01, 0x21]))
    assert a == [] and b == [33]


def test_raising_listener_does_not_starve_the_others(caplog):
    client = make_client()
    later = []

    def boom(level):
        raise RuntimeError("bad listener")

    client.add_status_callback(boom)
    client.add_status_callback(later.append)
    client._notification_handler(None, bytearray([0x01, 0x64]))
    assert later == [100]


def test_listener_may_unregister_itself_while_dispatching():
    client = make_client()
    seen = []
    holder = {}

    def once(level):
        seen.append(level)
        holder["remove"]()

    holder["remove"] = client.add_status_callback(once)
    other = []
    client.add_status_callback(other.append)
    client._notification_handler(None, bytearray([0x01, 0x21]))
    client._notification_handler(None, bytearray([0x01, 0x42]))
    assert seen == [33] and other == [33, 66]


def test_command_channel_frame_is_a_status_source():
    """Units without characteristic 0x0032 only ever get the 2-byte frame (issue #10)."""
    client = make_client()
    levels = []
    client.add_status_callback(levels.append)
    client._notification_handler(None, bytearray([0x00, 0x42]))  # off, last level 66
    assert levels == [0]


def test_unknown_command_frame_is_ignored():
    client = make_client()
    levels = []
    client.add_status_callback(levels.append)
    client._notification_handler(None, bytearray([0x07, 0x07]))
    assert levels == []


def test_stale_frame_is_filtered_right_after_a_command():
    client = make_client()
    levels = []
    client.add_status_callback(levels.append)
    client.set_expected_level(66)
    client._notification_handler(None, bytearray([0x01, 0x21]))  # old state arriving late
    client._notification_handler(None, bytearray([0x01, 0x42]))
    assert levels == [66]


def test_twenty_byte_status_still_works():
    client = make_client()
    levels = []
    client.add_status_callback(levels.append)
    data = bytearray(20)
    data[15], data[16] = 0x01, 0x64
    client._notification_handler(None, data)
    assert levels == [100]


async def test_unit_without_status_characteristic_warns_once_and_still_works(caplog):
    """Variant with no 0x0032 (issue #10): connection succeeds, noise is limited."""
    client = make_client()

    def factory(cb):
        fake = FakeBleakClient(services_with_enable(0x1F), cb)

        async def start_notify(handle, callback):
            fake.calls.append(("notify", handle))
            if handle == 0x32:
                raise BleakError("Characteristic 50 was not found!")

        fake.start_notify = start_notify
        return fake

    holder, p1, p2 = connect_with(factory)
    with p1, p2, caplog.at_level(logging.DEBUG):
        await client.ensure_ready()
        assert client.ready is True
        client._handle_disconnect(holder["fake"])
        await client.ensure_ready()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "0x0032" in r.getMessage()]
    assert len(warnings) == 1


def test_routine_advertisement_changes_are_not_logged_at_info(caplog):
    """Counter and checksum bytes change every advert; only real changes deserve INFO."""
    client = make_client()
    base = bytes.fromhex("0c0820037e0300000000000000002100002100002d64")

    def advert(counter: int, crc: bytes, flags: int = 0x20, level: bytes = b"\x00\x21"):
        d = bytearray(base)
        d[2], d[5], d[16:18], d[20:22] = flags, counter, level, crc
        return service_info(True, {89: bytes(d)})

    with caplog.at_level(logging.INFO):
        client.update_advertisement(advert(0x03, b"\x2d\x64"))
        caplog.clear()
        client.update_advertisement(advert(0x05, b"\xab\xcd"))  # counter + checksum only
        assert "Advertisement changed" not in caplog.text
        client.update_advertisement(advert(0x07, b"\x01\x02", flags=0x00))  # flags changed (power-up)
        assert "Advertisement changed" in caplog.text
        caplog.clear()
        client.update_advertisement(advert(0x09, b"\x03\x04", flags=0x00, level=b"\x01\x64"))
        assert "Advertisement changed" in caplog.text
    assert client.manufacturer_data[89].startswith("0c08")


# --- firmware revision is refreshed on every connection (0.6.1) ------------------------


def _factory_with_firmware(value: bytes | None):
    def factory(cb):
        fake = FakeBleakClient(services_with_enable(0x1F), cb)
        if value is not None:
            fake.read_values[FIRMWARE_UUID] = value
        return fake

    return factory


async def test_firmware_is_read_after_unlock_and_listeners_hear_changes():
    client = make_client()
    seen = []
    client.add_firmware_listener(seen.append)
    holder, p1, p2 = connect_with(_factory_with_firmware(b"BTS1-0013\x00"))
    with p1, p2:
        await client.ensure_ready()
    assert client.firmware == "BTS1-0013"
    assert seen == ["BTS1-0013"]


async def test_unchanged_firmware_does_not_notify_again():
    client = make_client()
    seen = []
    client.add_firmware_listener(seen.append)
    for _ in range(2):
        holder, p1, p2 = connect_with(_factory_with_firmware(b"BTS1-0013"))
        with p1, p2:
            await client.ensure_ready()
        client._handle_disconnect(holder["fake"])
    assert seen == ["BTS1-0013"]


async def test_updated_firmware_is_reported_on_next_connection():
    client = make_client()
    seen = []
    client.add_firmware_listener(seen.append)
    for version in (b"BTS1-0013", b"BTS1-0014"):
        holder, p1, p2 = connect_with(_factory_with_firmware(version))
        with p1, p2:
            await client.ensure_ready()
        client._handle_disconnect(holder["fake"])
    assert seen == ["BTS1-0013", "BTS1-0014"]
    assert client.firmware == "BTS1-0014"


async def test_unreadable_firmware_never_breaks_the_connection():
    client = make_client()
    holder, p1, p2 = connect_with(_factory_with_firmware(None))  # read raises BleakError
    with p1, p2:
        await client.ensure_ready()
    assert client.ready is True and client.firmware is None


async def test_listener_error_does_not_break_the_connection():
    client = make_client()

    def boom(value):
        raise RuntimeError("bad listener")

    client.add_firmware_listener(boom)
    holder, p1, p2 = connect_with(_factory_with_firmware(b"BTS1-0013"))
    with p1, p2:
        await client.ensure_ready()
    assert client.ready is True
