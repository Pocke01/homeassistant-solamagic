# CLAUDE.md - Solamagic Home Assistant Integration

## Project Overview

Custom Home Assistant integration for controlling Solamagic 2000BT Bluetooth infrared patio heaters. This project represents reverse-engineering of the proprietary xHeatlink mobile app's Bluetooth Low Energy (BLE) protocol through packet sniffing and protocol analysis.

**Author:** Jocke (Pocke01)  
**Repository:** https://github.com/Pocke01/homeassistant-solamagic  
**Status:** Production-ready, submitted to HACS  
**Primary Language:** Python (first Python project for author)  
**Documentation Language:** English (developer is Swedish)

## Key Technical Achievements

### Protocol Reverse Engineering

The integration was built by analyzing Bluetooth packet captures from the official xHeatlink iOS app using Apple PacketLogger. Through systematic analysis, we discovered:

1. **Critical BLE Handles:**
   - `0x001F` (or `0x001E` on some models): Initialization/unlock characteristic
   - `0x0028`: Command characteristic (power level control)
   - `0x002F` & `0x0032`: Status notification characteristics

2. **Initialization Sequence (since 0.6.0b1, from the decompiled app):**
   - Right after connecting, write `FF FF FF FD <crc_lo> <crc_hi> 00 00 00` to the ENABLE
     characteristic (service `0000e000-…`, characteristic `00000001-…`, handle 0x001F / 0x001E).
     The CRC is CRC-16/ARC over the MAC address bytes (`D0:65:4C:8B:6C:36` -> `94 34`).
   - Then enable notifications with `start_notify` (0x2F, 0x32, 0x28). Bleak >= 3 refuses direct
     CCCD writes (`ValueError`), so never write 0x2902 descriptors manually.
   - The app connects with `connectGatt(autoConnect=true)` and finds heaters via manufacturer
     data (company id 89 / 2161), not by name or service UUID.

3. **Command Protocol:**
   - 2-byte commands sent without response
   - Format: `[power/mode, level]`
   - OFF: `0x00 0x21`
   - 33%: `0x01 0x21`
   - 66%: `0x01 0x42`
   - 100%: `0x01 0x64`

4. **Device-Specific Init Tokens:**
   - Pattern `FF FF FF FD [XX XX] 00 00 00` (9 bytes); `XX XX` = CRC-16/ARC of the MAC bytes
   - Verified on three units (94 34, 2A BA, 57 80); no reading from the device needed

### Multi-Model Support Discovery

Through community testing, we discovered two hardware variants:
- **Model A:** Init handle `0x001F` (31) - most common
- **Model B:** Init handle `0x001E` (30) - entire GATT table shifted by -1

This led to implementing auto-detection that tries multiple handle candidates.

## Architecture

### Component Structure

```
custom_components/solamagic/
├── __init__.py          # Integration setup, service handlers
├── bluetooth.py         # Low-level BLE client (SolamagicBleClient)
├── client.py           # High-level control client (SolamagicClient)
├── climate.py          # Climate entity (HVAC control)
├── sensor.py           # Sensors (power level, RSSI, connection status)
├── config_flow.py      # Configuration UI and Bluetooth discovery
├── const.py            # Constants, handles, commands
├── manifest.json       # Integration metadata
├── services.yaml       # Service definitions
├── strings.json        # UI strings (English base)
└── translations/
    ├── en.json         # English
    ├── sv.json         # Swedish
    └── da.json         # Danish
```

### Key Classes

**SolamagicBleClient** (`bluetooth.py`)
- Manages BLE connection lifecycle
- Handles GATT operations (read, write, notifications)
- Implements auto-disconnect timer (180s) to allow mobile app access
- Parses status notifications from heater
- Returns BleakClientWithServiceCache for operations

**SolamagicClient** (`client.py`)
- Provides high-level control methods (`set_level`, `turn_on`, `turn_off`)
- Manages initialization sequence
- Coordinates between BLE layer and Home Assistant entities
- Handles status callbacks to entities

**SolamagicClimate** (`climate.py`)
- Home Assistant climate entity
- Preset modes: Low (33%), Medium (66%), High (100%)
- HVAC modes: OFF, HEAT
- Syncs with power sensor for state consistency

