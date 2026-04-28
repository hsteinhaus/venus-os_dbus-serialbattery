#!/usr/bin/env python3
"""
JKBMS PB — Standard Modbus RTU FC 0x03 validation test.

Custom FC 0x03 reader that handles CH341 TX echo (2-3 garbled bytes
prepended to response).  Validates register map from official JKBMS
RS485 Modbus V1.0/V1.1 spec against live hardware.

Usage: python3 jkbms_pb_fc03.py /dev/ttyUSB0 [addr]
  addr: hex, e.g. 0x01 (default)
"""

import struct
import sys
import time
import serial


PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
ADDR = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x01
BAUD = 115200


# ---------------------------------------------------------------------------
# Modbus RTU helpers — this is the code that will go into the driver
# ---------------------------------------------------------------------------

def modbus_crc(msg: bytes) -> bytes:
    crc = 0xFFFF
    for b in msg:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, "little")


def read_registers(ser, addr: int, start_reg: int, count: int, timeout: float = 0.5):
    """
    Modbus RTU FC 0x03 Read Holding Registers.

    Handles CH341 TX echo by scanning for the response pattern
    [addr] [0x03] [byte_count] in the received data.

    Returns list of uint16 register values, or None on failure.
    """
    # Build request
    req = struct.pack(">BBHH", addr, 0x03, start_reg, count)
    req += modbus_crc(req)

    ser.reset_input_buffer()
    ser.write(req)

    # Expected response: addr(1) + FC(1) + byte_count(1) + data(count*2) + CRC(2)
    expected_data_len = count * 2
    expected_resp_len = 3 + expected_data_len + 2
    # Read enough for echo prefix + response
    max_read = len(req) + expected_resp_len + 4  # generous margin

    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ser.in_waiting:
            data.extend(ser.read(ser.in_waiting))
            if len(data) >= max_read:
                break
        else:
            if len(data) >= expected_resp_len + 2:
                # Might have enough, check before sleeping
                break
            time.sleep(0.01)

    if not data:
        return None

    # Scan for response: [addr] [0x03] [byte_count]
    for i in range(len(data) - 2):
        if data[i] == addr and data[i + 1] == 0x03:
            bc = data[i + 2]
            if bc != expected_data_len:
                continue  # byte count mismatch, keep scanning
            resp_end = i + 3 + bc + 2  # include CRC
            if resp_end > len(data):
                # Not enough bytes yet — try reading more
                remaining = resp_end - len(data)
                extra_deadline = time.monotonic() + 0.1
                while len(data) < resp_end and time.monotonic() < extra_deadline:
                    if ser.in_waiting:
                        data.extend(ser.read(ser.in_waiting))
                    else:
                        time.sleep(0.005)
                if resp_end > len(data):
                    return None  # still truncated

            resp = data[i:resp_end]
            # Validate CRC
            msg_part = resp[:-2]
            expected_crc = modbus_crc(bytes(msg_part))
            actual_crc = bytes(resp[-2:])
            if expected_crc != actual_crc:
                continue  # CRC mismatch, keep scanning

            # Parse register values (big-endian)
            regs = []
            for j in range(0, bc, 2):
                regs.append(struct.unpack(">H", resp[3 + j : 3 + j + 2])[0])
            return regs

    # Check for Modbus error response: [addr] [0x83] [error_code]
    for i in range(len(data) - 2):
        if data[i] == addr and data[i + 1] == 0x83:
            err_names = {1: "illegal function", 2: "illegal address", 3: "illegal data"}
            print(f"    Modbus error: code {data[i+2]} ({err_names.get(data[i+2], '?')})")
            return None

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def s16(v):
    """Unsigned uint16 → signed int16."""
    return v - 65536 if v > 32767 else v


def u32(regs, i):
    """Two consecutive registers → unsigned 32-bit (big-endian word order)."""
    return (regs[i] << 16) | regs[i + 1]


