"""High-level client: initialisation follows the BLE connection lifecycle."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.solamagic.client import SolamagicClient
from custom_components.solamagic.const import CMD_ON_33

ADDR = "D0:65:4C:8B:6C:36"


def make_client() -> SolamagicClient:
    entry = MagicMock()
    entry.data = {"address": ADDR}
    entry.options = {}
    client = SolamagicClient(MagicMock(), entry)
    client._ble = MagicMock()
    client._ble.ensure_ready = AsyncMock()
    client._ble.write_handle_raw = AsyncMock()
    client._ble._status_callback = None
    return client


async def test_set_level_unlocks_through_ble_layer():
    client = make_client()
    await client.set_level(33)
    client._ble.ensure_ready.assert_awaited()
    client._ble.write_handle_raw.assert_awaited_with(CMD_ON_33, response=False, repeat=1, delay_ms=0)


async def test_no_private_initialized_flag_can_go_stale():
    """Unlock state lives in the BLE layer, so an unexpected disconnect can't leave it stale."""
    client = make_client()
    assert not hasattr(client, "_initialized") or client._initialized is False
    await client.set_level(33)
    await client.set_level(33)
    # Every command asks the BLE layer; it decides whether re-unlocking is needed.
    assert client._ble.ensure_ready.await_count == 2


async def test_reappear_listeners_fire():
    client = make_client()
    hits = []
    remove = client.add_reappear_listener(lambda: hits.append(1))
    client.notify_reappeared()
    remove()
    client.notify_reappeared()
    assert hits == [1]