### Data Flow

```
User Action (HA UI/Automation)
    ↓
Climate Entity / Service Call
    ↓
SolamagicClient (high-level)
    ↓
SolamagicBleClient (BLE operations)
    ↓
Heater Hardware
    ↓
BLE Notifications
    ↓
Status Callback
    ↓
Sensor & Climate Entity Update
```

## Important Implementation Details

### Initialization Sequence

Runs on every new BLE connection (state lives in `SolamagicBleClient`, reset on disconnect):

```python
1. Write the computed token to the ENABLE characteristic (service 0xE000 / char 0x0001)
2. start_notify 0x002F, then 0x0032, then 0x0028
```

The token is `FF FF FF FD <crc16-arc(MAC)> 00 00 00` (`protocol.py`); nothing is read from or
saved for the device. A stored `init_token` from older versions still wins, with a warning on mismatch.

### Status Parsing

Status notifications are 20 bytes with this structure:
```
Byte 15: Power status (0x00 = OFF, 0x01 = ON)
Byte 16: Level code (0x21 = 33%, 0x42 = 66%, 0x64 = 100%)
```

The heater sends multiple notifications on state change (first OFF, then new state).

### Auto-Disconnect Feature

To allow the xHeatlink mobile app to connect, the integration:
- Implements 180-second disconnect timer
- Resets timer on any activity
- Disconnects automatically when idle
- Reconnects on-demand when commands are sent

This was crucial for user acceptance - users can still use the mobile app.

### Handle Auto-Detection

Added to support multiple heater models:

```python
HANDLE_INIT_CANDIDATES = [0x001F, 0x001E, 0x001D]

# Try each until one works
for handle in HANDLE_INIT_CANDIDATES:
    try:
        init_value = read_gatt_char(handle)
        if init_value:
            # Save working handle for future use
            save_to_config(handle)
            break
    except:
        continue
```

## Development Tools & Resources

### Bluetooth Sniffing
- **Apple PacketLogger** - Primary tool for iOS app analysis
- **Wireshark** - Alternative for deeper packet analysis
- **ESPHome Bluetooth Proxy** - For testing in Home Assistant

### Testing Environment
- Home Assistant development container
- Real hardware: Solamagic 2000BT heaters
- Multiple test units with different firmware versions

### Key Resources
- Home Assistant BLE integration documentation
- Bleak library (Python BLE library)
- HACS submission guidelines
- Home Assistant integration quality scale

## Common Issues & Solutions

### Issue: "Characteristic 31 was not found"
**Cause:** Heater uses handle 30 instead of 31  
**Solution:** Auto-detection tries multiple handles (31, 30, 29)

### Issue: Commands don't work
**Cause:** Unlock not completed, or very old heater firmware (`BTS1-000x`, handle 0x001F is then a
12-byte placeholder)  
**Solution:** Check the log for `Heater did not acknowledge`; update the heater via the xHeatlink app

### Issue: Status not updating
**Cause:** Notifications not properly enabled  
**Solution:** Verify CCCD writes successful, check callback registration

### Issue: Mobile app can't connect
**Cause:** Home Assistant holding connection  
**Solution:** Auto-disconnect timer (180s), or manual disconnect service

### Issue: Heater stops responding
**Cause:** Bluetooth connection stale or heater needs power cycle  
**Solution:** Disconnect/reconnect, power cycle heater

## Code Quality Standards

Following Home Assistant integration standards:

- **Error Handling:** Use `HomeAssistantError` for user-facing errors
- **Async Patterns:** Proper async/await, no blocking operations
- **Timing:** Named constants instead of hardcoded values
- **Logging:** Appropriate log levels (DEBUG for operations, INFO for state changes, WARNING for issues)
- **Type Hints:** Full type annotations throughout
- **Documentation:** Docstrings for all public methods
- **Testing:** Manual testing with real hardware (automated tests pending)

## Services

Advanced services for power users and debugging:

- **`solamagic.set_level`** - Set power level (0/33/66/100)
- **`solamagic.write_handle`** - Raw write to command handle
- **`solamagic.write_handle_any`** - Write to any handle (debugging)
- **`solamagic.write_uuid`** - Write to characteristic by UUID
- **`solamagic.disconnect`** - Force disconnect
- **`solamagic.scan_init_handles`** - Diagnostic tool to find init handle

