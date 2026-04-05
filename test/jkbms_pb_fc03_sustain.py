#!/usr/bin/env python3
"""
JKBMS PB — Can FC 0x03 sustain itself through continuous polling?

Tests whether rapid FC 0x03 reads can keep the bus alive without
any FC 0x10 priming.  Measures how many consecutive reads succeed
and at what intervals the bus goes cold.

Usage: python3 jkbms_pb_fc03_sustain.py /dev/ttyUSB0 [addr]
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
    return None


print(f"Port: {PORT}  Addr: 0x{ADDR:02X}\n")

ser = serial.Serial(PORT, baudrate=BAUD, timeout=0.1)
time.sleep(0.2)
ser.reset_input_buffer()

# === Test 1: Warmup — how many attempts to get first response? ===
print("=== Warmup (FC 0x03 only, no FC 0x10) ===")
for attempt in range(1, 21):
    r = read_registers(ser, ADDR, 0x1200, 2, timeout=0.3)
    if r is not None:
        print(f"  First response on attempt {attempt}: {r}")
        break
    print(f"  Attempt {attempt}: no response")
else:
    print("  FAILED after 20 attempts — bus is dead")
    ser.close()
    sys.exit(1)

# === Test 2: Sustained polling — 50 reads, no pause between ===
print("\n=== Sustained polling: 50 reads, no extra delay ===")
results = []
t_start = time.monotonic()
for i in range(50):
    t0 = time.monotonic()
    r = read_registers(ser, ADDR, 0x1200, 4, timeout=0.3)
    t1 = time.monotonic()
    ok = r is not None
    results.append((i, ok, (t1 - t0) * 1000, r))
    if i < 10 or not ok or i % 10 == 0:
        status = f"cells={r}" if ok else "FAIL"
        print(f"  [{i:2d}] {(t1-t0)*1000:5.0f}ms  {status}")

elapsed = time.monotonic() - t_start
ok_count = sum(1 for _, ok, _, _ in results if ok)
fail_count = 50 - ok_count
print(f"\n  Total: {ok_count}/50 OK, {fail_count} FAIL, {elapsed:.1f}s elapsed")
print(f"  Avg per read: {elapsed/50*1000:.0f}ms")

# Find first failure
first_fail = next((i for i, ok, _, _ in results if not ok), None)
if first_fail is not None:
    print(f"  First failure at read #{first_fail}")
    # Count consecutive successes from start
    consec = sum(1 for i, ok, _, _ in results if ok and i < first_fail)
    print(f"  Consecutive successes from start: {consec}")
else:
    print(f"  No failures — FC 0x03 sustained itself!")

# === Test 3: Polling with 200ms interval (simulating 5 reads/s) ===
print("\n=== Polling at 200ms interval: 30 reads ===")
results2 = []
for i in range(30):
    t0 = time.monotonic()
    r = read_registers(ser, ADDR, 0x1200, 4, timeout=0.3)
    t1 = time.monotonic()
    ok = r is not None
    results2.append((i, ok, (t1 - t0) * 1000))
    status = "OK" if ok else "FAIL"
    if i < 5 or not ok or i % 5 == 0:
        print(f"  [{i:2d}] {(t1-t0)*1000:5.0f}ms  {status}")
    # Pad to 200ms
    wait = 0.2 - (t1 - t0)
    if wait > 0:
        time.sleep(wait)

ok_count2 = sum(1 for _, ok, _ in results2 if ok)
print(f"\n  Total: {ok_count2}/30 OK")

# === Test 4: Polling with 1s interval (simulating real driver) ===
print("\n=== Polling at 1s interval: 10 reads ===")
results3 = []
for i in range(10):
    t0 = time.monotonic()
    r = read_registers(ser, ADDR, 0x1200, 4, timeout=0.3)
    t1 = time.monotonic()
    ok = r is not None
    results3.append((i, ok, (t1 - t0) * 1000))
    status = f"cells={r}" if ok else "FAIL"
    print(f"  [{i:2d}] {(t1-t0)*1000:5.0f}ms  {status}")
    wait = 1.0 - (t1 - t0)
    if wait > 0:
        time.sleep(wait)

ok_count3 = sum(1 for _, ok, _ in results3 if ok)
print(f"\n  Total: {ok_count3}/10 OK")

# === Test 5: Multiple addresses in sequence (simulating multi-battery) ===
print("\n=== Multi-address polling: 4 addrs x 5 rounds ===")
addrs = [0x01, 0x02, 0x03, 0x04]
for rnd in range(5):
    line = f"  Round {rnd+1}:"
    for addr in addrs:
        r = read_registers(ser, addr, 0x1200, 4, timeout=0.3)
        if r is not None:
            line += f"  0x{addr:02X}=OK"
        else:
            line += f"  0x{addr:02X}=FAIL"
    print(line)

ser.close()
print("\nDone.")
