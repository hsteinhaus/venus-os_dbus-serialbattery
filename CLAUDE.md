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

As of 2026-04-28 both RS485 devices share a single FTDI Dual RS232-HS
adapter (FT2232H, 0403:6010), at platform path `1c1c000.usb-usb-0:1:1.x`.

| Port | USB ID | Iface | Use |
|------|--------|-------|-----|
| ttyUSB0 | `usb-FTDI_Dual_RS232-HS-if00-port0` | 0 (port A) | JKBMS PB RS485 |
| ttyUSB1 | `usb-FTDI_Dual_RS232-HS-if01-port0` | 1 (port B) | Modbus energy meter (PRO380-Mod) |

## Serial-Starter Integration (WORKING)

Config at `/data/conf/serial-starter.d/dbus-serialbattery.conf`:
```
service   sbattery    dbus-serialbattery
alias     default     gps:vedirect:sbattery
alias     rs485       cgwacs:fzsonick:imt:modbus:sbattery
```

The FTDI dual chip enumerates with `ID_MODEL=Dual_RS232-HS`, but Venus's
`/etc/udev/rules.d/serial-starter.rules` ships a typo'd match string
(`"FTDI Dual RS232-HS"`). We patched it in place to `"Dual_RS232-HS"` so
both interfaces get `VE_SERVICE=rs485:default` and the rs485 alias
sorts each port onto its proper service:
- ttyUSB0 → sbattery (BMS)
- ttyUSB1 → dbus-modbus-client.serial (meter, registers as `com.victronenergy.pvinverter.cg_572068425`)

Backup: `/etc/udev/rules.d/serial-starter.rules.bak`.
Persistence across Venus updates not yet confirmed (the `/data/serial-starter.rules`
copy doesn't contain the rule at all — patch may need re-applying).

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
Restart service (BMS = ttyUSB0 since 2026-04-28):
```bash
ssh root@ess.wallbox.home "svc -t /service/dbus-serialbattery.ttyUSB0"
```
Use `tai64nlocal` to decode log timestamps, e.g.:
```bash
tail -F /var/log/dbus-serialbattery.ttyUSB0/current | tai64nlocal
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

## Test Rigs

Concrete hardware/network details that are *not* generic JKBMS PB doc.
Generic adapter compatibility findings live in `bms-docs/JKBMS-PB.md`.

| Rig | Host | Adapter | BMS | Notes |
|-----|------|---------|-----|-------|
| Our Cerbo | `ess.wallbox.home` (Venus OS v3.70, armv7) | FT2232H dual, isolated (Waveshare B0D7BLNG75, port A) | 4× JKBMS PB at addr 0x01–0x04 | Port B of same adapter carries SolarLog PRO380-Mod meter via dbus-modbus-client.serial |
| Andy's RPi | `10.22.10.52` (RPi 3B+, mainline Linux) | CH340 USB + Waveshare RS485↔TCP bridge (`socat` to `/dev/ttyVx`) | 12× JKBMS PB | Two parallel buses (USB + TCP), 8+4 split |

### LA1010 logic analyzer (local debug only)

- Device: Kingst LA1010 (USB ID `77a1:01a2`), physically attached to
  `ess.wallbox.home`
- Capture script: `~/syncthing/projects/hsteinhaus/la1010/la1010_capture.py`,
  run on the Cerbo via SSH
- Channel 0: RS485 **A+** (single-ended, referenced to bus GND)
- B− not probed — single-ended on A+ suffices at 115200 baud
- Threshold: 2.0 V (A+ idles high ≥ +200 mV; falling edges = start bits)
- Sample rate: 500 kHz (≈4× oversampling at 115200)
- Capture: `python3 la1010_capture.py --channels 0 --duration 5 --threshold 2.0`
- Decode: sigrok UART decoder, 115200 8N1, no parity

FT232R failure data: `debug.txt` (2026-04-28) from Andy's RPi 3B+ rig.
