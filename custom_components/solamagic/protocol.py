"""Pure protocol helpers for Solamagic / Koch Heatlink-X heaters.

Derived from static analysis of the official app (com.koch.solamagic 1.2):
after service discovery the app writes ``FF FF FF FD <crc_lo> <crc_hi>`` to the
ENABLE characteristic (service 0xE000, characteristic 0x0001), where the CRC is
CRC-16/ARC (init 0, reflected poly 0xA001) over the device's address bytes in
the order they are written (``D0:65:4C:8B:6C:36`` -> ``D0 65 4C 8B 6C 36``).
"""
from __future__ import annotations

# Bluetooth base UUID: the app builds every UUID as (id << 96) | base.
_BASE = "-0000-1000-8000-00805f9b34fb"


def uuid16(value: int) -> str:
    """Return the 128-bit UUID the app derives from a 16/32-bit id."""
    return f"{value:08x}{_BASE}"


SERVICE_CONFIG_UUID = uuid16(0xE000)
CHAR_ENABLE_UUID = uuid16(0x0001)

# The ENABLE characteristic sits at handle 0x001F on the reference heaters;
# other hardware revisions shift the whole table (e.g. -1).
STANDARD_ENABLE_HANDLE = 0x001F

TOKEN_PREFIX = bytes([0xFF, 0xFF, 0xFF, 0xFD])


def crc16_arc(data: bytes) -> int:
    """CRC-16/ARC, identical to the table-driven routine in the app."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def address_bytes(address: str) -> bytes:
    """Parse ``AA:BB:CC:DD:EE:FF`` / ``AA-BB-...`` into 6 bytes."""
    parts = address.replace("-", ":").split(":")
    if len(parts) != 6 or any(len(p) != 2 for p in parts):
        raise ValueError(f"Invalid Bluetooth address: {address!r}")
    try:
        return bytes(int(p, 16) for p in parts)
    except ValueError as err:
        raise ValueError(f"Invalid Bluetooth address: {address!r}") from err


def compute_init_token(address: str) -> bytes:
    """Return the 9-byte unlock value for ``address``.

    The app sends the first 6 bytes; the heaters present (and this integration
    has always written) the same value padded with three zero bytes.
    """
    crc = crc16_arc(address_bytes(address))
    return TOKEN_PREFIX + bytes([crc & 0xFF, crc >> 8]) + b"\x00\x00\x00"


def decode_power_level(power: int, level: int) -> int | None:
    """Map a ``[power, level]`` pair to 0/33/66/100, or ``None`` if unknown.

    Both the 2-byte frame on the command characteristic and bytes 15/16 of the
    20-byte status notification use this encoding. With ``power == 0`` the level
    byte only remembers the last setting, so the heater is simply off.
    """
    if power == 0x00:
        return 0
    if power == 0x01:
        return {0x21: 33, 0x42: 66, 0x64: 100}.get(level)
    return None
