# Tacx Flow Bluetooth Scripts

Bluetooth LE (BLE) Python scripts for Tacx Flow Smart trainers (FTMS/ERG-capable).

## Features

- `tacx-180` – Set the trainer to a fixed wattage (default 180 W)
- `tacx-watt` – Live display of current power and speed with 28-inch wheel calculation

## Installation

```bash
git clone https://github.com/jkbutterfinger/tacx-flow-bluetooth.git
cd tacx-flow-bluetooth
python3 -m venv venv
venv/bin/pip install bleak
```

Create executable wrappers in `~/.local/bin/`:

```bash
# tacx-180
cat > ~/.local/bin/tacx-180 << 'SCRIPT'
#!/usr/bin/env bash
cd "$(dirname "$0")/../work/tacx-flow-bluetooth"
exec ./venv/bin/python tacx_180.py "$@"
SCRIPT
chmod +x ~/.local/bin/tacx-180

# tacx-watt
cat > ~/.local/bin/tacx-watt << 'SCRIPT'
#!/usr/bin/env bash
cd "$(dirname "$0")/../work/tacx-flow-bluetooth"
exec ./venv/bin/python tacx_watt.py "$@"
SCRIPT
chmod +x ~/.local/bin/tacx-watt
```

## Usage

### Set fixed power
```bash
tacx-180          # sets 180 W
tacx-180 200      # sets 200 W
tacx-180 --list   # list nearby BLE devices
```

The script must stay running to keep ERG mode active. Press Ctrl+C to exit.

### Live power + speed display
```bash
tacx-watt                  # live power and 28-inch wheel speed (2.136 m)
tacx-watt --wheel-circumference 2.20   # custom wheel circumference
tacx-watt --duration 300   # runs for 5 minutes
tacx-watt --start          # start FTMS session if needed
```

## Requirements

- **Linux** (tested on Debian/Ubuntu with BlueZ)
- **Bluetooth adapter** with BLE support
- **Python 3.12** or newer with `bleak` 3.0.2+
- **Tacx Flow Smart (T2240)** for FTMS power control
- Any FTMS or Cycling Power trainer for live display

## Notes

- The Tacx Flow Smart (T2240) supports FTMS power control (ERGM).
- Older Tacx Flow models (T1680/T1690) cannot be electronically controlled.
- If `tacx-180` fails with "no FTMS control point", your trainer doesn't support FTMS/ERG.
- Live speed calculation works with any trainer that sends wheel revolution data.

## License

Public domain. Use at your own risk.
