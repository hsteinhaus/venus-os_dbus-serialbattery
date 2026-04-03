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

Port numbering can change after reboot. Use udev rules to pin services.
As of 2026-04-01, JKBMS is on ttyUSB0 (was ttyUSB1 previously).

| Port | USB ID | Chip | Use |
|------|--------|------|-----|
| ttyUSB0 | `usb-1a86_USB_Serial-if00-port0` | CH341 | JKBMS PB RS485 |
| ttyUSB1 | `usb-1a86_USB2.0-Ser_-if00-port0` | CH341 | modbus energy meter |

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
- Response: exactly 300 bytes starting with `0x55 0xAA 0xEB 0x90` header
- Byte 299 is sum8 checksum: `sum(bytes[0:299]) & 0xFF` (driver doesn't validate yet)
- Frame type at offset 4-5: 0x0002=status, 0x0001=settings, 0x0003=about
- **No battery address in the 0x55AA payload** — FC16 ACK (8 bytes, after the
  payload) contains the correct address
- Response header `0x55 0xAA` searched anywhere in buffer via `data.find()`
  (CH341 TX echo or write-ACK may shift it by a few bytes)
- See `bms-docs/JKBMS-PB.md` for full verified field map and protocol details.

## Serial Communication Architecture

- **Wakeup-and-drain is OBSOLETE** (2026-04-03): LA1010 captures proved the
  JKBMS Monitor software gets clean addressed responses with single FC16
  commands at ~800ms intervals, using the same CH341 adapter, no wakeup
  burst needed. The wakeup-and-drain rapid command bursts were the ROOT
  CAUSE of the cross-talk, not a fix for it. Should be removed.
- `_read_response(ser, command, length, timeout, no_data_timeout)`: core method,
  sends command on already-open port, custom read loop with fail-fast (bail after
  `no_data_timeout` if zero bytes received, default 250ms). Extends deadline by
  50ms on each received chunk (capped at 2× timeout) to handle BMS mid-response
  pauses. Returns False on truncated data instead of passing partial buffer.
- `read_serial_data_jkbms_pb()`: opens fresh port, delegates to `_read_response()`.
  Only used by `test_connection()` fallback path.
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

## Tested Stability (2026-04-01)

- 10/10 restarts: 0 errors, 0 warnings, all 4 batteries first try
- 1-hour endurance: 0 errors, 0 warnings, stable 1s poll interval
- Per-battery timing: ~150ms serial, ~3ms calc, ~20ms dbus (typical)
- External tester (Off-Grid-Garage): 9 batteries (V14/V15/V19 mix) stable
  with default timing (50ms+30ms). Occasional truncated responses on 0x08
  handled by soft deadline extension + truncation guard.

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
