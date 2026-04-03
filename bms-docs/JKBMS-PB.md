# JKBMS PB RS485 — Wire Protocol Reference

This document describes the RS485 protocols used by JKBMS PB series BMS
units.  Based on the official JKBMS RS485 Modbus V1.0/V1.1 spec,
reverse-engineered driver code, and protocol-level testing on a
4-battery system (2026-04-02 through 2026-04-03).

## Hardware

- BMS: JKBMS PB series (tested with firmware V15.x and V19.x)
- RS485 adapter: factory-supplied JKBMS comm adapter (CH341-based USB-serial)
- Bus: multi-drop RS485, half-duplex, 115200 baud, 8N1
- Addressing: each BMS has a DIP-switch configurable address (0x01–0xFF);
  address 0x00 configures the BMS as bus master

## Three Protocols on One Bus

The JKBMS PB supports **three distinct protocols** on the same RS485 bus.
The current `dbus-serialbattery` driver uses Protocol B.

### Protocol A: Standard Modbus RTU (official, documented)

Defined in "极空BMS RS485 Modbus通用协议 V1.0/V1.1". Uses standard
Modbus RTU framing with FC 0x03 (Read Holding Registers) and FC 0x10
(Write Multiple Registers).

**Request** (FC 0x03 read):

    [ ADDR (1) ] [ 0x03 ] [ START_REG (2) ] [ REG_COUNT (2) ] [ CRC (2) ]

Total: 8 bytes.

**Response** (FC 0x03):

    [ ADDR (1) ] [ 0x03 ] [ BYTE_COUNT (1) ] [ DATA (N) ] [ CRC (2) ]

Standard Modbus framing.  Big-endian register values (per Modbus spec).

**Properties** (tested 2026-04-02, external host, no bus master present):
- ✅ Address filtering works — only the addressed BMS responds
- ✅ CRC is checked — corrupted CRC gets no response
- ✅ No cross-talk — unaddressed BMS units stay silent
- ⚠️ Max register count per read appears limited (~40 works, 128 fails)

**Register base addresses** (Modbus register addresses, not byte offsets):

| Base     | Content                        | Access |
|----------|--------------------------------|--------|
| 0x1000   | Settings/configuration         | RW     |
| 0x1200   | Status/telemetry (live data)   | R      |
| 0x1400   | Device info (version, serial)  | R      |
| 0x1600   | Calibration/control            | W      |

**Verified FC 0x03 reads** (test D, 2026-04-02):
- `01 03 1200 000A crc` → response `01 03 14 0d0d 0d0e 0d0d...` —
  10 status registers (cell voltages in mV, big-endian)
- `01 03 1400 000A crc` → response `01 03 14 4a4b5f504232...` —
  10 about registers ("JK_PB2A16S20P...15A")
- `04 03 1200 000A crc` → response from addr 0x04, cells=[3341,3341,...]
  (correct address filtering confirmed)

### Protocol B: Proprietary 0x55AA (used by driver)

The current `dbus-serialbattery` driver uses FC 0x10 writes to specific
registers in the 0x1600 area as "triggers".  The BMS responds with a
proprietary binary format starting with magic header `0x55 0xAA`.

**Request** (FC 0x10 trigger):

    [ ADDR (1) ] [ COMMAND (8) ] [ CRC (2) ]

Total: 11 bytes.  The 8-byte command body is a Modbus FC 0x10 write
(function code + register address + register count + byte count + data).

| Trigger name | Command bytes (hex)       | Register   | Response frame type |
|--------------|---------------------------|------------|---------------------|
| `status`     | `10 16 20 00 01 02 00 00` | 0x1620     | 0x0002              |
| `settings`   | `10 16 1e 00 01 02 00 00` | 0x161E     | 0x0001              |
| `about`      | `10 16 1c 00 01 02 00 00` | 0x161C     | (not observed)      |

**Response** (proprietary):

    [ TX echo (0-5) ] [ 0x55 0xAA 0xEB 0x90 ] [ frame_type (2) ] [ payload ]

- `0x55 0xAA` header must be found by scanning (variable offset due to
  TX echo and Modbus write-ACK bytes)
- `0xEB 0x90` follows the magic header in all observed responses
- Payload is **little-endian** (unlike standard Modbus which is big-endian)
- Frame is exactly **300 bytes** (offsets 0–299 from `0x55 0xAA`).
  Byte 299 is a checksum: `sum(bytes[0:299]) & 0xFF`.  Verified on
  all 4 batteries (3 slaves + master), both status and settings
  responses, across multiple cycles.  Bytes after 299 are noise or
  next-command bleed from the bus.

