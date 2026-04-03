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

Wire protocol per command (verified by LA1010 captures, 2026-04-03):
```
TX: [ADDR(1)] [CMD(8)] [CRC(2)]                              = 11 bytes
RX: [echo(0-11)] [55AA EB90 ftype(2) data(294) cksum(1)] [00] [ACK(8)] [00]
                  \_________ payload: 300 bytes __________/     FC16 ACK
```
Total RX after 0x55AA header: 310 bytes (payload + pad + ACK + pad).

- Sum8 checksum at byte 299: `sum(bytes[0:299]) & 0xFF`
- Frame type at bytes 4-5: 0x0002=status, 0x0001=settings, 0x0003=about
- No battery address in the 0x55AA payload; the FC16 ACK contains it
- See `bms-docs/JKBMS-PB.md` for full verified field map

## Serial Communication Architecture

- Shared serial port kept open across all battery instances (`_shared_ser`)
- `_read_response(ser, command, timeout)`: sends FC16 command, reads all
  310 bytes after 0x55AA, validates checksum + frame marker + frame type +
  padding bytes + ACK (address, register, CRC) + total byte count
- `COMMAND_GAP` (default 0.1s, configurable via `[JKBMS_PB]` in config.ini):
  minimum gap between consecutive RS485 commands
- EMA low-pass filter (alpha=0.3) on cell voltages suppresses 1mV ADC jitter
- **Critical**: the read loop must consume ALL 310 bytes after the 0x55AA
  header. Breaking early (e.g., after 300 bytes) leaves the ACK + padding
  in the CH341 FIFO, which corrupts the next command's response.

## D-Bus Performance

- `_CachedDbusProxy` in `dbushelper.py` suppresses writes when value unchanged.
- `last_refresh_duration` tracked per battery in `dbushelper.py`; poll interval
  auto-increase uses refresh time only (not total runtime including dbus).

## Tested Stability (2026-04-03, deterministic driver)

- 4/4 batteries detected first try, zero errors, zero warnings
- All responses validated: checksum, frame marker, frame type, ACK CRC
- Zero stale bytes between commands (all 310 RX bytes consumed)
- Verified by LA1010: zero cross-talk, all ACKs from correct addresses

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
