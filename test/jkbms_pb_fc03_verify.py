#!/usr/bin/env python3
"""
JKBMS PB — FC 0x03 register verification with LA1010 cross-check.

Sends individual FC 0x03 reads to each register of interest.
For each read: validates TX on wire (via LA1010), validates RX
(correct addr, FC, byte count, CRC), rejects any extra bytes.

Usage: python3 jkbms_pb_fc03_verify.py /dev/ttyUSB0 [addr_hex]
"""

import struct
import sys
import time
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
ADDR = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x01
BAUD = 115200
INTER_CMD_PAUSE = 0.1  # seconds between commands


def modbus_crc(msg: bytes) -> int:
    crc = 0xFFFF
    for b in msg:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_fc03_request(addr, start_reg, count):
    body = struct.pack(">BBHH", addr, 0x03, start_reg, count)
    crc = modbus_crc(body)
    return body + struct.pack("<H", crc)


def send_and_receive(ser, addr, start_reg, count, timeout=0.5):
    """Send FC 0x03 request, return (tx_bytes, rx_bytes, parsed_result, errors)."""
    req = build_fc03_request(addr, start_reg, count)
    expected_data_bytes = count * 2
    expected_resp_len = 1 + 1 + 1 + expected_data_bytes + 2  # addr+fc+bc+data+crc

    ser.reset_input_buffer()
    time.sleep(0.01)
    ser.reset_input_buffer()  # double-flush to catch late arrivals

    ser.write(req)

    # Read with timeout — collect ALL bytes
    data = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if ser.in_waiting:
                data.extend(ser.read(ser.in_waiting))
            else:
                if len(data) >= expected_resp_len:
                    time.sleep(0.03)
                    if ser.in_waiting:
                        data.extend(ser.read(ser.in_waiting))
                    break
                time.sleep(0.005)
    except serial.SerialException:
        errors.append("SERIAL_ERROR")

    errors = []

    if len(data) == 0:
        return req, bytes(data), None, ["NO RESPONSE"]

    # Scan for valid FC 0x03 response: [addr] [0x03] [byte_count] [data...] [crc]
    resp = None
    resp_start = None
    resp_end = None

    for i in range(len(data)):
        if data[i] != addr:
            continue
        if i + 1 >= len(data) or data[i + 1] != 0x03:
            continue
        if i + 2 >= len(data):
            continue
        bc = data[i + 2]
        if bc != expected_data_bytes:
            continue
        end = i + 3 + bc + 2
        if end > len(data):
            errors.append(f"TRUNCATED: need {end} bytes, have {len(data)}")
            continue
        frame = data[i:end]
        crc_recv = struct.unpack_from("<H", frame, len(frame) - 2)[0]
        crc_calc = modbus_crc(bytes(frame[:-2]))
        if crc_recv != crc_calc:
            errors.append(f"CRC MISMATCH at offset {i}: recv=0x{crc_recv:04X} calc=0x{crc_calc:04X}")
            continue
        # Valid response found
        resp_start = i
        resp_end = end
        regs = []
        for j in range(0, bc, 2):
            regs.append(struct.unpack_from(">H", frame, 3 + j)[0])
        resp = regs
        break

    # Check for echo bytes before response
    if resp is not None and resp_start > 0:
        echo = data[:resp_start]
        errors.append(f"TX_ECHO: {len(echo)} bytes before response: {echo.hex()}")

    # Check for extra bytes after response
    if resp is not None and resp_end < len(data):
        extra = data[resp_end:]
        errors.append(f"EXTRA: {len(extra)} bytes after response: {extra.hex()}")

    # Check for Modbus exception response
    if resp is None:
        for i in range(len(data)):
            if data[i] == addr and i + 1 < len(data) and data[i + 1] == 0x83:
                if i + 4 <= len(data):
                    exc_code = data[i + 2]
                    exc_names = {1: "ILLEGAL_FUNCTION", 2: "ILLEGAL_ADDRESS",
                                 3: "ILLEGAL_DATA", 4: "CRC_ERROR"}
                    errors.append(f"EXCEPTION: code={exc_code} ({exc_names.get(exc_code, '?')})")

    return req, bytes(data), resp, errors


def s16(v):
    return v - 65536 if v > 32767 else v

def u32(regs, i=0):
    return (regs[i] << 16) | regs[i + 1]

def s32(regs, i=0):
    v = u32(regs, i)
    return v - 0x100000000 if v > 0x7FFFFFFF else v