**Offset 4–5** is a frame type identifier, NOT the responding battery
address (all batteries return the same value: 0x0002 for status, 0x0001
for settings).  Confirmed by observing all four batteries (three slaves
at addr 1–3 and the bus master at addr 0) all returning 0x0002 for
status.  The 0x55AA payload contains **no battery address field** —
there is no byte in the response that identifies which battery sent it.

**Address filtering:** When triggered by a BMS bus master (Protocol C),
address filtering works correctly — see below.  When triggered by an
external host (CH341 adapter), cross-talk was observed in earlier tests
(2026-04-02) where all 4 batteries were configured as regular slaves
(no bus master present).  It is unclear whether the cross-talk was
caused by Protocol B itself or by the absence of a bus master on the
bus.  See "Bus Behaviour" section for details.

### Protocol C: BMS-to-BMS Master Polling (bus master mode)

When a BMS is configured with DIP-switch address 0x00, it becomes the
**bus master** and continuously polls all other batteries on the RS485
bus.  Captured and decoded 2026-04-03 using LA1010 logic analyzer
(single-ended, A+ vs GND, 500 kHz, 2.0V threshold).

**Transport:** Modbus RTU FC 0x10 (Write Multiple Registers) — the
same trigger commands as Protocol B, but with correct address filtering.

**Request→Response sequence** for each active battery:

    Master TX:  [ ADDR ] [ 10 16 20 00 01 02 00 00 ] [ CRC ]   (11 bytes)
    Slave TX:   [ 0x55 0xAA 0xEB 0x90 ... ]                     (300 bytes, proprietary)
    Slave TX:   [ ADDR ] [ 10 16 20 00 01 ] [ CRC ]             (8 bytes, FC16 ACK)

The slave transmits the proprietary 0x55AA payload first, then the
standard FC 0x10 ACK.  The master uses the ACK as a delimiter.

**Cycle structure** (observed with 3 active batteries at addr 1–3):

| Phase | Duration | Description |
|-------|----------|-------------|
| Active poll: bat 1 | ~260ms | 0x1620 req→ACK, 0x161E req→ACK |
| Active poll: bat 2 | ~260ms | same |
| Active poll: bat 3 | ~260ms | same |
| Discovery scan: addr 4–15 | ~2640ms | 0x1620 only, 220ms timeout each |
| **Total cycle** | **4400ms** | fixed period |

Per-battery timing: FC 0x10 request → ACK in ~38ms.  Between the ACK
and the next request, ~180ms gap.

**Registers polled:** only 0x1620 (status) and 0x161E (settings), every
cycle.  0x161C (about) was never observed in 60 seconds of capture.
Discovery scan addresses use only 0x1620.

**Addressing: ✅ correct.**  Verified over 60 seconds (14 full cycles):
- FC 0x10 ACKs arrive only from the addressed battery (CRC-verified)
- Exactly one 0x55AA response per request, between request and ACK
- Zero 0x55AA cross-talk in post-ACK windows
- Zero 0x55AA cross-talk in non-existent address windows (addr 4–14)
- Batteries 1–3 do NOT respond to requests addressed to other batteries

**Master self-broadcast:** During the addr=15 → addr=1 gap (~660ms),
the master broadcasts its own status (ftype=2) and settings (ftype=1)
as 0x55AA frames.  These appear once per cycle.

**Bus utilisation:** The bus is driven at ~70% utilisation (8 KB/s)
continuously, even during scan windows where no battery responds.
Decoding at alternative baud rates (9600–460800) yields zero valid
frames — the continuous signal is not UART data at any standard rate.
The master's RS485 driver appears to remain enabled (not tri-stated),
producing garbled UART bytes between valid frames.

## Official Register Map (from JKBMS RS485 Modbus V1.0/V1.1)

All registers are 16-bit Modbus registers.  Multi-byte values span 2
registers (4 bytes).  In FC 0x03 responses, values are **big-endian**
(standard Modbus).  In proprietary 0x55AA responses, they are
**little-endian**.

### Settings Registers (base 0x1000, RW)

