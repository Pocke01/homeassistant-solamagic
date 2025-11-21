# Solamagic 2000BT for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/Pocke01/homeassistant-solamagic.svg)](https://github.com/Pocke01/homeassistant-solamagic/releases)
![](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.solamagic.total)

Home Assistant custom integration for Solamagic 2000BT Bluetooth infrared patio heaters.

## Features

- ✅ **Climate Entity** - Control heater with preset modes (33%, 66%, 100%)
- ✅ **Real-time Status** - Instant updates when changing power level
- ✅ **Sensors** - Power level, RSSI signal strength, connection status
- ✅ **Bluetooth Proxy Support** - Works with ESPHome Bluetooth proxies
- ✅ **Auto-disconnect** - Automatically disconnects after 3 minutes to allow app access
- ✅ **Device Picker** - Easy device selection in service calls (no more entry_id!)
- ✅ **Services** - Advanced control via Home Assistant services

## Supported Devices

- Solamagic 2000BT (Bluetooth-enabled infrared heaters)
- Manufacturer ID: 89
- Bluetooth address pattern: `D0:65:4C:*`

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots in the top right
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/Pocke01/homeassistant-solamagic`
6. Category: "Integration"
7. Click "Add"
8. Search for "Solamagic" and install

### Manual Installation

1. Download the latest release
2. Copy the `custom_components/solamagic` folder to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

## Configuration

The integration is configured via the UI:

1. Go to **Settings** → **Devices & Services**
2. Click **"+ Add Integration"**
3. Search for **"Solamagic"**
4. The integration will auto-discover your heater via Bluetooth
5. Follow the setup steps

## Usage

### Climate Entity

The heater appears as a climate entity with the following controls:

- **HVAC Mode:** Heat / Off
- **Preset Modes:**
  - Low (33%)
  - Medium (66%)
  - High (100%)

### Sensors

- **Power Level** - Current power level in %
- **Signal Strength** - Bluetooth RSSI in dBm
- **Connection Status** - Connected or disconnected

### Services

Advanced control via services with **easy device selection**:

#### `solamagic.set_level`
Set heater to specific power level (0%, 33%, 66%, or 100%).

**Using Device Picker (Easy!):**
```yaml
service: solamagic.set_level
target:
  device_id: # Select from dropdown in UI
data:
  level: 66  # Options: 0, 33, 66, 100
```

**In Automations:**
```yaml
automation:
  - alias: "Turn on patio heater at sunset"
    trigger:
      platform: sun
      event: sunset
    action:
      - service: solamagic.set_level
        target:
          device_id: YOUR_DEVICE_ID  # Easy to select in UI
        data:
          level: 66
```

**Advanced (using entry_id):**
```yaml
service: solamagic.set_level
data:
  entry_id: YOUR_ENTRY_ID  # Still supported for advanced users
  level: 66
```

#### `solamagic.write_handle`
Send raw Bluetooth commands to handle 0x0028 (advanced users).

```yaml
service: solamagic.write_handle
target:
  device_id: # Select your heater
data:
  payload_hex: "01 42"  # 66% command
  response: false
  repeat: 2  # Optional: repeat count
  delay_ms: 120  # Optional: delay between repeats
```

#### `solamagic.write_handle_any`
Write to any Bluetooth handle (for advanced protocol exploration).

```yaml
service: solamagic.write_handle_any
target:
  device_id: # Select your heater
data:
  handle: 40  # Decimal handle (0x0028)
  payload_hex: "01 64"  # 100% command
  response: true
```

#### `solamagic.write_uuid`
Write to characteristic by UUID (alternative method).

```yaml
service: solamagic.write_uuid
target:
  device_id: # Select your heater
data:
  char_uuid: "0000f001-0000-1000-8000-00805f9b34fb"
  payload_hex: "01 21"  # 33% command
  response: false
```

#### `solamagic.disconnect`
Manually disconnect from heater (useful for troubleshooting or to allow app access).

```yaml
service: solamagic.disconnect
target:
  device_id: # Select your heater
```

## Automation Examples

### Basic Temperature Control
```yaml
automation:
  - alias: "Patio heater on when cold"
    trigger:
      - platform: numeric_state
        entity_id: sensor.outdoor_temperature
        below: 15
    action:
      - service: solamagic.set_level
        target:
          device_id: YOUR_DEVICE_ID
        data:
          level: 66
```

### Smart Heating Schedule
```yaml
automation:
  - alias: "Evening patio heating"
    trigger:
      - platform: time
        at: "18:00:00"
    condition:
      - condition: numeric_state
        entity_id: sensor.outdoor_temperature
        below: 20
    action:
      - service: solamagic.set_level
        target:
          device_id: YOUR_DEVICE_ID
        data:
          level: 100
      - delay:
          hours: 3
      - service: solamagic.set_level
        target:
          device_id: YOUR_DEVICE_ID
        data:
          level: 0
