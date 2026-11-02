"""set_level only reports a level once the heater has echoed the command (issue #11)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.solamagic import client as client_module
from custom_components.solamagic.bluetooth import SolamagicBleClient
from custom_components.solamagic.client import SolamagicClient
from custom_components.solamagic.const import CMD_OFF, CMD_OFF_REPEAT_COUNT, CMD_ON_66

ADDR = "D0:65:4C:8B:6C:36"


def make_client(echo=True, echo_delay=0.0):
    """A real BLE layer whose write is faked; the 'heater' echoes the command back."""
    entry = MagicMock()
    entry.data = {"address": ADDR}
    entry.options = {}
    client = SolamagicClient(MagicMock(), entry)
    ble = SolamagicBleClient(MagicMock(), ADDR, 180)
    ble.ensure_ready = AsyncMock()
    writes = []

    async def write_handle_raw(data, response=False, repeat=1, delay_ms=100):
        writes.append(bytes(data))
        if echo:
            loop = asyncio.get_running_loop()
            if echo_delay:
                loop.call_later(echo_delay, ble._notification_handler, None, bytearray(data))
            else:
                # Echo arrives *during* the write, before write_handle_raw returns.
                ble._notification_handler(None, bytearray(data))

    ble.write_handle_raw = write_handle_raw
    client._ble = ble
    return client, ble, writes


async def test_acknowledged_command_updates_state_once():
    client, ble, writes = make_client()
    levels = []
    ble.add_status_callback(levels.append)
    assert await client.set_level(66) is True
    assert writes == [bytes(CMD_ON_66)]
    assert levels == [66]


async def test_echo_arriving_during_the_write_is_not_missed():
    """The listener must exist before the write: the echo can arrive within a few ms."""
    client, ble, _ = make_client(echo=True, echo_delay=0.0)
    levels = []
    ble.add_status_callback(levels.append)
    await client.set_level(66)
    assert levels == [66]


async def test_unacknowledged_command_leaves_state_alone(caplog, monkeypatch):
    client, ble, _ = make_client(echo=False)
    monkeypatch.setattr(client_module, "COMMAND_CONFIRM_TIMEOUT", 0.05)
    levels = []
    ble.add_status_callback(levels.append)
    with caplog.at_level("WARNING"):
        assert await client.set_level(66) is False
    assert levels == []
    assert "did not acknowledge" in caplog.text
    assert "command confirmed" not in caplog.text.lower()


async def test_off_repeats_and_is_confirmed():
    client, ble, writes = make_client()
    levels = []
    ble.add_status_callback(levels.append)
    await client.set_level(0)
    assert writes == [bytes(CMD_OFF)] * CMD_OFF_REPEAT_COUNT
    assert levels and set(levels) == {0}


async def test_confirmation_callback_is_restored_afterwards():
    client, ble, _ = make_client()
    sentinel = lambda data: None  # noqa: E731
    ble._confirmation_callback = sentinel
    await client.set_level(66)
    assert ble._confirmation_callback is sentinel
