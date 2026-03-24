# dbus-serialbattery Project Notes

## Project Overview

Venus OS driver (dbus-serialbattery) for communicating with JKBMS PB BMS devices over RS485.
Installed on Victron Cerbo GX at `ess.wallbox.home` (SSH as root, no password needed).

## Target Hardware / System

- Host: `ess.wallbox.home`
- Install path: `/data/apps/dbus-serialbattery`
- OS: Venus OS v3.70, armv7l, kernel 6.12.23-venus-8
- Python: 3.12.12

## Configuration (`config.ini` on Cerbo)

- `BMS_TYPE = Jkbms_pb`
- `BATTERY_ADDRESSES = 0x01, 0x02, 0x03, 0x04` (all 4 batteries connected)
- `LOGGING = INFO`
- Baud rate: 115200 (hardcoded in `dbus-serialbattery.py` for Jkbms_pb)

## USB Serial Ports

| Port | USB ID | Chip | Use |
|------|--------|------|-----|
| ttyUSB0 | `usb-1a86_USB2.0-Ser_-if00-port0` | CH341 | modbus energy meter — do not touch |
| ttyUSB1 | `usb-1a86_USB_Serial-if00-port0` | CH341 | JKBMS PB RS485 |

## Serial-Starter Integration (WORKING)

Config at `/data/conf/serial-starter.d/dbus-serialbattery.conf`:
```
service   sbattery    dbus-serialbattery
alias     default     gps:vedirect:sbattery
alias     rs485       cgwacs:fzsonick:imt:modbus:sbattery
```

ttyUSB1 is dedicated to sbattery via udev rule in `/etc/udev/rules.d/local.rules`:
```
SUBSYSTEM=="tty", ENV{ID_PATH}=="platform-1c1c400.usb-usb-0:1:1.0", ENV{VE_SERVICE}="sbattery"
```
This causes serial-starter to skip gps/vedirect and go straight to sbattery.

ttyUSB0 is left to modbus (`dbus-modbus-client.serial.ttyUSB0`).

## Deployment

Full deploy (committed files only):
```bash
git archive HEAD -- dbus-serialbattery/ | ssh root@ess.wallbox.home \
  "tar --strip-components=1 -xf - -C /data/apps/dbus-serialbattery/ \
   && echo $(git rev-parse HEAD) > /data/apps/dbus-serialbattery/.git-commit"
```
Quick deploy of single file (uncommitted ok):
```bash
scp dbus-serialbattery/bms/jkbms_pb.py root@ess.wallbox.home:/data/apps/dbus-serialbattery/bms/jkbms_pb.py
```
Restart service:
```bash
ssh root@ess.wallbox.home "svc -t /service/dbus-serialbattery.ttyUSB1"
```
Use `tai64nlocal` to decode log timestamps, e.g.:
```bash
tail -F /var/log/dbus-serialbattery.ttyUSB1/current | tai64nlocal
```

## Protocol Details (Jkbms_pb)

- Modbus RTU framing; command format: `address + command_bytes + modbusCRC`
- Response header: `0x55 0xAA` — searched anywhere in buffer (not fixed at offset 0)
- BMS fw >= v15.36: `command_status` only responds when preceded by another command
  in the same rapid burst (< ~200ms gap). `refresh_data()` sends `command_settings`
  as a write-only wake-up (100ms settle, flush RX), then `command_status`.
- Multi-battery RS485 bus: Modbus 0x10 write-ACK prepended to BMS response, shifting
  `0x55 0xAA` header by a few bytes → fixed by `data.find(b"\x55\xaa")` in
  `_read_response()`.
- CH341 TX echo: TX bytes echo into RX buffer on half-duplex adapters;
  `_read_response()` scans for 0x55AA header past the echo bytes.
- Response: ~308-315 bytes (vs. 299/300 in `length_fixed`); fine since inner loop
  exits as soon as data exceeds `length_fixed`.
- BMS needs ~100ms gap between back-to-back commands in the same serial session
  (e.g. `command_settings` → `command_about` in `get_settings()`).

## Serial Communication Architecture

- `_read_response(ser, command, length, timeout, no_data_timeout)`: core method,
  sends command on already-open port, custom read loop with fail-fast (bail after
  `no_data_timeout` if zero bytes received, default 250ms).
- `read_serial_data_jkbms_pb()`: opens fresh port, delegates to `_read_response()`.
  Only used by `test_connection()` fallback path.
- `refresh_data()`: single serial session — open port once, write-only wake-up
  (75ms settle + flush), then `_read_response()` for status. One automatic retry
  on failure (wake-up still warm). Timeout 0.5s (BMS responds in ~200ms).
- `get_settings()`: single serial session for both `command_settings` (1.0s timeout)
  and `command_about` (1.0s timeout) with 100ms gap + RX flush between them.
  20ms CH341 drain + internal retry before first command.
- Polling 4 batteries takes ~700-800ms (down from ~2.7s with separate sessions).
- No longer uses `read_serialport_data()` from utils.py.
- EMA low-pass filter (alpha=0.3) on cell voltages suppresses 1mV ADC jitter,
  reducing actual dbus writes from ~100 to ~3-10 per battery per cycle.

## D-Bus Performance

- `_CachedDbusProxy` in `dbushelper.py` suppresses writes when value unchanged.
- `last_refresh_duration` tracked per battery in `dbushelper.py`; poll interval
  auto-increase uses refresh time only (not total runtime including dbus).
- dbus-python/GLib reentrancy: incoming GetValue requests pile up during serial
  reads and get processed in bursts when publish_dbus touches dbus objects.
  Causes occasional 1-2s stalls. Attempted fixes (deferred signals, ctx.iteration,
  threaded writers) all failed due to GLib single-threaded reentrancy.
  Current mitigation: decouple poll interval from dbus overhead.
- See `JKBMS-PB.md` for full analysis.

## Tested Stability (2026-03-24)

- 10/10 restarts: 0 errors, 0 warnings, all 4 batteries first try
- 1-hour endurance: 0 errors, 0 warnings, stable 1s poll interval
- Per-battery timing: ~150ms serial, ~3ms calc, ~20ms dbus (typical)

## Upstream / PR Workflow

- Upstream repo: `mr-manuel/venus-os_dbus-serialbattery`
- PRs submitted from `hsteinhaus` fork branches to upstream
- PR #425: burst protocol + header offset (branch `fix/jkbms-pb-multi-battery`)
- PR #428: perf + stability (branch `feature/jkbms-perf-and-stability`)
- CI (`.github/workflows/analyse.yml`): CodeQL + `psf/black@25.1.0` + flake8 + tests
- `black` line-length = 160 (see `pyproject.toml`); flake8 max-line-length = 160
- `git archive` only deploys committed files — run black before committing

## Local Development

Local repo: `/home/holger-local/syncthing/projects/hsteinhaus/venus-os_dbus-serialbattery/dbus-serialbattery/`
Mirrored to: `/home/holger-local/projects/hsteinhaus/venus-os_dbus-serialbattery/`

Driver code: `dbus-serialbattery/bms/jkbms_pb.py`
Diagnostic tool: `jkbms_pb_sniff.py` — standalone RS485 probe script