| Offset | Hex    | Type   | Unit  | Field                           |
|--------|--------|--------|-------|---------------------------------|
| 0      | 0x0000 | UINT32 | mV    | VolSmartSleep                   |
| 4      | 0x0004 | UINT32 | mV    | VolCellUV (cell undervoltage)   |
| 8      | 0x0008 | UINT32 | mV    | VolCellUVPR (UV recovery)       |
| 12     | 0x000C | UINT32 | mV    | VolCellOV (cell overvoltage)    |
| 16     | 0x0010 | UINT32 | mV    | VolCellOVPR (OV recovery)       |
| 20     | 0x0014 | UINT32 | mV    | VolBalanTrig (balance trigger)   |
| 24     | 0x0018 | UINT32 | mV    | VolSOC100% (SOC full voltage)   |
| 28     | 0x001C | UINT32 | mV    | VolSOC0% (SOC empty voltage)    |
| 32     | 0x0020 | UINT32 | mV    | VolCellRCV (charge voltage) ¹   |
| 36     | 0x0024 | UINT32 | mV    | VolCellRFV (float voltage) ¹    |
| 40     | 0x0028 | UINT32 | mV    | VolSysPwrOff (auto shutdown)    |
| 44     | 0x002C | UINT32 | mA    | CurBatCOC (charge current limit)|
| 48     | 0x0030 | UINT32 | s     | TIMBatCOCPDly (charge OCP delay)|
| 52     | 0x0034 | UINT32 | s     | TIMBatCOCPRDly (charge OCP recovery)|
| 56     | 0x0038 | UINT32 | mA    | CurBatDcOC (discharge current)  |
| 60     | 0x003C | UINT32 | s     | TIMBatDcOCPDly                  |
| 64     | 0x0040 | UINT32 | s     | TIMBatDcOCPRDly                 |
| 68     | 0x0044 | UINT32 | s     | TIMBatSCPRDly (SCP recovery)    |
| 72     | 0x0048 | UINT32 | mA    | CurBalanMax (max balance current)|
| 76     | 0x004C | INT32  | 0.1°C | TMPBatCOT (charge overtemp)     |
| 80     | 0x0050 | INT32  | 0.1°C | TMPBatCOTPR                     |
| 84     | 0x0054 | INT32  | 0.1°C | TMPBatDcOT (discharge overtemp) |
| 88     | 0x0058 | INT32  | 0.1°C | TMPBatDcOTPR                    |
| 92     | 0x005C | INT32  | 0.1°C | TMPBatCUT (charge undertemp)    |
| 96     | 0x0060 | INT32  | 0.1°C | TMPBatCUTPR                     |
| 100    | 0x0064 | INT32  | 0.1°C | TMPMosOT (MOS overtemp)         |
| 104    | 0x0068 | INT32  | 0.1°C | TMPMosOTPR                      |
| 108    | 0x006C | UINT32 | —     | CellCount                       |
| 112    | 0x0070 | UINT32 | —     | BatChargeEN (1=on)              |
| 116    | 0x0074 | UINT32 | —     | BatDisChargeEN (1=on)           |
| 120    | 0x0078 | UINT32 | —     | BalanEN (1=on)                  |
| 124    | 0x007C | UINT32 | mAh   | CapBatCell (design capacity)    |
| 128    | 0x0080 | UINT32 | µs    | SCPDelay                        |
| 132    | 0x0084 | UINT32 | mV    | VolStartBalan                   |
| 136–260| 0x0088–0x0104 | UINT32 | µΩ | CellConWireRes0–31 (wire resistance) |
| 264    | 0x0108 | UINT32 | —     | DevAddr (device address)        |
| 268    | 0x010C | UINT32 | s     | TIMProdischarge                 |
| 276    | 0x0114 | UINT16 | —     | Control bitmask (see below)     |
| 278    | 0x0116 | INT8×2 | °C    | TMPBatOTA / TMPBatOTAR          |
| 280    | 0x0118 | UINT8×2| —     | TIMSmartSleep (hours) / data ctrl |

¹ V1.1 adds VolCellRCV (0x0020) and VolCellRFV (0x0024); V1.0 has
  VolSysPwrOff at 0x0028 instead.

**Control bitmask** (offset 276 / register 0x0114):

| Bit | Function                              |
|-----|---------------------------------------|
| 0   | HeatEN (heating enabled)              |
| 1   | Disable temp-sensor                   |
| 2   | GPS Heartbeat                         |
| 3   | Port Switch (1=RS485, 0=CAN)          |
| 4   | LCD Always On                         |
| 5   | Special Charger                       |
| 6   | SmartSleep                            |
| 7   | DisablePCLModule (V1.1 only)          |
| 8   | TimedStoredData (V1.1 only)           |
| 9   | ChargingFloatMode (V1.1 only)         |

### Status Registers (base 0x1200, R)