## Diagnostic Tools

### Init Handle Scanner

A diagnostic service was created to help identify the correct init handle on different heater models:

```python
# Scans handles 15-40 by default
# Identifies readable handles
# Flags potential init tokens (9 bytes starting with 0xFF)
# Logs complete results for analysis
```

Usage:
1. Enable DEBUG logging for `custom_components.solamagic`
2. Call `solamagic.scan_init_handles` service
3. Check logs for `🎯 POTENTIAL INIT TOKEN FOUND`
4. Review `📊 SCAN COMPLETE` summary

## Translation Support

Currently supports:
- **English** (en.json) - Base language
- **Swedish** (sv.json) - Developer's native language
- **Danish** (da.json) - Added for community user

All UI strings, service descriptions, and entity names are translatable.

## Future Enhancements

Potential improvements identified:

1. **Automated Testing:** Unit tests for BLE protocol, integration tests
2. **Additional Models:** Support for Solamagic dimmer variants
3. **Energy Monitoring:** Calculate power consumption based on level
4. **Schedules:** Built-in scheduling without automations
5. **Groups:** Control multiple heaters as one entity
6. **OTA Updates:** If heaters support firmware updates via BLE

## Community & Support

- **GitHub Issues:** Bug reports and feature requests
- **Home Assistant Community Forum:** User discussions
- **HACS:** Primary distribution method
- **Documentation:** README.md in repository

## Development Workflow

1. **Protocol Analysis:** Sniff BLE packets from mobile app
2. **Hypothesis:** Formulate theory about protocol behavior
3. **Implementation:** Code the feature in Python
4. **Testing:** Verify with real hardware
5. **Iteration:** Refine based on results
6. **Documentation:** Update code comments and docs
7. **Community Testing:** Beta users test changes
8. **Release:** Tag version, submit to HACS

## Key Learnings

### BLE Protocol Insights
- Read-echo-save pattern for initialization
- CCCD order matters
- Multiple notifications per state change
- Handle numbering can vary between firmware versions

### Home Assistant Integration
- Device registry critical for multi-device support
- Event system better than callback chains for state sync
- Auto-disconnect improves user experience
- Diagnostic services invaluable for community support

### Python Development
- Type hints catch bugs early
- Async patterns need careful deadlock prevention
- Logging levels are communication with users
- Error messages should guide users to solutions

## Attribution

This integration would not exist without:
- **Solamagic/KOCH** - Hardware manufacturer
- **Dension** - xHeatlink app developer (protocol source)
- **Home Assistant Community** - Testing and feedback
- **Bleak Library** - Python BLE foundation
- **Claude (Anthropic)** - Development assistance and code review

## Version History

- **0.6.1** - Firmware revision (0x2A26) read on every connection; device registry and stored device info follow a firmware update
- **0.6.0** - Stable release of the 0.6.0 betas (README and docs updated)
- **0.6.0b3** - From the 2026-11-03 power-cycle log: poll skipped while the link is already up (no false timeout warning), poll never cuts the link under a running command, advertisement INFO logging only on real changes (counter/checksum bytes ignored)
- **0.6.0b2** - Ported from community PR #13 (storebatfar): status from the 2-byte command-channel frame (units without 0x0032), commands only reported once the heater echoes them, multiple status listeners (climate + sensor + poll)
- **0.6.0b1** - Token computed from MAC, unlock before notifications, Bleak 3 CCCD fix, connect race fix, advertisement-driven reconnect, company id 2161
- **0.5.2** - Handle auto-detection for multi-model support
- **0.5.1** - Added diagnostic scanner service
- **0.5.0** - HACS submission, Danish translation
- **0.4.x** - Init token discovery and save
- **0.3.x** - Auto-disconnect timer
- **0.2.x** - Climate entity with presets
- **0.1.x** - Initial BLE control implementation

## License

See repository LICENSE file.

---

**Last Updated:** 2026-02-01  
**Document Version:** 1.0  
**For Claude Code & Future Development Reference**
