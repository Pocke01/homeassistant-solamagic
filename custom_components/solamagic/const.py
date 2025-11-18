"""Constants for the Solamagic integration."""
from __future__ import annotations

DOMAIN = "solamagic"

# UUIDs that work in this setup (from earlier tests)
SERVICE_UUID = "0000f000-0000-1000-8000-00805f9b34fb"
CHAR_CMD_F001 = "0000f001-0000-1000-8000-00805f9b34fb"
CHAR_ALT_F002 = "0000f002-0000-1000-8000-00805f9b34fb"

# Handles (from Bluetooth sniffing)
# This is what REALLY works!
HANDLE_CMD = 0x0028   # Command characteristic - where we write commands
HANDLE_NTF1 = 0x002F  # Status notification
HANDLE_NTF2 = 0x0032  # Data notification

# Initialization handle (CRITICAL!)
# This MUST be written BEFORE enabling CCCDs
HANDLE_INIT = 0x001F  # Initialization/unlock characteristic
INIT_PAYLOAD = bytes([0xFF, 0xFF, 0xFF, 0xFD, 0x94, 0x34, 0x00, 0x00, 0x00])

# CCCD handles (Client Characteristic Configuration Descriptors)
# These MUST be enabled in the correct order AFTER initialization!
CCCD_NTF1 = 0x0030  # CCCD for 0x002F (enable notifications: 0x01 0x00) - FIRST!
CCCD_NTF2 = 0x0033  # CCCD for 0x0032 (enable notifications: 0x01 0x00) - SECOND!
CCCD_CMD = 0x0029   # CCCD for 0x0028 (enable notifications: 0x01 0x00) - LAST!

# Commands (2 bytes: [power/mode, level])
# From Heatlink sniffing - uses Write Command (0x52) without response!
CMD_OFF = bytes([0x00, 0x21])      # Off (0 = off, 33 = 0x21 hex)
CMD_ON_33 = bytes([0x01, 0x21])    # 33% (1 = on, 33 = 0x21 hex)
CMD_ON_66 = bytes([0x01, 0x42])    # 66% (1 = on, 66 = 0x42 hex)
CMD_ON_100 = bytes([0x01, 0x64])   # 100% (1 = on, 100 = 0x64 hex)

# Legacy aliases (deprecated but kept for compatibility)
CMD_ON_100_OLD = bytes([0x01, 0x64])
CMD_LEVEL_33 = CMD_ON_33
CMD_LEVEL_66 = CMD_ON_66

# Config keys
CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_COMMAND_CHAR = "command_characteristic"
CONF_DEFAULT_ON_LEVEL = "default_on_level"
CONF_WRITE_MODE = "write_mode"  # "handle" (recommended via proxy) or "uuid"


def get_device_info(address: str, entry_title: str = None) -> dict:
    """
    Create uniform device_info for all entities.
    
    This ensures that all entities for the same device
    use the same device information.
    
    Args:
        address: MAC address of the device
        entry_title: Title from config entry (if available)
    
    Returns:
        Dict with device information
    """
    # Create a better device name based on MAC address
    if address:
        # Take last 6 characters of MAC (e.g. "8B6C36")
        short_mac = address.replace(":", "")[-6:].upper()
        device_name = f"BT2000-{short_mac}"
    else:
        device_name = entry_title or "Solamagic BT2000"
    
    device_info = {
        "identifiers": {(DOMAIN, address)},
        "manufacturer": "Solamagic",
        "name": device_name,
        "model": "BT2000",
        "suggested_area": "Outdoor",
    }
    
    # Add MAC as hardware version if available
    if address:
        device_info["hw_version"] = address
    
    return device_info