| Offset | Hex    | Type   | Unit  | Field                           |
|--------|--------|--------|-------|---------------------------------|
| 0–62   | 0x0000–0x003E | UINT16 | mV | CellVol0–31                |
| 64     | 0x0040 | UINT32 | bit   | CellSta (cell presence bitmask) |
| 68     | 0x0044 | UINT16 | mV    | CellVolAve (average)            |
| 70     | 0x0046 | UINT16 | mV    | CellVdifMax (max delta)         |
| 72     | 0x0048 | UINT8×2| —     | MaxVolCellNbr / MinVolCellNbr   |
| 74–136 | 0x004A–0x0088 | UINT16 | mΩ | CellWireRes0–31            |
| 138    | 0x008A | INT16  | 0.1°C | TempMos                         |
| 140    | 0x008C | UINT32 | bit   | CellWireResSta                  |
| 144    | 0x0090 | UINT32 | mV    | BatVol (pack voltage)           |
| 148    | 0x0094 | UINT32 | mW    | BatWatt (pack power)            |
| 152    | 0x0098 | INT32  | mA    | BatCurrent (signed)             |
| 156    | 0x009C | INT16  | 0.1°C | TempBat1                        |
| 158    | 0x009E | INT16  | 0.1°C | TempBat2                        |
| 160    | 0x00A0 | UINT32 | bit   | Alarm bitmask (see below)       |
| 164    | 0x00A4 | INT16  | mA    | BalanCurrent                    |
| 166    | 0x00A6 | UINT8×2| —     | BalanSta (2=discharge,1=charge,0=off) / SOC (%) |
| 168    | 0x00A8 | INT32  | mAh   | SOCCapRemain                    |
| 172    | 0x00AC | UINT32 | mAh   | SOCFullChargeCap                |
| 176    | 0x00B0 | UINT32 | —     | SOCCycleCount                   |
| 180    | 0x00B4 | UINT32 | mAh   | SOCCycleCap                     |
| 184    | 0x00B8 | UINT8×2| —     | SOCSOH (%) / Precharge (1=on)   |
| 188    | 0x00BC | UINT32 | s     | RunTime                         |
| 192    | 0x00C0 | UINT8×2| —     | Charge (1=on) / Discharge (1=on)|
| 208    | 0x00D0 | UINT8×2| bit   | TempSensor presence / Heating   |
| 228    | 0x00E4 | UINT16 | 0.01V | BatVol (alternate)              |
| 230    | 0x00E6 | INT16  | mA    | HeatCurrent                     |
| 248    | 0x00F8 | INT16  | 0.1°C | TempBat3                        |
| 250    | 0x00FA | INT16  | 0.1°C | TempBat4                        |
| 252    | 0x00FC | INT16  | 0.1°C | TempBat5                        |

**Alarm bitmask** (offset 160 / register 0x00A0, UINT32):

| Bit  | Alarm                          |
|------|--------------------------------|
| 0    | AlarmWireRes (wire resistance)  |
| 1    | AlarmMosOTP                    |
| 2    | AlarmCellQuantity              |
| 3    | AlarmCurSensorErr              |
| 4    | AlarmCellOVP                   |
| 5    | AlarmBatOVP                    |
| 6    | AlarmChOCP (charge overcurrent)|
| 7    | AlarmChSCP (charge short-circuit)|
| 8    | AlarmChOTP (charge overtemp)   |
| 9    | AlarmChUTP (charge undertemp)  |
| 10   | AlarmCPUAuxCommuErr            |
| 11   | AlarmCellUVP                   |
| 12   | AlarmBatUVP                    |
| 13   | AlarmDchOCP                    |
| 14   | AlarmDchSCP                    |
| 15   | AlarmDchOTP                    |
| 16   | AlarmChargeMOS                 |
| 17   | AlarmDischargeMOS              |
| 18   | GPSDisconnected                |
| 19   | Modify PWD in time             |
| 20   | Discharge On Failed            |
| 21   | Battery Over Temp Alarm        |
| 22   | Temperature sensor anomaly (V1.1)|
| 23   | PLCModule anomaly (V1.1)       |

**Temperature sensor presence** (offset 208, first UINT8):

| Bit | Sensor                    |
|-----|---------------------------|
| 0   | MOS TempSensorAbsent      |
| 1   | BATTempSensor1Absent      |
| 2   | BATTempSensor2Absent      |
| 3   | BATTempSensor3Absent (V1.1)|
| 4   | BATTempSensor4Absent      |
| 5   | BATTempSensor5Absent      |

(1 = sensor present/normal, 0 = absent)

### Device Info Registers (base 0x1400, R)