```

### Presence-Based Heating
```yaml
automation:
  - alias: "Heater on when people on patio"
    trigger:
      - platform: state
        entity_id: binary_sensor.patio_motion
        to: "on"
    action:
      - service: solamagic.set_level
        target:
          device_id: YOUR_DEVICE_ID
        data:
          level: 66
```

## Bluetooth Protocol

This integration uses reverse-engineered Bluetooth protocol from the xHeatlink app:

- **Initialization:** Sends unlock sequence to handle 0x001F
- **Commands:** Sent to handle 0x0028 without response
- **Status:** Received via notifications on handle 0x0032
- **Power Levels:**
  - OFF: `00 21` (sent 21 times with 16ms delay)
  - 33%: `01 21`
  - 66%: `01 42`
  - 100%: `01 64`

### Protocol Details
The integration follows this initialization sequence:
1. Write initialization payload to handle 0x001F
2. Enable notifications on handle 0x002F (CCCD 0x0030)
3. Enable notifications on handle 0x0032 (CCCD 0x0033)
4. Enable notifications on handle 0x0028 (CCCD 0x0029)

All commands use Write Command (no response) for faster execution.

## Troubleshooting

### Heater not discovered

- Ensure Bluetooth is enabled in Home Assistant
- Check that the heater is powered on and within range
- Try restarting Home Assistant
- Verify your device MAC address starts with `D0:65:4C:`

### Connection issues

- The integration auto-disconnects after 3 minutes to allow xHeatlink app access
- If you need to use the app, wait for auto-disconnect or use the `solamagic.disconnect` service
- Check signal strength sensor - poor RSSI (<-80 dBm) may cause issues
- If using Bluetooth proxy, ensure it's online and within range

### Status not updating

- Check if Power Level sensor is updating
- Verify Connection Status sensor shows "connected"
- Enable debug logging:
  ```yaml
  logger:
    logs:
      custom_components.solamagic: debug
  ```
- Check Home Assistant logs for errors

### Service calls failing

If you see "Device not found" errors:
1. Go to **Developer Tools** → **Services**
2. Select your service (e.g., `solamagic.set_level`)
3. Use the **device selector** dropdown to choose your heater
4. This is easier than manually entering `entry_id`

### Finding your device in automations

1. When creating an automation, add an action
2. Choose "Call service"
3. Search for "Solamagic"
4. Use the device selector to pick your heater
5. The UI will automatically fill in the correct `device_id`

## FAQ

**Q: Can I control multiple heaters?**  
A: Yes! Add each heater separately, and use the device picker to select which one to control.

**Q: Does it work with Bluetooth proxies?**  
A: Yes! Works great with ESPHome Bluetooth proxies. The integration is proxy-aware.

**Q: Why does it disconnect after 3 minutes?**  
A: To allow the xHeatlink mobile app to connect. You can adjust this timeout if needed.

**Q: Can I use both the app and Home Assistant?**  
A: Yes, but not simultaneously. The integration disconnects after 3 minutes to allow app access.

**Q: What's the difference between preset modes and service calls?**  
A: Preset modes (Low/Medium/High) are the easiest way to control via the UI. Services offer more flexibility for automations.

## Development

### Code Quality
This integration follows Home Assistant best practices:
- Proper error handling with user-friendly messages
- Async/await patterns for non-blocking operations
- Comprehensive logging for debugging
- Device picker support for easy service calls
- English code comments for international collaboration

### Contributing
Found a bug or want to add a feature? 
- Check existing [issues](https://github.com/Pocke01/homeassistant-solamagic/issues)
- Fork the repository
- Create a feature branch
- Submit a pull request

## Credits

- Protocol analysis based on Bluetooth sniffing of the xHeatlink app
- Inspired by reverse engineering work on similar BLE devices
- Big thanks to ChatGPT and Claude.ai for helping with endless questions and suggestions!
- Thanks to the Home Assistant community for code reviews and feedback

## Disclaimer

I do not know Python or how the Bluetooth protocol works. But I know how to solve problems! 😊

This integration is not affiliated with or endorsed by Solamagic. Use at your own risk.

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Support

For issues and feature requests, please use the [GitHub issue tracker](https://github.com/Pocke01/homeassistant-solamagic/issues).

---

**Version:** 0.3.25  
**Status:** Production-ready, HACS-compatible  
**Home Assistant:** 2024.1.0+
