from __future__ import annotations

DOMAIN = "solamagic"

# UUID:er som fungerar i din setup (från tidigare tester)
SERVICE_UUID = "0000f000-0000-1000-8000-00805f9b34fb"
CHAR_CMD_F001 = "0000f001-0000-1000-8000-00805f9b34fb"
CHAR_ALT_F002 = "0000f002-0000-1000-8000-00805f9b34fb"

# Handles (från Bluetooth-sniffning)
# Detta är det som VERKLIGEN fungerar!
HANDLE_CMD = 0x0028   # Command characteristic - här skriver vi kommandon
HANDLE_NTF1 = 0x002F  # Status notification
HANDLE_NTF2 = 0x0032  # Data notification

# Initialization handle (KRITISKT!)
# Detta måste skrivas FÖRE CCCD-aktivering
HANDLE_INIT = 0x001F  # Initialization/unlock characteristic
INIT_PAYLOAD = bytes([0xFF, 0xFF, 0xFF, 0xFD, 0x94, 0x34, 0x00, 0x00, 0x00])

# CCCD handles (Client Characteristic Configuration Descriptors)
# Dessa MÅSTE aktiveras i rätt ordning EFTER initialization!
CCCD_NTF1 = 0x0030    # CCCD för 0x002F (enable notifications: 0x01 0x00) - FÖRSTA!
CCCD_NTF2 = 0x0033    # CCCD för 0x0032 (enable notifications: 0x01 0x00) - ANDRA!
CCCD_CMD = 0x0029     # CCCD för 0x0028 (enable notifications: 0x01 0x00) - SISTA!

# Kommandon (2 bytes: [power/mode, level])
# Från Heatlink-sniffning - använder Write Command (0x52) utan response!
CMD_OFF = bytes([0x00, 0x21])      # Av (0 = off, 33 = 0x21 hex)
CMD_ON_33 = bytes([0x01, 0x21])    # 33% (1 = on, 33 = 0x21 hex)
CMD_ON_66 = bytes([0x01, 0x42])    # 66% (1 = on, 66 = 0x42 hex)
CMD_ON_100 = bytes([0x01, 0x64])   # 100% (1 = on, 100 = 0x64 hex)

# Äldre aliases (deprecated men behålls för kompatibilitet)
CMD_ON_100_OLD = bytes([0x01, 0x64])
CMD_LEVEL_33 = CMD_ON_33
CMD_LEVEL_66 = CMD_ON_66

# Config keys
CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_COMMAND_CHAR = "command_characteristic"
CONF_DEFAULT_ON_LEVEL = "default_on_level"
CONF_WRITE_MODE = "write_mode"  # "handle" (rekommenderat via proxy) eller "uuid"


def get_device_info(address: str, entry_title: str = None) -> dict:
    """
    Skapa enhetlig device_info för alla entities.
    
    Detta säkerställer att alla entities för samma enhet
    använder samma device information.
    
    Args:
        address: MAC-adress för enheten
        entry_title: Titel från config entry (om tillgänglig)
    
    Returns:
        Dict med device information
    """
    # Skapa ett bättre device name baserat på MAC-adress
    if address:
        # Ta sista 6 tecken av MAC (ex: "8B6C36")
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
    
    # Lägg till MAC som hardware version om tillgänglig
    if address:
        device_info["hw_version"] = address
    
    return device_info