| Offset | Hex    | Type   | Field                    |
|--------|--------|--------|--------------------------|
| 0      | 0x0000 | ASCII  | ManufacturerDeviceID (16 chars) |
| 16     | 0x0010 | ASCII  | HardwareVersion (8 chars)|
| 24     | 0x0018 | ASCII  | SoftwareVersion (8 chars)|
| 32     | 0x0020 | UINT32 | ODDRunTime (seconds)     |
| 36     | 0x0024 | UINT32 | PWROnTimes               |

### Calibration/Control Registers (base 0x1600, W)

| Offset | Type   | Field                    |
|--------|--------|--------------------------|
| 0      | UINT16 | VoltageCalibration (mV)  |
| 4      | UINT16 | Shutdown                 |
| 6      | UINT16 | CurrentCalibration (mA)  |
| 10     | UINT16 | LI-ION preset            |
| 12     | UINT16 | LIFEPO4 preset           |
| 14     | UINT16 | LTO preset               |
| 16     | UINT16 | Emergency start          |
| 18     | UINT32 | Timecalibration          |

## Proprietary 0x55AA Response Mapping

The proprietary responses use **different byte offsets** from the official
register map because they include a proprietary header and frame metadata.
All offsets below are from the `0x55 0xAA` header.  Values are
**little-endian** (opposite of standard Modbus).

**Offset rule:** For both status and settings responses, the 0x55AA
payload offset = official register byte offset + 6 (accounting for the
4-byte header `55 AA EB 90` + 2-byte frame type).  This means the
official register maps above can be used directly by adding 6 to each
byte offset.

### Status (trigger 0x1620) — verified field map

Cross-validated against 3 batteries (addr 1–3) with known physical state:
16S LiFePO4, 280 Ah design capacity, indoor ~20°C, idle/no load.
Captured 2026-04-03.

**Confidence levels:**
- **V** = Verified by cross-battery comparison and physical plausibility
- **D** = Matches driver code (may not be independently verified)
- **?** = Unidentified; values observed but purpose unknown

| Offset | Size | Type    | Conf | Field / Interpretation |
|--------|------|---------|------|------------------------|
| 0–1    | 2    | —       | V | Magic header `0x55 0xAA` |
| 2–3    | 2    | —       | V | Frame marker `0xEB 0x90` (constant) |
| 4–5    | 2    | uint16  | V | Frame type: 0x0002=status, 0x0001=settings. **Not** the battery address. |
| 6+2n   | 2    | uint16  | V | Cell voltage [n] in mV (n=0..15 for 16S). ÷1000 for volts. |
| 38–69  | 32   | —       | V | Unused cell slots 17–32 (all zeros on 16S) |
| 70–73  | 4    | uint32  | V | Cell presence bitmask (0x0000FFFF for 16S) |
| 74–75  | 2    | uint16  | V | Max cell voltage (mV) |
| 76–77  | 2    | uint16  | V | Cell voltage delta (mV) |
| 78     | 1    | uint8   | ?  | Cell index field (meaning unclear) |
| 79     | 1    | uint8   | ?  | Cell index field (meaning unclear) |
| 80+2n  | 2    | uint16  | V | Wire resistance [n] in mΩ (n=0..15) |
| 112–143| 32   | —       | V | All zeros (unused wire resistance slots 17–32) |
| 144–145| 2    | int16   | V | TempMos: raw ÷ 10 = °C. See temperature encoding below. |
| 146–149| 4    | uint32  | D | Wire resistance status (0 = all ok) |
| 150–153| 4    | uint32  | V | Pack voltage in mV. ÷1000 for volts. |
| 154–157| 4    | uint32  | V | Pack power in mW (0 at idle) |
| 158–161| 4    | int32   | V | Pack current in mA (signed). ÷1000 for amps. |
| 162–163| 2    | int16   | V | TempBat1: raw ÷ 10 = °C |
| 164–165| 2    | int16   | V | TempBat2: raw ÷ 10 = °C |
| 166–169| 4    | uint32  | V | Alarm bitmask (same bits as status register 0x00A0) |
| 170–171| 2    | int16   | D | Balance current in mA |
| 172    | 1    | uint8   | D | Balance state (0=off, 1=charge, 2=discharge) |
| 173    | 1    | uint8   | V | SOC in %. Verified: 55% × 280 Ah = 154 Ah ≈ remaining cap. |
| 174–177| 4    | int32   | V | Remaining capacity in mAh. ÷1000 for Ah. |
| 178–181| 4    | uint32  | V | Design capacity in mAh (280000 = 280 Ah, matches config) |
| 182–185| 4    | uint32  | V | Charge cycle count (differs per battery: 57, 61, 62) |
| 186–189| 4    | uint32  | V | Cumulative cycle capacity in mAh. ≈ cycles × capacity. |
| 190    | 1    | uint8   | V | SOH in % (100 for all batteries) |
| 191    | 1    | uint8   | D | Precharge state |
| 194–197| 4    | uint32  | V | Total runtime in seconds (562–624 days, per battery) |
| 198    | 1    | uint8   | V | Charge FET state (1=on, same across batteries at idle) |
| 199    | 1    | uint8   | V | Discharge FET state (1=on) |
| 214    | 1    | uint8   | V | Temp sensor presence bitmask (0xFF = all present) |
| 215    | 1    | uint8   | D | Heating active (0/1) |
| 236–237| 2    | uint16  | D | Heater current in mA. ÷1000 for amps. |
| 254–255| 2    | int16   | V | Temperature — identical to [144] (MOS temp duplicate) |
| 256–257| 2    | int16   | V | TempBat3: raw ÷ 10 = °C. Driver reads at this offset. |
| 258–259| 2    | int16   | V | TempBat4: raw ÷ 10 = °C. Driver reads at this offset. |
| 286–297| 12   | —       | V | Constant footer (identical across all 4 batteries) |
| 298    | 1    | uint8   | ?  | Last per-battery field (varies between batteries) |
| 299    | 1    | uint8   | V | 8-bit checksum: `sum(bytes[0:299]) & 0xFF` |

