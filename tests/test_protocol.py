"""Token derivation, verified against the decompiled app (CRC-16/ARC over MAC)."""
import pytest

from custom_components.solamagic.protocol import (
    address_bytes,
    compute_init_token,
    crc16_arc,
)


def test_crc16_arc_check_value():
    # Standard CRC-16/ARC test vector.
    assert crc16_arc(b"123456789") == 0xBB3D


def test_address_bytes_accepts_colon_and_dash():
    assert address_bytes("D0:65:4C:8B:6C:36") == bytes.fromhex("D0654C8B6C36")
    assert address_bytes("d0-65-4c-8b-6c-36") == bytes.fromhex("D0654C8B6C36")


def test_known_heater_token():
    # Verified: this is the INIT_PAYLOAD that already works on real hardware.
    assert compute_init_token("D0:65:4C:8B:6C:36") == bytes.fromhex("FFFFFFFD9434000000")


def test_token_is_little_endian_crc_after_prefix():
    token = compute_init_token("F2:E8:4A:A3:5F:B2")
    crc = crc16_arc(address_bytes("F2:E8:4A:A3:5F:B2"))
    assert token[:4] == bytes.fromhex("FFFFFFFD")
    assert token[4] == crc & 0xFF and token[5] == crc >> 8
    assert token[6:] == b"\x00\x00\x00"
    assert len(token) == 9


@pytest.mark.parametrize("bad", ["", "D0:65:4C", "ZZ:65:4C:8B:6C:36", "D0:65:4C:8B:6C:36:00"])
def test_invalid_address_raises(bad):
    with pytest.raises(ValueError):
        address_bytes(bad)
