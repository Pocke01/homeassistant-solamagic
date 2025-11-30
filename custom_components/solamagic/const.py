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

# Timing constants (from protocol analysis and testing)
# These values were determined through Bluetooth sniffing and testing
INIT_DELAY_MS = 50          # Delay after initialization (ms)
CCCD_ENABLE_DELAY_MS = 50   # Delay between CCCD enables (ms)
CMD_CONFIRMATION_DELAY_MS = 200  # Wait for command confirmation (ms)
CMD_OFF_REPEAT_COUNT = 21   # Number of OFF commands to send (from sniffer)
CMD_OFF_DELAY_MS = 16       # Delay between OFF commands (ms)

# Status parsing constants (from protocol analysis)
STATUS_MIN_LENGTH = 17      # Minimum length of status data
STATUS_POWER_BYTE = 15      # Byte index for power status (0=off, 1=on)
STATUS_LEVEL_BYTE = 16      # Byte index for level (0x21=33, 0x42=66, 0x64=100)

# Config keys
CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_COMMAND_CHAR = "command_characteristic"
CONF_DEFAULT_ON_LEVEL = "default_on_level"
CONF_WRITE_MODE = "write_mode"  # "handle" (recommended via proxy) or "uuid"
CONF_INIT_TOKEN = "init_token"
CONF_DEVICE_INFO = "device_info"  # Manufacturer, model, HW/SW versions

def format_device_name(address: str) -> str:
    """
    Create device name from MAC address.

    This is the single source of truth for device naming.
    Used by both config flow and device registry.

    Args:
        address: MAC address (e.g., "D0:65:4C:8B:6C:36")

    Returns:
        Formatted name (e.g., "2000BT-8B6C36")

    Examples:
        >>> format_device_name("D0:65:4C:8B:6C:36")
        "2000BT-8B6C36"
        >>> format_device_name("")
        "Solamagic 2000BT"
    """
    if address:
        # Take last 6 characters of MAC (e.g., "8B6C36")
        short_mac = address.replace(":", "")[-6:].upper()
        return f"2000BT-{short_mac}"
    return "Solamagic 2000BT"


def get_device_info(address: str, entry_title: str = None, device_info_dict: dict | None = None) -> dict:
    """
    Create uniform device_info for all entities.

    This ensures that all entities for the same device
    use the same device information.

    Args:
        address: MAC address of the device
        entry_title: Title from config entry (user's chosen name)
                     If provided, this is used as device name.
                     If None, generates name from MAC address.
        device_info_dict: Optional dict with manufacturer, model, hw_version, sw_version
                          read from BLE Device Information Service

    Returns:
        Dict with device information
    """
    # Use entry_title if provided (user's chosen name),
    # otherwise generate from MAC address
    if entry_title:
        device_name = entry_title
    else:
        device_name = format_device_name(address)

    # Start with defaults
    device_info = {
        "identifiers": {(DOMAIN, address)},
        "manufacturer": "Solamagic",
        "name": device_name,
        "model": "2000BT",
        "suggested_area": "Outdoor",
    }

    # Override with BLE-read info if available
    if device_info_dict:
        if "manufacturer" in device_info_dict:
            device_info["manufacturer"] = device_info_dict["manufacturer"]
        if "model" in device_info_dict:
            # Combine commercial name with technical model
            # e.g., "Solamagic 2000BT (KOCH BTS)"
            ble_model = device_info_dict["model"]
            device_info["model"] = f"Solamagic 2000BT ({ble_model})"
        if "hw_version" in device_info_dict:
            device_info["hw_version"] = device_info_dict["hw_version"]
        if "sw_version" in device_info_dict:
            device_info["sw_version"] = device_info_dict["sw_version"]
    
    # Fallback: Add MAC as hardware version if not set and address available
    if "hw_version" not in device_info and address:
        device_info["hw_version"] = address

    return device_info