**Unidentified offsets** (non-zero, vary between batteries):
192–193, 216–217, 220–221, 226–229, 234–235, 238–243, 246–249,
260–267, 270–273, 276–277, 298.

### Temperature encoding

The driver reads temperatures as `int16` (signed, little-endian) and
divides by 10 to get degrees Celsius:

```python
raw = unpack_from("<h", data, offset)[0] / 10
if raw < 99:
    temp_c = raw          # normal: 199 → 19.9°C
else:
    temp_c = 100 - raw    # negative: 1050 → 105.0 → 100-105 = -5°C
```

Observed values at ~20°C ambient: raw=187–207, giving 18.7–20.7°C.

### Settings (trigger 0x161E) — verified field map

Settings payload starts at offset 6 (after the 4-byte header + 2-byte
frame type).  Each field is a 32-bit little-endian value.  Offsets 6–138
map sequentially to the 0x1000 register map (offset 6 in the 0x55AA
frame = register offset 0 in the settings map).

Verified against 3 batteries (all identical settings):

| Offset | Register field  | Observed value | Interpretation |
|--------|-----------------|----------------|----------------|
| 6      | VolSmartSleep   | 3500           | 3.500 V |
| 10     | VolCellUV       | 2700           | 2.700 V |
| 14     | VolCellUVPR     | 2901           | 2.901 V |
| 18     | VolCellOV       | 3650           | 3.650 V |
| 22     | VolCellOVPR     | 3444           | 3.444 V |
| 26     | VolBalanTrig    | 5              | 5 mV |
| 30     | VolSOC100%      | 3445           | 3.445 V |
| 34     | VolSOC0%        | 2900           | 2.900 V |
| 38     | VolCellRCV      | 3450           | 3.450 V |
| 42     | VolCellRFV      | 3350           | 3.350 V |
| 46     | VolSysPwrOff    | 2500           | 2.500 V |
| 50     | CurBatCOC       | 60000          | 60 A |
| 54     | TIMBatCOCPDly   | 3              | 3 s |
| 58     | TIMBatCOCPRDly  | 60             | 60 s |
| 62     | CurBatDcOC      | 100000         | 100 A |
| 66     | TIMBatDcOCPDly  | 300            | 300 s |
| 70     | TIMBatDcOCPRDly | 60             | 60 s |
| 74     | TIMBatSCPRDly   | 5              | 5 s |
| 78     | CurBalanMax     | 2000           | 2 A |
| 82     | TMPBatCOT       | 350            | 35.0 °C |
| 86     | TMPBatCOTPR     | 320            | 32.0 °C |
| 90     | TMPBatDcOT      | 350            | 35.0 °C |
| 94     | TMPBatDcOTPR    | 320            | 32.0 °C |
| 98     | TMPBatCUT       | 50             | 5.0 °C |
| 102    | TMPBatCUTPR     | 70             | 7.0 °C |
| 106    | TMPMosOT        | 800            | 80.0 °C |
| 110    | TMPMosOTPR      | 700            | 70.0 °C |
| 114    | CellCount       | 16             | |
| 118    | BatChargeEN     | 1              | enabled |
| 122    | BatDisChargeEN  | 1              | enabled |
| 126    | BalanEN         | 1              | enabled |
| 130    | CapBatCell      | 280000         | 280 Ah |
| 134    | SCPDelay        | 1500           | 1500 µs |
| 138    | VolStartBalan   | 3440           | 3.440 V |

