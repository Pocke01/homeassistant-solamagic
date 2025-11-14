# Solamagic BT2000 for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/Pocke01/homeassistant-solamagic.svg)](https://github.com/Pocke01/homeassistant-solamagic/releases)

Home Assistant custom integration for Solamagic BT2000 Bluetooth infrared patio heaters.

## Features

- ✅ **Climate Entity** - Control heater with preset modes (33%, 66%, 100%)
- ✅ **Real-time Status** - Instant updates when changing power level
- ✅ **Sensors** - Power level, RSSI signal strength, connection status
- ✅ **Bluetooth Proxy Support** - Works with ESPHome Bluetooth proxies
- ✅ **Auto-disconnect** - Automatically disconnects after 3 minutes to allow app access
- ✅ **Services** - Advanced control via Home Assistant services

## Supported Devices

- Solamagic BT2000 (Bluetooth-enabled infrared heaters)
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

Advanced control via services:

#### `solamagic.set_level`
Set heater to specific power level.

```yaml
service: solamagic.set_level
data:
  entry_id: YOUR_ENTRY_ID
  level: 66  # 0, 33, 66, or 100
```

#### `solamagic.write_handle`
Send raw Bluetooth commands (advanced).

```yaml
service: solamagic.write_handle
data:
  entry_id: YOUR_ENTRY_ID
  payload_hex: "01 42"  # 66% command
  response: false
```

## Bluetooth Protocol

This integration uses reverse-engineered Bluetooth protocol from the xHeatlink app:

- **Initialization:** Sends unlock sequence to handle 0x001F
- **Commands:** Sent to handle 0x0028 without response
- **Status:** Received via notifications on handle 0x0032
- **Power Levels:**
  - OFF: `00 21` (sent 21 times)
  - 33%: `01 21`
  - 66%: `01 42`
  - 100%: `01 64`

## Troubleshooting

### Heater not discovered

- Ensure Bluetooth is enabled in Home Assistant
- Check that the heater is powered on and within range
- Try restarting Home Assistant

### Connection issues

- The integration auto-disconnects after 3 minutes to allow xHeatlink app access
- If you need to use the app, wait for auto-disconnect or use the `solamagic.disconnect` service

### Status not updating

- Enable debug logging:
  ```yaml
  logger:
    logs:
      custom_components.solamagic: debug
  ```

## Credits

- Protocol analysis based on Bluetooth sniffing of xHeatlink app
- Inspired by reverse engineering work on similar BLE devices

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Support

For issues and feature requests, please use the [GitHub issue tracker](https://github.com/Pocke01/homeassistant-solamagic/issues).
