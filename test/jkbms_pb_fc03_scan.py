#!/usr/bin/env python3
"""
JKBMS PB — Scan Modbus register space to find accessible ranges.

Reads single registers across 0x1000, 0x1200, 0x1400 areas to map
which addresses exist and which return errors.

Usage: python3 jkbms_pb_fc03_scan.py /dev/ttyUSB0 [addr]
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


def read_registers(ser, addr, start_reg, count, timeout=0.3):
    req = struct.pack(">BBHH", addr, 0x03, start_reg, count)
    req += modbus_crc(req)
    ser.reset_input_buffer()
    ser.write(req)

    data = bytearray()
    deadline = time.monotonic() + timeout
    expected_resp_len = 3 + count * 2 + 2
    max_read = len(req) + expected_resp_len + 4

    while time.monotonic() < deadline:
        if ser.in_waiting:
            data.extend(ser.read(ser.in_waiting))
            if len(data) >= expected_resp_len + 2:
                break
        else:
            time.sleep(0.005)

    if not data:
        return None

    for i in range(len(data) - 2):
        if data[i] == addr and data[i + 1] == 0x03:
            bc = data[i + 2]
            if bc != count * 2:
                continue
            resp_end = i + 3 + bc + 2
            if resp_end > len(data):
                extra_deadline = time.monotonic() + 0.1
                while len(data) < resp_end and time.monotonic() < extra_deadline:
                    if ser.in_waiting:
                        data.extend(ser.read(ser.in_waiting))
                    else:
                        time.sleep(0.005)
                if resp_end > len(data):
                    return None
            resp = data[i:resp_end]
            expected_crc = modbus_crc(bytes(resp[:-2]))
            if expected_crc != bytes(resp[-2:]):
                continue
            regs = []
            for j in range(0, bc, 2):
                regs.append(struct.unpack(">H", resp[3 + j : 3 + j + 2])[0])
            return regs

    # Check for error response
    for i in range(len(data) - 2):
        if data[i] == addr and data[i + 1] == 0x83:
            return "ERR"

    return None


ser = serial.Serial(PORT, baudrate=BAUD, timeout=0.1)
time.sleep(0.2)
ser.reset_input_buffer()

print(f"Port: {PORT}  Addr: 0x{ADDR:02X}")

# Warmup
for attempt in range(10):
    r = read_registers(ser, ADDR, 0x1200, 1)
    if r is not None and r != "ERR":
        print(f"Warm on attempt {attempt + 1}\n")
        break
else:
    print("Warmup FAILED")
    ser.close()
    sys.exit(1)

# Scan each area: read 1 register at a time
for area_name, base, span in [
    ("STATUS 0x1200", 0x1200, 0x90),
    ("SETTINGS 0x1000", 0x1000, 0x90),
    ("ABOUT 0x1400", 0x1400, 0x30),
]:
    print(f"=== {area_name} (scanning {span} registers) ===")
    run_start = None
    run_vals = []

    for offset in range(span):
        reg = base + offset
        result = read_registers(ser, ADDR, reg, 1)

        if result is not None and result != "ERR":
            val = result[0]
            if run_start is None:
                run_start = reg
                run_vals = [val]
            else:
                run_vals.append(val)
        else:
            # End of accessible run
            if run_start is not None:
                end = run_start + len(run_vals) - 1
                hex_vals = " ".join(f"{v:04x}" for v in run_vals[:20])
                suffix = "..." if len(run_vals) > 20 else ""
                print(f"  0x{run_start:04X}-0x{end:04X} ({len(run_vals)} regs): {hex_vals}{suffix}")
                run_start = None
                run_vals = []

    # Flush final run
    if run_start is not None:
        end = run_start + len(run_vals) - 1
        hex_vals = " ".join(f"{v:04x}" for v in run_vals[:20])
        suffix = "..." if len(run_vals) > 20 else ""
        print(f"  0x{run_start:04X}-0x{end:04X} ({len(run_vals)} regs): {hex_vals}{suffix}")

    print()

ser.close()
print("Done.")