Offsets 142–269 contain wire resistance calibration values
(CellConWireRes0–31, 32 × 4 bytes = 128 bytes).

Higher offsets (derived from official register map, offset = register + 6):

| Offset | Register field  | Type   | Interpretation |
|--------|-----------------|--------|----------------|
| 270    | DevAddr         | uint32 | Device address (DIP switch) |
| 274    | TIMProdischarge | uint32 | Pre-discharge time (s) |
| 282    | Control bitmask | uint16 | See control bitmask table above |
| 284    | TMPBatOTA       | int8   | Heating start temp (°C) |
| 285    | TMPBatOTAR      | int8   | Heating stop temp (°C) |
| 286    | TIMSmartSleep   | uint8  | Smart sleep hours |

### About (trigger 0x161C) — driver offsets

Not observed in bus master captures (Protocol C does not use this
trigger).  These offsets are from the driver source only, unverified
by logic analyzer capture.

| Offset | Size | Type   | Field                    |
|--------|------|--------|--------------------------|
| 6      | 16   | ASCII  | ManufacturerDeviceID     |
| 22     | 8    | ASCII  | HardwareVersion          |
| 30     | 8    | ASCII  | SoftwareVersion          |
| 38     | 4    | uint32 | ODDRunTime (seconds)     |
| 42     | 4    | uint32 | PWROnTimes               |
| 46     | 16   | ASCII  | Serial number            |
| 102    | 16   | ASCII  | User data 1              |
| 118    | 16   | ASCII  | PIN                      |
| 134    | 16   | ASCII  | User data 2              |

## Bus Behaviour

### Cross-Talk Observations

**Protocol C (bus master):** No cross-talk.  Address filtering works
correctly — verified over 60 seconds / 14 full cycles (2026-04-03).

**Protocol A (FC 0x03 from external host):** No cross-talk.  Standard
Modbus address filtering works.  Tested 2026-04-02 with CH341 adapter,
all 4 batteries as regular slaves (no bus master).

**Protocol B (FC 0x10 trigger from external host):** Cross-talk was
observed in tests on 2026-04-02, with all 4 batteries configured as
regular slaves (no bus master present on the bus).  All BMS units
responded to every FC 0x10 trigger regardless of the address byte.
The addressed BMS responded within ~50ms; others sent late responses
200ms–5s later.

⚠️ **Caveat:** The Protocol B cross-talk was tested under different bus
conditions than Protocol C.  When the bus master was later introduced
(2026-04-03), the bus topology changed (continuous 70% bus utilisation,
master driving the line).  It is unknown whether Protocol B cross-talk
from an external host would still occur with a bus master present.
Possible explanations for the difference:
- The bus master's presence may change slave behaviour
- The slaves may filter differently based on who sent the trigger
- The CH341 adapter's signal characteristics may be a factor
- The absence of any master may put slaves in a promiscuous mode

The bus is completely silent during passive listening when no master
is present (verified: 5 rounds × 5 seconds = zero bytes, 2026-04-02).

### Why the Driver Uses Protocol B

The proprietary protocol was likely the original BMS interface, predating
the official Modbus spec. The driver was written against this interface.
Despite the cross-talk observed in external-host tests, it works in
practice because:
1. One BMS responds quickly (~50ms) with 300 bytes
2. The driver reads just enough data and stops
3. The wakeup-and-drain pattern consumes stale cross-talk before reads
4. Late cross-talk from other BMS units is drained between polls

Note: the 0x55AA payload does NOT contain the responding BMS's address
(offset 4–5 is a frame type, not an address; no address field exists
anywhere in the payload), so the driver cannot verify which battery
actually responded.

### Migration Path to Protocol A

Switching to standard Modbus FC 0x03 would eliminate cross-talk, the
wakeup-drain hack, and the 0x55AA parsing — but requires:
- Determining the maximum register count per read (10 confirmed, need
  to find upper limit)
- Reading in multiple FC 0x03 blocks if the full status map (~140
  registers) exceeds the per-read limit
- Handling the big-endian → little-endian conversion (FC 0x03 returns
  standard Modbus big-endian; the driver currently expects little-endian)

## Checksums