def u32_swap(regs, i):
    """Two consecutive registers → unsigned 32-bit (little-endian word order)."""
    return (regs[i + 1] << 16) | regs[i]


def s32(regs, i):
    v = u32(regs, i)
    return v - 0x100000000 if v > 0x7FFFFFFF else v


def s32_swap(regs, i):
    v = u32_swap(regs, i)
    return v - 0x100000000 if v > 0x7FFFFFFF else v


def regs_to_ascii(regs):
    chars = []
    for r in regs:
        hi = (r >> 8) & 0xFF
        lo = r & 0xFF
        if hi:
            chars.append(chr(hi) if 32 <= hi < 127 else ".")
        if lo:
            chars.append(chr(lo) if 32 <= lo < 127 else ".")
    return "".join(chars).split("\x00")[0]


def regs_hex(regs):
    return " ".join(f"{r:04x}" for r in regs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

print(f"Port: {PORT}  Baud: {BAUD}  Address: 0x{ADDR:02X}")
print()

ser = serial.Serial(PORT, baudrate=BAUD, timeout=0.1)
time.sleep(0.3)
ser.reset_input_buffer()

# === Bus warmup ===
print("=== Bus warmup ===")
for attempt in range(1, 11):
    regs = read_registers(ser, ADDR, 0x1200, 2, timeout=0.5)
    if regs is not None:
        print(f"  Warm on attempt {attempt}: regs={regs}")
        break
    print(f"  Attempt {attempt}: no response")
else:
    print("  FAILED after 10 attempts.  Run immediately after stopping the driver.")
    ser.close()
    sys.exit(1)

# === 1. Max register count ===
print("\n=== Max register count ===")
for base_name, base in [("status 0x1200", 0x1200), ("settings 0x1000", 0x1000), ("about 0x1400", 0x1400)]:
    max_ok = 0
    for count in [10, 20, 30, 40, 50, 60, 70, 80, 100, 125]:
        regs = read_registers(ser, ADDR, base, count, timeout=0.8)
        if regs is not None:
            max_ok = count
            print(f"  {base_name} count={count:3d}: OK ({len(regs)} regs)")
        else:
            print(f"  {base_name} count={count:3d}: FAIL")
            break
    print(f"  → max confirmed: {max_ok}\n")

# === 2. Device info (0x1400) ===
print("=== Device info (0x1400) ===")
regs = read_registers(ser, ADDR, 0x1400, 20)
if regs:
    print(f"  raw: {regs_hex(regs)}")
    device_id = regs_to_ascii(regs[0:8])
    hw_ver = regs_to_ascii(regs[8:12])
    sw_ver = regs_to_ascii(regs[12:16])
    # Try both word orders for UINT32
    runtime_be = u32(regs, 16)
    runtime_le = u32_swap(regs, 16)
    pwr_on_be = u32(regs, 18)
    pwr_on_le = u32_swap(regs, 18)
    print(f"  DeviceID:    '{device_id}'")
    print(f"  HW version:  '{hw_ver}'")
    print(f"  SW version:  '{sw_ver}'")
    print(f"  Runtime:     BE={runtime_be}s ({runtime_be/3600:.1f}h)  LE={runtime_le}s ({runtime_le/3600:.1f}h)")
    print(f"  PWR-on:      BE={pwr_on_be}  LE={pwr_on_le}")
else:
    print("  FAIL")

# Probe serial number at higher offsets
print("\n=== Probing serial number (0x1400 area) ===")
for start, label in [(0x1414, "offset 40"), (0x1417, "offset 46"), (0x141A, "offset 52")]:
    regs = read_registers(ser, ADDR, start, 10)
    if regs:
        text = regs_to_ascii(regs)
        print(f"  0x{start:04X} ({label}): raw={regs_hex(regs)}")
        print(f"    ascii: '{text}'")
    else:
        print(f"  0x{start:04X} ({label}): FAIL")

# === 3. Cell voltages (0x1200) ===
print("\n=== Cell voltages (0x1200, 16 cells) ===")
regs = read_registers(ser, ADDR, 0x1200, 16)
if regs:
    print(f"  raw (mV): {regs}")
    print(f"  volts:    {[r/1000 for r in regs]}")
else:
    print("  FAIL")

# === 4. Status block — TempMos through Charge/Discharge ===
print("\n=== Status block (0x1245, 28 regs) ===")
regs = read_registers(ser, ADDR, 0x1245, 28)
if regs:
    print(f"  raw: {regs_hex(regs)}")
    # Parse with both word orders to determine which is correct
    print(f"\n  --- Big-endian word order ---")
    print(f"  [0]  TempMos:       {s16(regs[0])/10}°C (raw={regs[0]}, signed={s16(regs[0])})")
    print(f"  [3-4] BatVol:       {u32(regs, 3)/1000}V (raw={u32(regs, 3)} mV)")
    print(f"  [7-8] BatCurrent:   {s32(regs, 7)/1000}A (raw={s32(regs, 7)} mA)")
    print(f"  [9]  TempBat1:      {s16(regs[9])/10}°C (raw={regs[9]})")
    print(f"  [10] TempBat2:      {s16(regs[10])/10}°C (raw={regs[10]})")
    print(f"  [11-12] Alarms:     0x{u32(regs, 11):08X}")
    print(f"  [13] BalanCurrent:  {s16(regs[13])} mA")
    print(f"  [14] BalanSta/SOC:  sta={regs[14]>>8} SOC={regs[14]&0xFF}%")
    print(f"  [15-16] CapRemain:  {s32(regs, 15)/1000} Ah (raw={s32(regs, 15)} mAh)")
    print(f"  [17-18] FullCap:    {u32(regs, 17)/1000} Ah")
    print(f"  [19-20] Cycles:     {u32(regs, 19)}")
    print(f"  [23] SOH/Precharge: SOH={regs[23]>>8}% precharge={regs[23]&0xFF}")
    print(f"  [27] Chg/Disch:     charge={regs[27]>>8} discharge={regs[27]&0xFF}")

    print(f"\n  --- Little-endian (swapped) word order ---")
    print(f"  [3-4] BatVol:       {u32_swap(regs, 3)/1000}V (raw={u32_swap(regs, 3)} mV)")
    print(f"  [7-8] BatCurrent:   {s32_swap(regs, 7)/1000}A (raw={s32_swap(regs, 7)} mA)")
    print(f"  [11-12] Alarms:     0x{u32_swap(regs, 11):08X}")
    print(f"  [15-16] CapRemain:  {s32_swap(regs, 15)/1000} Ah")
    print(f"  [17-18] FullCap:    {u32_swap(regs, 17)/1000} Ah")
    print(f"  [19-20] Cycles:     {u32_swap(regs, 19)}")
else:
    print("  FAIL")

# === 5. TempSensor presence + Heating ===
print("\n=== TempSensor/Heating (0x1268) ===")
regs = read_registers(ser, ADDR, 0x1268, 1)
if regs:
    print(f"  raw: 0x{regs[0]:04X}  hi=0b{regs[0]>>8:08b} lo={regs[0]&0xFF}")
    print(f"  TempSensorPresence: 0x{regs[0]>>8:02X}  Heating: {regs[0]&0xFF}")
else:
    print("  FAIL")

# === 6. HeatCurrent ===
print("\n=== HeatCurrent (0x1273) ===")
regs = read_registers(ser, ADDR, 0x1273, 1)
if regs:
    print(f"  raw: {regs[0]}  = {regs[0]/1000} A")
else:
    print("  FAIL")

# === 7. TempBat3/4 ===
print("\n=== TempBat3/4 (0x127C-D) ===")
regs = read_registers(ser, ADDR, 0x127C, 2)
if regs:
    print(f"  TempBat3: {s16(regs[0])/10}°C (raw={regs[0]})")
    print(f"  TempBat4: {s16(regs[1])/10}°C (raw={regs[1]})")
else:
    print("  FAIL")

# === 8. Settings — key fields ===
print("\n=== Settings (0x1000 area) ===")
# Block 1: first 40 registers (byte offsets 0-79)
s1 = read_registers(ser, ADDR, 0x1000, 40)
if s1:
    print(f"  Block 0x1000 x40 raw: {regs_hex(s1)}")
    # CellUV at byte offset 4 = register offset 2
    print(f"\n  --- BE word order ---")
    print(f"  [0-1] VolSmartSleep: {u32(s1, 0)} mV = {u32(s1, 0)/1000}V")
    print(f"  [2-3] CellUV:        {u32(s1, 2)} mV = {u32(s1, 2)/1000}V")
    print(f"  [6-7] CellOV:        {u32(s1, 6)} mV = {u32(s1, 6)/1000}V")
    print(f"  [22-23] CurBatCOC:   {u32(s1, 22)} mA = {u32(s1, 22)/1000}A")
    print(f"  [28-29] CurBatDcOC:  {u32(s1, 28)} mA = {u32(s1, 28)/1000}A")
    print(f"\n  --- LE (swapped) word order ---")
    print(f"  [0-1] VolSmartSleep: {u32_swap(s1, 0)} mV = {u32_swap(s1, 0)/1000}V")
    print(f"  [2-3] CellUV:        {u32_swap(s1, 2)} mV = {u32_swap(s1, 2)/1000}V")
    print(f"  [6-7] CellOV:        {u32_swap(s1, 6)} mV = {u32_swap(s1, 6)/1000}V")
    print(f"  [22-23] CurBatCOC:   {u32_swap(s1, 22)} mA = {u32_swap(s1, 22)/1000}A")
    print(f"  [28-29] CurBatDcOC:  {u32_swap(s1, 28)} mA = {u32_swap(s1, 28)/1000}A")
else:
    print("  Block 0x1000 x40: FAIL")

# CellCount + capacity area
s2 = read_registers(ser, ADDR, 0x1036, 6)
if s2:
    print(f"\n  Block 0x1036 x6 raw: {regs_hex(s2)}")
    print(f"  --- BE ---")
    print(f"  [0-1] CellCount:   {u32(s2, 0)}")
    print(f"  [4-5] CapBatCell:  {u32(s2, 4)} mAh = {u32(s2, 4)/1000} Ah")
    print(f"  --- LE ---")
    print(f"  [0-1] CellCount:   {u32_swap(s2, 0)}")
    print(f"  [4-5] CapBatCell:  {u32_swap(s2, 4)} mAh = {u32_swap(s2, 4)/1000} Ah")
else:
    print("  Block 0x1036 x6: FAIL")

# BalanEN
s3 = read_registers(ser, ADDR, 0x103C, 2)
if s3:
    print(f"\n  Block 0x103C x2 (BalanEN) raw: {regs_hex(s3)}")
    print(f"  BE: {u32(s3, 0)}  LE: {u32_swap(s3, 0)}")
else:
    print("  Block 0x103C: FAIL")

# DevAddr + control bitmask
s4 = read_registers(ser, ADDR, 0x1084, 8)
if s4:
    print(f"\n  Block 0x1084 x8 raw: {regs_hex(s4)}")
    print(f"  [0-1] DevAddr:    BE={u32(s4, 0)} LE={u32_swap(s4, 0)}")
    if len(s4) >= 6:
        print(f"  [4-5] CtrlBitmask area: {regs_hex(s4[4:])}")
else:
    print("  Block 0x1084: FAIL")

# === 9. Address filtering ===
print("\n=== Address filtering ===")
for addr in [0x01, 0x02, 0x03, 0x04]:
    regs = read_registers(ser, addr, 0x1200, 4)
    if regs:
        print(f"  addr=0x{addr:02X}: cells(mV)={regs}")
    else:
        print(f"  addr=0x{addr:02X}: no response")

ser.close()
print("\nDone.")