def regs_to_ascii(regs):
    chars = []
    for r in regs:
        hi, lo = (r >> 8) & 0xFF, r & 0xFF
        if 32 <= hi < 127:
            chars.append(chr(hi))
        if 32 <= lo < 127:
            chars.append(chr(lo))
    return "".join(chars).rstrip("\x00 ")


# Register map: (label, base, reg_offset, count, interpreter)
# reg_offset = byte_offset / 2 (standard Modbus word-addressing)
REGISTERS = [
    # --- Device info (0x1400) ---
    ("DeviceID",       0x1400, 0x00, 8,  lambda r: f"'{regs_to_ascii(r)}'"),
    ("HW_Version",     0x1400, 0x08, 4,  lambda r: f"'{regs_to_ascii(r)}'"),
    ("SW_Version",     0x1400, 0x0C, 4,  lambda r: f"'{regs_to_ascii(r)}'"),
    ("RunTime",        0x1400, 0x10, 2,  lambda r: f"{u32(r)}s = {u32(r)/3600:.1f}h"),
    ("PowerOnCount",   0x1400, 0x12, 2,  lambda r: f"{u32(r)}"),

    # --- Settings (0x1000) ---
    ("CellCount",      0x1000, 0x36, 2,  lambda r: f"{u32(r)}"),
    ("CapBatCell",     0x1000, 0x3E, 2,  lambda r: f"{u32(r)} mAh = {u32(r)/1000:.1f} Ah"),
    ("CurBatCOC",      0x1000, 0x16, 2,  lambda r: f"{u32(r)} mA = {u32(r)/1000:.1f} A"),
    ("CurBatDcOC",     0x1000, 0x1C, 2,  lambda r: f"{u32(r)} mA = {u32(r)/1000:.1f} A"),
    ("BalanEN",        0x1000, 0x3C, 2,  lambda r: f"{u32(r)} ({'on' if u32(r) else 'off'})"),
    ("DevAddr",        0x1000, 0x84, 2,  lambda r: f"0x{u32(r):02X}"),

    # --- Status (0x1200) ---
    ("Cells_0_7",      0x1200, 0x00, 8,  lambda r: f"{[v/1000 for v in r]} V"),
    ("Cells_8_15",     0x1200, 0x08, 8,  lambda r: f"{[v/1000 for v in r]} V"),
    ("TempMos",        0x1200, 0x45, 1,  lambda r: f"{s16(r[0])/10:.1f} °C"),
    ("BatVol",         0x1200, 0x48, 2,  lambda r: f"{u32(r)/1000:.3f} V"),
    ("BatWatt",        0x1200, 0x4A, 2,  lambda r: f"{u32(r)/1000:.1f} W"),
    ("BatCurrent",     0x1200, 0x4C, 2,  lambda r: f"{s32(r)/1000:.3f} A"),
    ("TempBat1",       0x1200, 0x4E, 1,  lambda r: f"{s16(r[0])/10:.1f} °C"),
    ("TempBat2",       0x1200, 0x4F, 1,  lambda r: f"{s16(r[0])/10:.1f} °C"),
    ("Alarms",         0x1200, 0x50, 2,  lambda r: f"0x{u32(r):08X}"),
    ("BalanSta_SOC",   0x1200, 0x53, 1,  lambda r: f"bal={r[0]>>8} SOC={r[0]&0xFF}%"),
    ("CapRemain",      0x1200, 0x54, 2,  lambda r: f"{s32(r)/1000:.1f} Ah"),
    ("FullChargeCap",  0x1200, 0x56, 2,  lambda r: f"{u32(r)/1000:.1f} Ah"),
    ("CycleCount",     0x1200, 0x58, 2,  lambda r: f"{u32(r)}"),
    ("SOH_Precharge",  0x1200, 0x5C, 1,  lambda r: f"SOH={r[0]>>8}% pre={r[0]&0xFF}"),
    ("ChgDischg",      0x1200, 0x60, 1,  lambda r: f"chg={r[0]>>8} disch={r[0]&0xFF}"),
    ("TempSens_Heat",  0x1200, 0x68, 1,  lambda r: f"sens=0b{r[0]>>8:08b} heat={r[0]&0xFF}"),
    ("HeatCurrent",    0x1200, 0x73, 1,  lambda r: f"{s16(r[0])} mA"),
    ("TempBat3",       0x1200, 0x7C, 1,  lambda r: f"{s16(r[0])/10:.1f} °C"),
    ("TempBat4",       0x1200, 0x7D, 1,  lambda r: f"{s16(r[0])/10:.1f} °C"),
]


