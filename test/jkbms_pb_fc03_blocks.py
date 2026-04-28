#!/usr/bin/env python3
"""
JKBMS PB — Read specific FC 0x03 register blocks rapidly.

Reads targeted blocks immediately after bus warmup, no pauses between.
Tests both small (2-reg) and larger (10-reg) blocks.

Usage: python3 jkbms_pb_fc03_blocks.py /dev/ttyUSB0 [addr]
"""

import struct
import sys
import time
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
ADDR = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x01
BAUD = 115200


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


def read_registers(ser, addr, start_reg, count, timeout=0.5):
    req = struct.pack(">BBHH", addr, 0x03, start_reg, count)
    req += modbus_crc(req)
    ser.reset_input_buffer()
    ser.write(req)

    expected_data_len = count * 2
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ser.in_waiting:
            data.extend(ser.read(ser.in_waiting))
            if len(data) >= 3 + expected_data_len + 2 + 2:
                break
        else:
            time.sleep(0.005)

    for i in range(len(data) - 2):
        if data[i] == addr and data[i + 1] == 0x03:
            bc = data[i + 2]
            if bc != expected_data_len:
                continue
            resp_end = i + 3 + bc + 2
            if resp_end > len(data):
                extra = time.monotonic() + 0.1
                while len(data) < resp_end and time.monotonic() < extra:
                    if ser.in_waiting:
                        data.extend(ser.read(ser.in_waiting))
                    else:
                        time.sleep(0.005)
                if resp_end > len(data):
                    return None
            resp = data[i:resp_end]
            if modbus_crc(bytes(resp[:-2])) != bytes(resp[-2:]):
                continue
            return [struct.unpack(">H", resp[3 + j : 3 + j + 2])[0] for j in range(0, bc, 2)]

    for i in range(len(data) - 2):
        if data[i] == addr and data[i + 1] == 0x83:
            return f"ERR:{data[i+2]}"
    return None


def s16(v):
    return v - 65536 if v > 32767 else v

def u32(regs, i):
    return (regs[i] << 16) | regs[i + 1]

def s32(regs, i):
    v = u32(regs, i)
    return v - 0x100000000 if v > 0x7FFFFFFF else v

def regs_hex(regs):
    return " ".join(f"{r:04x}" for r in regs)

def regs_to_ascii(regs):
    chars = []
    for r in regs:
        hi, lo = (r >> 8) & 0xFF, r & 0xFF
        chars.append(chr(hi) if 32 <= hi < 127 else "")
        chars.append(chr(lo) if 32 <= lo < 127 else "")
    return "".join(chars).split("\x00")[0]


ser = serial.Serial(PORT, baudrate=BAUD, timeout=0.1)
time.sleep(0.2)
ser.reset_input_buffer()

# Warmup
print(f"Port: {PORT}  Addr: 0x{ADDR:02X}\n")
for attempt in range(10):
    r = read_registers(ser, ADDR, 0x1200, 2)
    if r is not None and not isinstance(r, str):
        print(f"Warm on attempt {attempt + 1}: cells[0:2]={r}\n")
        break
else:
    print("Warmup FAILED")
    ser.close()
    sys.exit(1)

# Read blocks rapidly — no delays between reads
reads = []

def do_read(label, reg, count):
    r = read_registers(ser, ADDR, reg, count)
    status = "OK" if r is not None and not isinstance(r, str) else str(r)
    reads.append((label, reg, count, r))
    return r

# --- Cells (0x1200) ---
do_read("cells 0-7", 0x1200, 8)
do_read("cells 8-15", 0x1208, 8)

# --- Status area: try different block sizes at key offsets ---
# TempMos area (byte offset 0x008A = reg offset 0x45)
do_read("TempMos(1)", 0x1245, 1)
do_read("TempMos(2)", 0x1245, 2)
do_read("TempMos(4)", 0x1245, 4)

# BatVol area (byte offset 0x0090 = reg offset 0x48)
do_read("BatVol(2)", 0x1248, 2)
do_read("BatVol+Watt+Cur(6)", 0x1248, 6)

# BatCurrent (byte offset 0x0098 = reg offset 0x4C)
do_read("BatCurrent(2)", 0x124C, 2)

# TempBat1/2 (byte offset 0x009C = reg offset 0x4E)
do_read("TempBat1+2(2)", 0x124E, 2)

# Alarms (byte offset 0x00A0 = reg offset 0x50)
do_read("Alarms(2)", 0x1250, 2)

# BalanSta/SOC (byte offset 0x00A6 = reg offset 0x53)
do_read("BalanSta+SOC(1)", 0x1253, 1)

# SOCCapRemain (byte offset 0x00A8 = reg offset 0x54)
do_read("CapRemain(2)", 0x1254, 2)

# Cycles (byte offset 0x00B0 = reg offset 0x58)
do_read("Cycles(2)", 0x1258, 2)

# SOH (byte offset 0x00B8 = reg offset 0x5C)
do_read("SOH(1)", 0x125C, 1)

# Charge/Discharge (byte offset 0x00C0 = reg offset 0x60)
do_read("ChgDisch(1)", 0x1260, 1)

# TempSensor/Heating (byte offset 0x00D0 = reg offset 0x68)
do_read("TempSens(1)", 0x1268, 1)

# HeatCurrent (byte offset 0x00E6 = reg offset 0x73)
do_read("HeatCur(1)", 0x1273, 1)

# TempBat3/4 (byte offset 0x00F8 = reg offset 0x7C)
do_read("TempBat3+4(2)", 0x127C, 2)

# --- Try a big contiguous block from 0x1248 to 0x1260 (25 regs) ---
do_read("status_block(25)", 0x1248, 25)

