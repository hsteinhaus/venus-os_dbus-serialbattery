#!/usr/bin/env python3
"""
JKBMS PB — What wakes the bus for FC 0x03?

Tests different "warmup" stimuli before FC 0x03 reads to determine
what bus activity is required.

Usage: python3 jkbms_pb_fc03_warmup.py /dev/ttyUSB0 [addr]
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


def fc03_request(addr, start_reg, count):
    req = struct.pack(">BBHH", addr, 0x03, start_reg, count)
    return req + modbus_crc(req)


def fc10_trigger(addr, cmd_body):
    msg = bytes([addr]) + cmd_body
    return msg + modbus_crc(msg)


CMD_STATUS = b"\x10\x16\x20\x00\x01\x02\x00\x00"


def read_fc03(ser, addr, start_reg, count, timeout=0.3):
    req = fc03_request(addr, start_reg, count)
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


def cold_bus(ser, wait=3.0):
    """Wait for bus to go cold."""
    time.sleep(wait)
    ser.reset_input_buffer()


def try_fc03(ser, label, rounds=5):
    """Try FC 0x03 reads, report success rate."""
    ok = 0
    for i in range(rounds):
        r = read_fc03(ser, ADDR, 0x1200, 4)
        if r is not None:
            ok += 1
    print(f"  {label}: {ok}/{rounds}")
    return ok


print(f"Port: {PORT}  Addr: 0x{ADDR:02X}\n")

ser = serial.Serial(PORT, baudrate=BAUD, timeout=0.1)
time.sleep(0.2)
ser.reset_input_buffer()

# --- Test 0: Baseline — FC 0x03 on cold bus ---
print("=== Test 0: Baseline — cold bus, no warmup ===")
cold_bus(ser, 3)
try_fc03(ser, "cold bus")

# --- Test 1: Raw garbage bytes as warmup ---
print("\n=== Test 1: Send garbage bytes, then FC 0x03 ===")
cold_bus(ser, 3)
ser.write(b"\xff\xff\xff\xff\xff\xff\xff\xff")
time.sleep(0.05)
ser.reset_input_buffer()
try_fc03(ser, "after 8 garbage bytes")

# --- Test 2: Valid FC 0x10 trigger, drain, then FC 0x03 ---
print("\n=== Test 2: FC 0x10 trigger + drain, then FC 0x03 ===")
cold_bus(ser, 3)
ser.write(fc10_trigger(ADDR, CMD_STATUS))
time.sleep(0.3)  # let response arrive
drained = 0
while ser.in_waiting:
    drained += len(ser.read(ser.in_waiting))
    time.sleep(0.01)
ser.reset_input_buffer()
print(f"  (drained {drained} bytes from FC 0x10)")
try_fc03(ser, "after FC 0x10 + drain")

# --- Test 3: FC 0x03 to different address, then to target ---
print("\n=== Test 3: FC 0x03 to addr 0x02, then to target ===")
cold_bus(ser, 3)
r = read_fc03(ser, 0x02, 0x1200, 2)
print(f"  FC03 to 0x02: {'OK' if r else 'FAIL'}")
try_fc03(ser, f"FC03 to 0x{ADDR:02X} after")

# --- Test 4: FC 0x03 request bytes (no response expected), then read ---
print("\n=== Test 4: Send FC 0x03 request frame only (no read), then FC 0x03 ===")
cold_bus(ser, 3)
# Send a valid FC 0x03 request but don't read the response
ser.reset_input_buffer()
ser.write(fc03_request(ADDR, 0x1200, 2))
time.sleep(0.1)
ser.reset_input_buffer()  # discard any response
try_fc03(ser, "after blind FC 0x03")

# --- Test 5: Break character / bus assertion ---
print("\n=== Test 5: Serial break (TX held low 10ms), then FC 0x03 ===")
cold_bus(ser, 3)
ser.send_break(duration=0.01)
time.sleep(0.05)
ser.reset_input_buffer()
try_fc03(ser, "after break")

# --- Test 6: Multiple FC 0x10 triggers in rapid succession ---
print("\n=== Test 6: 5x FC 0x10 rapid fire, drain all, then FC 0x03 ===")
cold_bus(ser, 3)
for _ in range(5):
    ser.write(fc10_trigger(ADDR, CMD_STATUS))
    time.sleep(0.05)
time.sleep(0.5)
drained = 0
while ser.in_waiting:
    drained += len(ser.read(ser.in_waiting))
    time.sleep(0.01)
ser.reset_input_buffer()
print(f"  (drained {drained} bytes)")
try_fc03(ser, "after 5x FC 0x10")

# --- Test 7: FC 0x10 then immediate FC 0x03 (no drain) ---
print("\n=== Test 7: FC 0x10 then IMMEDIATE FC 0x03 (no drain) ===")
cold_bus(ser, 3)
ser.write(fc10_trigger(ADDR, CMD_STATUS))
time.sleep(0.01)  # minimal gap
# Don't drain — just send FC 0x03 immediately
try_fc03(ser, "immediate after FC 0x10")

# --- Test 8: Sustained FC 0x03 after successful FC 0x10 warmup ---
print("\n=== Test 8: FC 0x10 warmup, then 20x FC 0x03 sustained ===")
cold_bus(ser, 3)
ser.write(fc10_trigger(ADDR, CMD_STATUS))
time.sleep(0.3)
drained = 0
while ser.in_waiting:
    drained += len(ser.read(ser.in_waiting))
    time.sleep(0.01)
ser.reset_input_buffer()
print(f"  (drained {drained} bytes)")
results = []
for i in range(20):
    r = read_fc03(ser, ADDR, 0x1200, 4)
    results.append(r is not None)
ok_str = "".join("." if ok else "X" for ok in results)
ok_count = sum(results)
print(f"  20x FC 0x03: {ok_count}/20  pattern: {ok_str}")

# --- Test 9: Does the port need to stay open? Close and reopen ---
print("\n=== Test 9: FC 0x10, close port, reopen, FC 0x03 ===")
cold_bus(ser, 3)
ser.write(fc10_trigger(ADDR, CMD_STATUS))
time.sleep(0.3)
ser.close()
time.sleep(0.1)
ser = serial.Serial(PORT, baudrate=BAUD, timeout=0.1)
time.sleep(0.1)
ser.reset_input_buffer()
try_fc03(ser, "after port close/reopen")

ser.close()
print("\nDone.")