### Modbus CRC-16 (Protocol A, B requests, C)

Standard Modbus CRC-16 (polynomial 0xA001), used on all Modbus RTU
frames (FC 0x03, FC 0x10 requests and ACKs):
```
crc = 0xFFFF
for each byte b in message:
    crc ^= b
    repeat 8 times:
        if crc & 1:  crc = (crc >> 1) ^ 0xA001
        else:        crc >>= 1
result = crc as 2 bytes, little-endian
```

Verified by recomputing CRC for all 54 captured Modbus frames (10s
capture, 2026-04-03) — all match.

Note: In external-host tests (2026-04-02, no bus master present),
Protocol B did NOT verify the Modbus CRC on requests (BMS responded
to corrupted CRC).  Protocol A does verify CRC.  Protocol C uses valid
CRC in both directions (not tested with corrupted CRC).

### 8-bit checksum (0x55AA proprietary responses)

The 0x55AA proprietary response frame is exactly 300 bytes (offsets
0–299).  Byte 299 is a simple 8-bit checksum:

```
checksum = sum(bytes[0:299]) & 0xFF
```

Verified on all 4 batteries (3 slaves + bus master), both status
(0x1620) and settings (0x161E) responses, across multiple cycles
(2026-04-03).  The checksum position is consistently byte 299 — no
exceptions observed.

## CH341 USB-Serial Quirks

The CH341 chip in the factory RS485 adapter has two issues:

1. **TX echo**: in half-duplex mode, transmitted bytes appear in the RX
   buffer.  For Protocol B, this shifts the 0x55AA header by 3–5 bytes.
   For Protocol A, the FC 0x03 response is preceded by the echo.

2. **Stale FIFO**: retains bytes across port close/reopen.  Fixed by
   `reset_input_buffer()` before each command.

Neither issue occurs with proper RS485 transceivers with TX/RX control.
Neither issue applies to Protocol C (BMS-to-BMS, no external adapter).

## Protocol Timing (measured)

### Protocol B — external host, 4-battery system

| Phase              | Duration   | Notes |
|--------------------|------------|-------|
| Port open + settle | 15–50ms    | skip if port kept open |
| Command + response | 35–50ms    | 300 bytes at 115200 baud |
| Post-read drain    | 10–50ms    | write-ACK + cross-talk |
| **Total per battery** | **~50–100ms** | with shared port |

With the legacy wakeup-and-drain pattern, add ~80–100ms.

### Protocol C — bus master, 3 active + 12 scanned

| Phase              | Duration   | Notes |
|--------------------|------------|-------|
| Per active battery | ~260ms     | 0x1620 req + ACK + 0x161E req + ACK |
| Per scanned addr   | 220ms      | 0x1620 req, no response, timeout |
| Active poll total  | ~780ms     | 3 batteries × 260ms |
| Scan total         | ~2640ms    | 12 addresses × 220ms |
| Master self-broadcast | ~440ms  | 2 frames in the addr=15 gap |
| **Full cycle**     | **4400ms** | fixed, includes all phases |

## Venus OS / D-Bus Integration Notes

These notes are specific to the `dbus-serialbattery` Venus OS driver.

### D-Bus Performance

Venus OS uses D-Bus (via dbus-python + GLib) for IPC. Each battery
publishes ~100 properties per cycle. Mitigations:

1. **`_CachedDbusProxy`**: suppresses writes when value unchanged.
   Combined with EMA cell voltage filtering (alpha=0.3), reduces actual
   D-Bus writes from ~100 to ~3–10 per battery per cycle.

2. **Poll interval decoupling**: auto-increase logic uses serial+calc
   time only, excluding D-Bus overhead.

### GLib Reentrancy Problem (unresolved)

The driver runs a single-threaded GLib main loop handling both outgoing
property updates and incoming `GetValue`/`GetText` requests. During
serial I/O, requests queue up and get processed reentrantly when
`publish_dbus()` touches D-Bus objects, causing occasional 1–2s stalls.

Current workaround: decoupled poll interval prevents stalls from
affecting data rate. A full fix requires replacing dbus-python with an
async D-Bus library or separating serial and D-Bus into different
processes.

## Reference Documents

- "极空BMS RS485 Modbus通用协议(V1.0)" — `JK_BMS.RS485.Modbus.v1_0.pdf`
- "BMS RS485 Modbus V1.1" — `BMS RS485 Modbus V1.1-1.pdf`
- Driver source: `dbus-serialbattery/bms/jkbms_pb.py`
- Diagnostic tool: `test/jkbms_pb_sniff.py`
