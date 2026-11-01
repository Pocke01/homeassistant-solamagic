"""Small fakes for the GATT layer used by the BLE client tests."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

from bleak import BleakError

E000 = "0000e000-0000-1000-8000-00805f9b34fb"
ENABLE = "00000001-0000-1000-8000-00805f9b34fb"


class FakeChar:
    def __init__(self, uuid: str, handle: int) -> None:
        self.uuid = uuid
        self.handle = handle


class FakeService:
    def __init__(self, uuid: str, chars: list[FakeChar]) -> None:
        self.uuid = uuid
        self.characteristics = chars

    def get_characteristic(self, uuid: str):
        return next((c for c in self.characteristics if c.uuid == uuid), None)


class FakeServices:
    def __init__(self, services: list[FakeService]) -> None:
        self._services = services

    def get_service(self, uuid: str):
        return next((s for s in self._services if s.uuid == uuid), None)

    def __iter__(self):
        return iter(self._services)


def services_with_enable(handle: int) -> FakeServices:
    """GATT table where the ENABLE characteristic lives at ``handle``."""
    return FakeServices([FakeService(E000, [FakeChar(ENABLE, handle)])])


class FakeBleakClient:
    """Records every GATT operation in ``calls`` (in order)."""

    def __init__(self, services: FakeServices | None, disconnected_callback: Callable | None = None) -> None:
        self.services = services
        self.is_connected = True
        self.calls: list[tuple] = []
        self.read_values: dict[int, bytes] = {}
        self.write_error: Exception | None = None
        self.on_start_notify: Callable[[int], None] | None = None
        self._disconnected_callback = disconnected_callback

    def drop_link(self) -> None:
        """Simulate the peripheral dropping the connection."""
        self.is_connected = False
        if self._disconnected_callback:
            self._disconnected_callback(self)

    async def read_gatt_char(self, handle: int):
        self.calls.append(("read", handle))
        if handle not in self.read_values:
            raise BleakError(f"no characteristic {handle}")
        return bytearray(self.read_values[handle])

    async def write_gatt_char(self, handle: int, data: bytes, response: bool = False):
        self.calls.append(("write", handle, bytes(data), response))
        if self.write_error:
            raise self.write_error

    async def write_gatt_descriptor(self, handle: int, data: bytes):
        self.calls.append(("write_desc", handle, bytes(data)))
        raise ValueError("Cannot write to CCCD (0x2902) directly. Use start_notify() or stop_notify() instead.")

    async def start_notify(self, handle: int, callback: Any):
        self.calls.append(("notify", handle))
        if self.on_start_notify:
            self.on_start_notify(handle)

    async def disconnect(self):
        self.is_connected = False


def service_info(connectable: bool = True, mfr: dict[int, bytes] | None = None, device: Any = None):
    return SimpleNamespace(
        device=device if device is not None else object(),
        connectable=connectable,
        manufacturer_data=mfr if mfr is not None else {89: bytes([0x0C, 0, 0, 0x01])},
        rssi=-60,
    )