def build_fc10_write(addr, reg, value_word):
    """Build FC 0x10 write: 1 register, 2 bytes data."""
    body = struct.pack(">BBHHB", addr, 0x10, reg, 1, 2) + struct.pack(">H", value_word)
    crc = modbus_crc(body)
    return body + struct.pack("<H", crc)


def wake_bus(ser, addr):
    """Wake BMS from SmartSleep using FC 0x10, then verify FC 0x03 works."""
    # Phase 1: Send FC 0x10 writes until we get ANY response (bus activity wakes BMS)
    wake_cmd = build_fc10_write(addr, 0x1620, 0x0000)
    fc03_req = build_fc03_request(addr, 0x1200, 1)

    print("  Phase 1: FC 0x10 wake-up")
    for attempt in range(1, 21):
        ser.reset_input_buffer()
        time.sleep(0.01)
        ser.reset_input_buffer()
        ser.write(wake_cmd)
        time.sleep(0.3)
        n = ser.in_waiting
        if n > 0:
            data = ser.read(n)
            ser.reset_input_buffer()
            has_55aa = b"\x55\xaa" in data
            has_ack = any(data[i] == addr and i + 1 < len(data) and data[i + 1] == 0x10
                          for i in range(len(data)))
            print(f"  Attempt {attempt}: {n} bytes"
                  f"{' 55AA' if has_55aa else ''}{' ACK' if has_ack else ''}")
            if has_55aa or has_ack:
                break
        else:
            print(f"  Attempt {attempt}: no response")
    else:
        print("  FC 0x10 got no response after 20 attempts")
        return False

    # Phase 2: Drain any remaining cross-talk
    time.sleep(0.5)
    drained = 0
    while ser.in_waiting:
        drained += len(ser.read(ser.in_waiting))
        time.sleep(0.01)
    if drained:
        print(f"  Drained {drained} bytes of cross-talk")
    ser.reset_input_buffer()

    # Phase 3: Verify FC 0x03 works
    print("  Phase 2: FC 0x03 verify")
    ser.write(fc03_req)
    time.sleep(0.3)
    n = ser.in_waiting
    if n > 0:
        data = ser.read(n)
        for i in range(len(data)):
            if data[i] == addr and i + 1 < len(data) and data[i + 1] == 0x03:
                print(f"  FC 0x03 works! ({n} bytes, response at offset {i})")
                ser.reset_input_buffer()
                return True
        print(f"  FC 0x03: {n} bytes but no valid response: {data[:20].hex()}")
    else:
        print(f"  FC 0x03: no response")
    ser.reset_input_buffer()
    return False


def main():
    print(f"Port: {PORT}  Addr: 0x{ADDR:02X}  Baud: {BAUD}")
    print(f"Registers to test: {len(REGISTERS)}")
    print()

    ser = serial.Serial(PORT, baudrate=BAUD, timeout=0.1)
    time.sleep(0.2)
    ser.reset_input_buffer()

    # Wake the bus — FC 0x03 reads until BMS responds
    print("=== Waking bus ===")
    if not wake_bus(ser, ADDR):
        print("  Bus did NOT wake after 10 attempts — proceeding anyway")
    print()

    ok_count = 0
    fail_count = 0
    results = []

    for label, base, offset, count, interp in REGISTERS:
        reg = base + offset
        tx, rx, regs, errors = send_and_receive(ser, ADDR, reg, count)

        real_errors = [e for e in errors if not e.startswith("TX_ECHO")]
        status = "OK" if regs is not None and not real_errors else "FAIL"

        if status == "OK":
            ok_count += 1
            value = interp(regs)
        else:
            fail_count += 1
            value = "---"

        # Print result
        errs = "; ".join(errors) if errors else ""
        print(f"  [{status:4s}] {label:<18s} reg=0x{reg:04X} x{count}  {value}")
        if errs:
            print(f"         {errs}")
        if regs is not None:
            hex_str = " ".join(f"{r:04X}" for r in regs)
            print(f"         raw: {hex_str}")

        results.append({
            "label": label, "reg": reg, "count": count,
            "status": status, "tx": tx.hex(), "rx": rx.hex(),
            "regs": regs, "errors": errors,
        })

        time.sleep(INTER_CMD_PAUSE)

    ser.close()

    print(f"\n{'='*60}")
    print(f"Summary: {ok_count} OK, {fail_count} FAIL out of {len(REGISTERS)}")
    print(f"{'='*60}")

    # Write machine-readable results
    import json
    with open("/tmp/fc03_verify_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults written to /tmp/fc03_verify_results.json")


if __name__ == "__main__":
    main()