# --- Settings key fields ---
do_read("CellUV(2)", 0x1002, 2)   # byte offset 4
do_read("CellOV(2)", 0x1006, 2)   # byte offset 12
do_read("CurBatCOC(2)", 0x1016, 2)  # byte offset 44
do_read("CurBatDcOC(2)", 0x101C, 2) # byte offset 56
do_read("CellCount(2)", 0x1036, 2)  # byte offset 108
do_read("BalanEN(2)", 0x103C, 2)    # byte offset 120
do_read("CapBatCell(2)", 0x103E, 2) # byte offset 124
do_read("DevAddr(2)", 0x1084, 2)    # byte offset 264

# --- Device info ---
do_read("DeviceID(8)", 0x1400, 8)
do_read("HW+SW(8)", 0x1408, 8)
do_read("Runtime(2)", 0x1410, 2)

# --- Print results ---
print(f"{'Label':<25s} {'Reg':>6s} {'Cnt':>3s} {'Result'}")
print("-" * 80)
for label, reg, count, r in reads:
    if r is None:
        print(f"{label:<25s} 0x{reg:04X} x{count:<2d}  NO RESPONSE")
    elif isinstance(r, str):
        print(f"{label:<25s} 0x{reg:04X} x{count:<2d}  {r}")
    else:
        hex_str = regs_hex(r)
        print(f"{label:<25s} 0x{reg:04X} x{count:<2d}  {hex_str}")

# --- Interpret successful reads ---
print("\n=== Interpreted values ===")
for label, reg, count, r in reads:
    if r is None or isinstance(r, str):
        continue
    if label.startswith("cells"):
        print(f"  {label}: {[v/1000 for v in r]} V")
    elif label == "TempMos(1)" and len(r) == 1:
        print(f"  TempMos: {s16(r[0])/10}°C (raw={r[0]}, signed={s16(r[0])})")
    elif label == "BatVol(2)" and len(r) == 2:
        print(f"  BatVol: BE={u32(r,0)/1000}V  (raw={u32(r,0)})")
    elif label == "BatCurrent(2)" and len(r) == 2:
        print(f"  BatCurrent: BE={s32(r,0)/1000}A  (raw={s32(r,0)})")
    elif label == "TempBat1+2(2)" and len(r) == 2:
        print(f"  TempBat1: {s16(r[0])/10}°C  TempBat2: {s16(r[1])/10}°C")
    elif label == "Alarms(2)" and len(r) == 2:
        print(f"  Alarms: 0x{u32(r,0):08X}")
    elif label == "BalanSta+SOC(1)" and len(r) == 1:
        print(f"  BalanSta: {r[0]>>8}  SOC: {r[0]&0xFF}%")
    elif label == "CapRemain(2)" and len(r) == 2:
        print(f"  CapRemain: {s32(r,0)/1000} Ah  (raw={s32(r,0)})")
    elif label == "Cycles(2)" and len(r) == 2:
        print(f"  Cycles: {u32(r,0)}")
    elif label == "SOH(1)" and len(r) == 1:
        print(f"  SOH: {r[0]>>8}%  Precharge: {r[0]&0xFF}")
    elif label == "ChgDisch(1)" and len(r) == 1:
        print(f"  ChargeFET: {r[0]>>8}  DischargeFET: {r[0]&0xFF}")
    elif label == "TempSens(1)" and len(r) == 1:
        print(f"  TempSensorMask: 0b{r[0]>>8:08b}  Heating: {r[0]&0xFF}")
    elif label == "HeatCur(1)" and len(r) == 1:
        print(f"  HeatCurrent: {r[0]} mA = {r[0]/1000} A")
    elif label == "TempBat3+4(2)" and len(r) == 2:
        print(f"  TempBat3: {s16(r[0])/10}°C  TempBat4: {s16(r[1])/10}°C")
    elif label == "CellUV(2)" and len(r) == 2:
        print(f"  CellUV: BE={u32(r,0)} mV = {u32(r,0)/1000}V")
    elif label == "CellOV(2)" and len(r) == 2:
        print(f"  CellOV: BE={u32(r,0)} mV = {u32(r,0)/1000}V")
    elif label == "CurBatCOC(2)" and len(r) == 2:
        print(f"  CurBatCOC: BE={u32(r,0)} mA = {u32(r,0)/1000}A")
    elif label == "CurBatDcOC(2)" and len(r) == 2:
        print(f"  CurBatDcOC: BE={u32(r,0)} mA = {u32(r,0)/1000}A")
    elif label == "CellCount(2)" and len(r) == 2:
        print(f"  CellCount: BE={u32(r,0)}")
    elif label == "BalanEN(2)" and len(r) == 2:
        print(f"  BalanEN: BE={u32(r,0)}")
    elif label == "CapBatCell(2)" and len(r) == 2:
        print(f"  CapBatCell: BE={u32(r,0)} mAh = {u32(r,0)/1000} Ah")
    elif label == "DevAddr(2)" and len(r) == 2:
        print(f"  DevAddr: BE={u32(r,0)}")
    elif label == "DeviceID(8)":
        print(f"  DeviceID: '{regs_to_ascii(r)}'")
    elif label == "HW+SW(8)":
        print(f"  HW: '{regs_to_ascii(r[:4])}'  SW: '{regs_to_ascii(r[4:])}'")
    elif label == "Runtime(2)" and len(r) == 2:
        rt = u32(r, 0)
        print(f"  Runtime: {rt}s = {rt/3600:.1f}h = {rt/86400:.1f}d")

ser.close()
print("\nDone.")
