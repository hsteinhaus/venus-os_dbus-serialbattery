#!/usr/bin/env python3
"""
Cross-talk reliability test for JKBMS PB RS485.

Sends a single command to one address and listens for a long time
to see if other BMS units respond.  Tests whether cross-talk is
reliable/intentional (all batteries respond to any bus activity).

Usage: python3 jkbms_pb_sniff.py /dev/ttyUSB0 [target_addr] [listen_seconds]
  target_addr:    hex address to send to (default: 0x01)
  listen_seconds: how long to listen (default: 5)
"""

import sys
import time
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
TARGET = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x01
LISTEN = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
BAUD = 115200
ROUNDS = 5

CMD_STATUS = b"\x10\x16\x20\x00\x01\x02\x00\x00"


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


def build_cmd(addr: int, cmd_body: bytes) -> bytes:
    msg = bytes([addr]) + cmd_body
    return msg + modbus_crc(msg)


def drain(ser, quiet_ms=100):
    """Drain until bus quiet for quiet_ms."""
    total = 0
    quiet_since = time.monotonic()
    threshold = quiet_ms / 1000.0
    while time.monotonic() - quiet_since < threshold:
        if ser.in_waiting:
            total += len(ser.read(ser.in_waiting))
            quiet_since = time.monotonic()
        else:
            time.sleep(0.005)
    ser.reset_input_buffer()
    return total


def read_timed(ser, duration):
    """Read everything for `duration` seconds. Return list of (timestamp_ms, chunk)."""
    start = time.monotonic()
    events = []
    while time.monotonic() - start < duration:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting)
            t_ms = (time.monotonic() - start) * 1000
            events.append((t_ms, chunk))
        else:
            time.sleep(0.005)
    return events


def find_all_headers(data):
    """Find all 0x55AA positions."""
    positions = []
    start = 0
    while True:
        pos = data.find(b"\x55\xaa", start)
        if pos < 0:
            break
        positions.append(pos)
        start = pos + 2
    return positions


def extract_responses(data, headers):
    """Extract (header_offset, payload_size, address_hint) for each header."""
    responses = []
    for i, pos in enumerate(headers):
        end = headers[i + 1] if i + 1 < n else len(data)
        payload = data[pos:end]
        size = len(payload)
        # Try to extract address hint from payload
        # Format appears to be: 55 AA EB 90 ADDR ...
        addr_hint = None
        if len(payload) >= 5 and payload[2:4] == b"\xeb\x90":
            addr_hint = payload[4]
        responses.append((pos, size, addr_hint))
    return responses


TEST = sys.argv[4] if len(sys.argv) > 4 else "all"

print(f"Port: {PORT}  Baud: {BAUD}")
print(f"Target: 0x{TARGET:02X}  Listen: {LISTEN}s  Rounds: {ROUNDS}  Test: {TEST}")
print()

def report_events(events):
    """Print timeline and analyse headers. Returns set of addresses seen."""
    all_data = bytearray()
    print(f"\n  Timeline:")
    for t_ms, chunk in events:
        all_data.extend(chunk)
        hdr_in_chunk = b"\x55\xaa" in chunk
        marker = " <-- 0x55AA" if hdr_in_chunk else ""
        print(f"    +{t_ms:7.1f}ms  {len(chunk):4d} bytes{marker}")

    headers = find_all_headers(all_data)
    n = len(headers)
    print(f"\n  Total: {len(all_data)} bytes, {n} header(s)")

    addrs_seen = set()
    for i, pos in enumerate(headers):
        end = headers[i + 1] if i + 1 < n else len(all_data)
        payload = all_data[pos:end]
        size = len(payload)
        addr_hint = None
        if len(payload) >= 5 and payload[2:4] == b"\xeb\x90":
            addr_hint = payload[4]
            addrs_seen.add(addr_hint)
        addr_str = f"addr=0x{addr_hint:02X}" if addr_hint is not None else "addr=?"
        print(f"    header[{i}] at offset {pos}: {size} bytes, {addr_str}")
        print(f"      data: {payload[:20].hex()}")

    return addrs_seen


# ============================================================
# TEST A: Cross-talk reliability (send command, listen long)
# ============================================================
if TEST in ("all", "crosstalk"):
    round_results = []

    with serial.Serial(PORT, baudrate=BAUD, timeout=0.1) as ser:
        time.sleep(0.5)
        drain(ser, 100)

        for rnd in range(ROUNDS):
            print(f"{'='*60}")
            print(f"TEST-A ROUND {rnd+1}/{ROUNDS}: send to 0x{TARGET:02X}, listen {LISTEN}s")
            print(f"{'='*60}")

            time.sleep(1.0)
            stale = drain(ser, 200)
            if stale:
                print(f"  Pre-drain: {stale} stale bytes")

            cmd = build_cmd(TARGET, CMD_STATUS)
            ser.reset_input_buffer()
            print(f"  TX to 0x{TARGET:02X}: {cmd.hex()}")
            ser.write(cmd)

            events = read_timed(ser, LISTEN)
            addrs_seen = report_events(events)
            round_results.append(addrs_seen)
            print(f"\n  Addresses that responded: {sorted('0x%02X' % a for a in addrs_seen)}")

    print(f"\n{'='*60}")
    print(f"TEST-A SUMMARY — {ROUNDS} rounds, {LISTEN}s listen each")
    print(f"{'='*60}")
    print(f"  Command sent to: 0x{TARGET:02X}")
    all_addrs = set()
    for i, addrs in enumerate(round_results):
        all_addrs |= addrs
        print(f"  Round {i+1}: {sorted('0x%02X' % a for a in addrs)}")

    print(f"\n  All addresses seen: {sorted('0x%02X' % a for a in all_addrs)}")
    consistent = all(round_results[0] == r for r in round_results)
    print(f"  Consistent: {'YES' if consistent else 'NO'}")

    if len(all_addrs) > 1:
        always = set.intersection(*round_results) if round_results else set()
        sometimes = all_addrs - always
        print(f"  Always respond:    {sorted('0x%02X' % a for a in always)}")
        print(f"  Sometimes respond: {sorted('0x%02X' % a for a in sometimes)}")


# ============================================================
# TEST C: Address filtering — does the BMS check the address?
# ============================================================
if TEST in ("all", "addressing"):
    print(f"\n{'='*60}")
    print(f"TEST-C: ADDRESS FILTERING")
    print(f"{'='*60}")

    with serial.Serial(PORT, baudrate=BAUD, timeout=0.1) as ser:
        time.sleep(0.5)
        drain(ser, 100)

        # Warm up: send a few commands to known addresses to wake the bus
        print("  Warming up bus...")
        for warmup_addr in [TARGET]:
            ser.reset_input_buffer()
            ser.write(build_cmd(warmup_addr, CMD_STATUS))
            read_timed(ser, 0.5)
        for warmup_addr in [TARGET]:
            ser.reset_input_buffer()
            ser.write(build_cmd(warmup_addr, CMD_STATUS))
            read_timed(ser, 0.5)
        drain(ser, 200)
        print("  Bus warm.")

        # Verify control works before running tests
        print(f"\n--- control: addr=0x{TARGET:02X} (must work) ---")
        time.sleep(0.5)
        drain(ser, 100)
        ser.reset_input_buffer()
        cmd = build_cmd(TARGET, CMD_STATUS)
        ser.write(cmd)
        events = read_timed(ser, 1.0)
        ctrl_data = bytearray()
        for _, chunk in events:
            ctrl_data.extend(chunk)
        ctrl_ok = b"\x55\xaa" in ctrl_data
        if ctrl_ok:
            print(f"  Control OK: {len(ctrl_data)} bytes, 0x55AA present")
        else:
            print(f"  Control FAILED: {len(ctrl_data)} bytes, no 0x55AA — results unreliable!")

        tests = [
            ("broadcast 0x00", 0x00, CMD_STATUS),
            ("non-existent 0xFF", 0xFF, CMD_STATUS),
            ("target 0x{:02X} (repeat control)".format(TARGET), TARGET, CMD_STATUS),
        ]

        for label, addr, cmd_body in tests:
            print(f"\n--- {label} ---")
            time.sleep(1.0)
            drain(ser, 200)
            ser.reset_input_buffer()

            cmd = build_cmd(addr, cmd_body)
            print(f"  TX addr=0x{addr:02X}: {cmd.hex()}")
            ser.write(cmd)
            events = read_timed(ser, 2.0)
            if not events:
                print("  RX: NO RESPONSE")
            else:
                addrs_seen = report_events(events)
                print(f"  Addresses seen: {sorted('0x%02X' % a for a in addrs_seen)}")

        # Test: send raw command bytes without address (CRC over command only)
        print(f"\n--- raw command (no address byte, CRC over cmd only) ---")
        time.sleep(1.0)
        drain(ser, 200)
        ser.reset_input_buffer()
        raw_crc = modbus_crc(CMD_STATUS)
        raw_frame = CMD_STATUS + raw_crc
        print(f"  TX (no addr): {raw_frame.hex()}")
        ser.write(raw_frame)
        events = read_timed(ser, 2.0)
        if not events:
            print("  RX: NO RESPONSE")
        else:
            addrs_seen = report_events(events)
            print(f"  Addresses seen: {sorted('0x%02X' % a for a in addrs_seen)}")

        # Test: does the BMS check CRC at all? Send valid addr but corrupted CRC
        print(f"\n--- corrupted CRC (addr=0x{TARGET:02X}) ---")
        time.sleep(1.0)
        drain(ser, 200)
        ser.reset_input_buffer()
        cmd = build_cmd(TARGET, CMD_STATUS)
        # Flip last byte of CRC
        bad_cmd = cmd[:-1] + bytes([cmd[-1] ^ 0xFF])
        print(f"  TX (bad CRC): {bad_cmd.hex()}")
        ser.write(bad_cmd)
        events = read_timed(ser, 2.0)
        all_bytes = bytearray()
        for _, chunk in events:
            all_bytes.extend(chunk)
        has_header = b"\x55\xaa" in all_bytes
        if not has_header:
            echo_only = len(all_bytes) <= len(bad_cmd)
            if echo_only:
                print(f"  RX: {len(all_bytes)} bytes (TX echo only, no BMS response) — CRC is checked")
            else:
                print(f"  RX: {len(all_bytes)} bytes, no 0x55AA — unclear")
                print(f"      data: {all_bytes[:40].hex()}")
        else:
            addrs_seen = report_events(events)
            print(f"  Addresses seen: {sorted('0x%02X' % a for a in addrs_seen)}")
            print(f"  *** BMS does NOT verify CRC! ***")

        # Post-control: verify bus still works
        print(f"\n--- post-control: addr=0x{TARGET:02X} ---")
        time.sleep(1.0)
        drain(ser, 200)
        ser.reset_input_buffer()
        ser.write(build_cmd(TARGET, CMD_STATUS))
        events = read_timed(ser, 1.0)
        post_data = bytearray()
        for _, chunk in events:
            post_data.extend(chunk)
        post_ok = b"\x55\xaa" in post_data
        print(f"  {'OK' if post_ok else 'FAIL'}: {len(post_data)} bytes")


# ============================================================
# TEST D: Standard Modbus FC 0x03 reads vs proprietary FC 0x10 triggers
# ============================================================
if TEST in ("all", "modbus"):
    print(f"\n{'='*60}")
    print(f"TEST-D: STANDARD MODBUS FC 0x03 vs PROPRIETARY FC 0x10")
    print(f"{'='*60}")
    print(f"  Official protocol uses FC 0x03 (Read Holding Registers)")
    print(f"  Driver uses FC 0x10 (Write Multiple Registers) as trigger")
    print()

    # Official register bases from JKBMS RS485 Modbus V1.0/V1.1:
    #   0x1000 = Settings (RW)
    #   0x1200 = Status/telemetry (R)
    #   0x1400 = Device info (R)
    #   0x1600 = Calibration/control (W)
    #
    # Modbus register addresses are 16-bit word addresses.
    # The PDF byte offsets divide by 2 for register count.

    # FC 0x03: addr + 03 + start_reg(2) + reg_count(2) + crc
    def build_fc03(addr, start_reg, reg_count):
        msg = bytes([addr, 0x03,
                     (start_reg >> 8) & 0xFF, start_reg & 0xFF,
                     (reg_count >> 8) & 0xFF, reg_count & 0xFF])
        return msg + modbus_crc(msg)

    # Current driver FC 0x10 commands (for comparison)
    def build_fc10_trigger(addr, cmd_body):
        msg = bytes([addr]) + cmd_body
        return msg + modbus_crc(msg)

    CMD_STATUS_BODY = b"\x10\x16\x20\x00\x01\x02\x00\x00"
    CMD_SETTINGS_BODY = b"\x10\x16\x1e\x00\x01\x02\x00\x00"
    CMD_ABOUT_BODY = b"\x10\x16\x1c\x00\x01\x02\x00\x00"

    with serial.Serial(PORT, baudrate=BAUD, timeout=0.1) as ser:
        time.sleep(0.5)
        drain(ser, 100)

        # Warm up
        print("  Warming up bus...")
        for _ in range(3):
            ser.reset_input_buffer()
            ser.write(build_fc10_trigger(TARGET, CMD_STATUS_BODY))
            read_timed(ser, 0.5)
        drain(ser, 200)
        print("  Bus warm.\n")

        tests = []

        # --- FC 0x03 reads (official protocol) ---
        # Read 10 registers from each base to start small
        tests.append(("FC03 status  0x1200 x10", build_fc03(TARGET, 0x1200, 10)))
        tests.append(("FC03 settings 0x1000 x10", build_fc03(TARGET, 0x1000, 10)))
        tests.append(("FC03 about   0x1400 x10", build_fc03(TARGET, 0x1400, 10)))

        # Read larger blocks
        tests.append(("FC03 status  0x1200 x80", build_fc03(TARGET, 0x1200, 0x80)))
        tests.append(("FC03 settings 0x1000 x80", build_fc03(TARGET, 0x1000, 0x80)))

        # FC 0x03 to a wrong address (should get no response if filtering works)
        other_addr = 0x02 if TARGET != 0x02 else 0x03
        tests.append((f"FC03 status  0x1200 x10 addr=0x{other_addr:02X} (wrong)", build_fc03(other_addr, 0x1200, 10)))

        # --- FC 0x10 triggers (current driver, for comparison) ---
        tests.append(("FC10 trigger status  (driver)", build_fc10_trigger(TARGET, CMD_STATUS_BODY)))
        tests.append(("FC10 trigger settings (driver)", build_fc10_trigger(TARGET, CMD_SETTINGS_BODY)))

        # FC 0x10 trigger to wrong address
        tests.append((f"FC10 trigger status addr=0x{other_addr:02X} (wrong)", build_fc10_trigger(other_addr, CMD_STATUS_BODY)))

        for label, cmd in tests:
            print(f"--- {label} ---")
            time.sleep(0.5)
            drain(ser, 100)
            ser.reset_input_buffer()

            print(f"  TX ({len(cmd)}): {cmd.hex()}")
            ser.write(cmd)
            events = read_timed(ser, 1.5)

            all_bytes = bytearray()
            for _, chunk in events:
                all_bytes.extend(chunk)

            if not all_bytes:
                print(f"  RX: NO RESPONSE\n")
                continue

            # Check for standard Modbus FC 0x03 response
            # Format: addr + 03 + byte_count + data... + crc
            has_55aa = b"\x55\xaa" in all_bytes
            has_fc03 = len(all_bytes) >= 3 and all_bytes[1] == 0x03

            if has_fc03 and not has_55aa:
                byte_count = all_bytes[2]
                resp_addr = all_bytes[0]
                print(f"  RX: STANDARD MODBUS FC 0x03 response!")
                print(f"    addr=0x{resp_addr:02X}, byte_count={byte_count}, total={len(all_bytes)} bytes")
                # Show data (skip addr+fc+count header, skip 2-byte CRC at end)
                data_start = 3
                data_end = 3 + byte_count
                if data_end <= len(all_bytes):
                    print(f"    data: {all_bytes[data_start:min(data_end, data_start+40)].hex()}")
                # Verify CRC
                if len(all_bytes) >= data_end + 2:
                    msg_part = all_bytes[:data_end]
                    expected_crc = modbus_crc(bytes(msg_part))
                    actual_crc = bytes(all_bytes[data_end:data_end+2])
                    crc_ok = expected_crc == actual_crc
                    print(f"    CRC: {'OK' if crc_ok else 'MISMATCH'}")
            elif has_55aa:
                headers = find_all_headers(all_bytes)
                print(f"  RX: PROPRIETARY 0x55AA response, {len(all_bytes)} bytes, {len(headers)} header(s)")
                for i, pos in enumerate(headers):
                    end = headers[i+1] if i+1 < len(headers) else len(all_bytes)
                    payload = all_bytes[pos:end]
                    addr_hint = None
                    if len(payload) >= 5 and payload[2:4] == b"\xeb\x90":
                        addr_hint = payload[4]
                    astr = f"addr=0x{addr_hint:02X}" if addr_hint is not None else "addr=?"
                    print(f"    header[{i}] at {pos}: {len(payload)} bytes, {astr}")
            else:
                # Check for Modbus error response (addr + FC|0x80 + error_code + crc)
                if len(all_bytes) >= 3 and (all_bytes[1] & 0x80):
                    fc = all_bytes[1] & 0x7F
                    err = all_bytes[2]
                    err_names = {1: "illegal function", 2: "illegal address",
                                 3: "illegal data", 4: "CRC error"}
                    print(f"  RX: MODBUS ERROR — FC=0x{fc:02X}, error=0x{err:02X} ({err_names.get(err, '?')})")
                else:
                    print(f"  RX: {len(all_bytes)} bytes, unrecognized: {all_bytes[:40].hex()}")
            print()


# ============================================================
# TEST E: FC 0x03 register count limits and full register map
# ============================================================
if TEST in ("all", "fc03map"):
    print(f"\n{'='*60}")
    print(f"TEST-E: FC 0x03 REGISTER COUNT LIMITS & FULL MAP")
    print(f"{'='*60}")

    def build_fc03(addr, start_reg, reg_count):
        msg = bytes([addr, 0x03,
                     (start_reg >> 8) & 0xFF, start_reg & 0xFF,
                     (reg_count >> 8) & 0xFF, reg_count & 0xFF])
        return msg + modbus_crc(msg)

    def parse_fc03_response(raw):
        """Parse FC03 response, return (addr, data_bytes) or None."""
        # Skip TX echo: find addr+0x03 pattern
        for i in range(len(raw) - 4):
            if raw[i+1] == 0x03 and raw[i+2] < 0xFF:
                addr = raw[i]
                bc = raw[i+2]
                data_start = i + 3
                data_end = data_start + bc
                if data_end + 2 <= len(raw):
                    data = bytes(raw[data_start:data_end])
                    # verify CRC
                    msg = bytes(raw[i:data_end])
                    expected = modbus_crc(msg)
                    actual = bytes(raw[data_end:data_end+2])
                    if expected == actual:
                        return (addr, data)
                    else:
                        # try without CRC check (might be truncated)
                        return (addr, data)
        return None

    with serial.Serial(PORT, baudrate=BAUD, timeout=0.1) as ser:
        time.sleep(0.5)
        drain(ser, 100)

        # Warm up with FC10 triggers
        print("  Warming up...")
        for _ in range(3):
            ser.reset_input_buffer()
            ser.write(build_cmd(TARGET, CMD_STATUS))
            read_timed(ser, 0.5)
        drain(ser, 200)
        print("  Ready.\n")

        # --- Find max register count for each base ---
        for base_name, base_addr in [("status 0x1200", 0x1200),
                                      ("settings 0x1000", 0x1000),
                                      ("about 0x1400", 0x1400)]:
            print(f"  === {base_name}: finding max register count ===")
            for count in [10, 20, 40, 60, 80, 100, 125]:
                time.sleep(0.3)
                drain(ser, 50)
                ser.reset_input_buffer()
                cmd = build_fc03(TARGET, base_addr, count)
                ser.write(cmd)
                events = read_timed(ser, 1.0)
                raw = bytearray()
                for _, chunk in events:
                    raw.extend(chunk)
                result = parse_fc03_response(raw)
                if result:
                    addr, data = result
                    print(f"    count={count:3d}: OK, {len(data)} data bytes (={len(data)//2} regs) from addr 0x{addr:02X}")
                else:
                    has_55aa = b"\x55\xaa" in raw
                    print(f"    count={count:3d}: FAIL ({len(raw)} raw bytes, 55AA={'yes' if has_55aa else 'no'})")
                    break  # no point trying larger
            print()

        # --- Read full register maps with working count ---
        print("  === Full register reads ===\n")
        reads = [
            ("status", 0x1200, [
                (0, 10, "CellVol0-9"),
                (10, 10, "CellVol10-19"),
                (20, 10, "CellVol20-29"),
                (30, 10, "CellVol30+CellSta+CellVolAve+CellVdifMax+MaxMin"),
                (40, 10, "WireRes0-9"),
                (50, 10, "WireRes10-19"),
                (60, 10, "WireRes20-29"),
                (70, 10, "WireRes30-31+TempMos+WireResSta+BatVol"),
                (80, 10, "BatWatt+BatCurrent+TempBat1-2+Alarms"),
                (90, 10, "BalanCurrent+BalanSta+SOC+SOCCapRemain+FullCap+Cycles"),
                (100, 10, "CycleCap+SOH+Precharge+UserAlarm+RunTime+ChgDchg"),
                (110, 10, "UserAlarm2+TimeDcOCPR..TimeOVPR+TempSensor+Heating"),
                (120, 10, "Reserved+TimeEmergency+BatCurCorrect+VolChargCur+VolDischargCur+BatVolCorrect"),
            ]),
            ("settings", 0x1000, [
                (0, 10, "VolSmartSleep..VolSOC0%"),
                (10, 10, "VolRCV..TIMBatCOCPRDly (V1.1) or VolSysPwrOff.."),
                (60, 10, "TMPBatCUT..BalanEN"),
                (130, 4, "DevAddr+TIMProdischarge"),
            ]),
            ("about", 0x1400, [
                (0, 10, "ManufacturerDeviceID+HWVer"),
                (10, 10, "SWVer+ODDRunTime+PWROnTimes"),
            ]),
        ]

        # Test address filtering: read status from each address
        print("  === Address filtering test (FC03 status x10) ===")
        for addr in [0x01, 0x02, 0x03, 0x04]:
            time.sleep(0.3)
            drain(ser, 50)
            ser.reset_input_buffer()
            cmd = build_fc03(addr, 0x1200, 10)
            ser.write(cmd)
            events = read_timed(ser, 1.0)
            raw = bytearray()
            for _, chunk in events:
                raw.extend(chunk)
            result = parse_fc03_response(raw)
            if result:
                resp_addr, data = result
                # Show first few cell voltages
                cells = []
                for i in range(0, min(len(data), 20), 2):
                    cells.append(int.from_bytes(data[i:i+2], 'big'))
                print(f"    addr=0x{addr:02X}: resp from 0x{resp_addr:02X}, cells(mV)={cells[:5]}")
            else:
                print(f"    addr=0x{addr:02X}: NO VALID RESPONSE ({len(raw)} raw bytes)")
        print()

        # Read key status fields
        print("  === Key status fields (addr=0x{:02X}) ===".format(TARGET))
        # Read a big chunk: status registers 0-80 in blocks of 40
        for block_start in [0, 40, 80, 120]:
            time.sleep(0.3)
            drain(ser, 50)
            ser.reset_input_buffer()
            cmd = build_fc03(TARGET, 0x1200 + block_start, 40)
            ser.write(cmd)
            events = read_timed(ser, 1.0)
            raw = bytearray()
            for _, chunk in events:
                raw.extend(chunk)
            result = parse_fc03_response(raw)
            if result:
                resp_addr, data = result
                print(f"    regs {block_start}-{block_start+39}: {len(data)} bytes")
                print(f"      hex: {data[:40].hex()}")
            else:
                print(f"    regs {block_start}-{block_start+39}: FAIL")


# ============================================================
# TEST F: FC 0x03 after FC 0x10 priming — tight coupling test
# ============================================================
if TEST in ("all", "fc03tight"):
    print(f"\n{'='*60}")
    print(f"TEST-F: FC 0x03 AFTER FC 0x10 PRIMING")
    print(f"{'='*60}")

    def build_fc03(addr, start_reg, reg_count):
        msg = bytes([addr, 0x03,
                     (start_reg >> 8) & 0xFF, start_reg & 0xFF,
                     (reg_count >> 8) & 0xFF, reg_count & 0xFF])
        return msg + modbus_crc(msg)

    def parse_fc03_response(raw):
        """Find FC03 response in raw bytes (skipping TX echo)."""
        for i in range(len(raw) - 4):
            if raw[i+1] == 0x03 and raw[i+2] < 0xFE:
                addr = raw[i]
                bc = raw[i+2]
                data_start = i + 3
                data_end = data_start + bc
                if data_end <= len(raw):
                    return (addr, bytes(raw[data_start:data_end]))
        return None

    with serial.Serial(PORT, baudrate=BAUD, timeout=0.1) as ser:
        time.sleep(0.5)
        drain(ser, 100)

        # Heavy warmup: keep sending FC10 until we get a valid 0x55AA response
        print("  Heavy warmup (FC10 until 0x55AA response)...")
        for attempt in range(20):
            ser.reset_input_buffer()
            ser.write(build_cmd(TARGET, CMD_STATUS))
            events = read_timed(ser, 0.5)
            raw = bytearray()
            for _, chunk in events:
                raw.extend(chunk)
            if b"\x55\xaa" in raw:
                print(f"    Got 0x55AA response on attempt {attempt+1} ({len(raw)} bytes)")
                break
            time.sleep(0.2)
        else:
            print("    WARNING: No 0x55AA after 20 attempts!")
        drain(ser, 100)

        # Now test FC03 immediately after confirmed FC10 success
        fc03_tests = [
            ("status 0x1200 x10", 0x1200, 10),
            ("status 0x1200 x20", 0x1200, 20),
            ("status 0x1200 x40", 0x1200, 40),
            ("status 0x1200 x60", 0x1200, 60),
            ("status 0x1200 x80", 0x1200, 80),
            ("settings 0x1000 x10", 0x1000, 10),
            ("settings 0x1000 x40", 0x1000, 40),
            ("about 0x1400 x10", 0x1400, 10),
            ("about 0x1400 x20", 0x1400, 20),
        ]

        for label, base, count in fc03_tests:
            # Prime with FC10 first
            ser.reset_input_buffer()
            ser.write(build_cmd(TARGET, CMD_STATUS))
            read_timed(ser, 0.3)
            drain(ser, 50)

            # Now FC03
            ser.reset_input_buffer()
            cmd = build_fc03(TARGET, base, count)
            ser.write(cmd)
            events = read_timed(ser, 1.0)
            raw = bytearray()
            for _, chunk in events:
                raw.extend(chunk)

            result = parse_fc03_response(raw)
            if result:
                addr, data = result
                print(f"  {label:30s} OK  {len(data)} bytes from 0x{addr:02X}: {data[:20].hex()}")
            else:
                has_55aa = b"\x55\xaa" in raw
                print(f"  {label:30s} FAIL ({len(raw)} bytes, 55AA={'yes' if has_55aa else 'no'})")
                if raw:
                    print(f"    raw: {raw[:40].hex()}")

        # Address filtering: FC03 to each address (with FC10 prime before each)
        print(f"\n  === Address filtering (FC03 status x10) ===")
        for addr in [0x01, 0x02, 0x03, 0x04, 0xFF]:
            ser.reset_input_buffer()
            ser.write(build_cmd(TARGET, CMD_STATUS))
            read_timed(ser, 0.3)
            drain(ser, 50)

            ser.reset_input_buffer()
            cmd = build_fc03(addr, 0x1200, 10)
            ser.write(cmd)
            events = read_timed(ser, 1.0)
            raw = bytearray()
            for _, chunk in events:
                raw.extend(chunk)

            result = parse_fc03_response(raw)
            if result:
                resp_addr, data = result
                cells = [int.from_bytes(data[i:i+2], 'big') for i in range(0, min(len(data), 10), 2)]
                print(f"    send to 0x{addr:02X}: resp 0x{resp_addr:02X}, cells(mV)={cells}")
            else:
                print(f"    send to 0x{addr:02X}: NO RESPONSE ({len(raw)} bytes)")

        # CRC check: FC03 with bad CRC
        print(f"\n  === CRC filtering (FC03 status x10, bad CRC) ===")
        ser.reset_input_buffer()
        ser.write(build_cmd(TARGET, CMD_STATUS))
        read_timed(ser, 0.3)
        drain(ser, 50)

        ser.reset_input_buffer()
        good_cmd = build_fc03(TARGET, 0x1200, 10)
        bad_cmd = good_cmd[:-1] + bytes([good_cmd[-1] ^ 0xFF])
        ser.write(bad_cmd)
        events = read_timed(ser, 1.0)
        raw = bytearray()
        for _, chunk in events:
            raw.extend(chunk)
        result = parse_fc03_response(raw)
        if result:
            print(f"    Bad CRC: GOT RESPONSE — CRC NOT checked!")
        else:
            print(f"    Bad CRC: NO RESPONSE — CRC IS checked ({len(raw)} bytes)")


# ============================================================
# TEST G: minimalmodbus FC 0x03 — register limits, temps, serial#
# ============================================================
if TEST in ("all", "mmb"):
    import sys as _sys

    _sys.path.insert(0, "/data/apps/dbus-serialbattery")
    import ext.minimalmodbus as minimalmodbus

    print(f"\n{'='*60}")
    print(f"TEST-G: MINIMALMODBUS FC 0x03 — REGISTER LIMITS & DATA")
    print(f"{'='*60}")

    slave = TARGET
    mbdev = minimalmodbus.Instrument(PORT, slaveaddress=slave, mode="rtu", close_port_after_each_call=True)
    mbdev.serial.baudrate = BAUD
    mbdev.serial.parity = "N"
    mbdev.serial.stopbits = 1
    mbdev.serial.timeout = 0.5
    mbdev.handle_local_echo = False

    def to_signed16(v):
        return v - 65536 if v > 32767 else v

    def to_signed32(v):
        return v - 0x100000000 if v > 0x7FFFFFFF else v

    def regs_to_ascii(regs):
        chars = []
        for r in regs:
            chars.append(chr((r >> 8) & 0x7F) if (r >> 8) else "")
            chars.append(chr(r & 0x7F) if (r & 0xFF) else "")
        return "".join(chars).split("\x00")[0]

    def u32(regs, i):
        return (regs[i] << 16) | regs[i + 1]

    def s32(regs, i):
        return to_signed32(u32(regs, i))

    # --- Bus warmup ---
    print("\n  Bus warmup...")
    for attempt in range(10):
        try:
            mbdev.read_registers(0x1200, 2)
            print(f"    Warm on attempt {attempt + 1}")
            break
        except Exception as e:
            if attempt == 9:
                print(f"    FAILED after 10 attempts: {e}")
                print("    Try running with driver warm (stop driver, run immediately)")

    # --- 1. Max register count per base ---
    print("\n  === Max register count ===")
    for base_name, base in [("status 0x1200", 0x1200), ("settings 0x1000", 0x1000), ("about 0x1400", 0x1400)]:
        max_ok = 0
        for count in [10, 20, 30, 40, 50, 60, 70, 80, 100, 125]:
            try:
                regs = mbdev.read_registers(base, count)
                max_ok = count
                print(f"    {base_name} count={count:3d}: OK ({len(regs)} regs)")
            except Exception as e:
                print(f"    {base_name} count={count:3d}: FAIL ({type(e).__name__})")
                break
        print(f"    → max confirmed: {max_ok}\n")

    # --- 2. Read device info (0x1400) ---
    print("  === Device info (0x1400) ===")
    try:
        info = mbdev.read_registers(0x1400, 20)
        device_id = regs_to_ascii(info[0:8])
        hw_ver = regs_to_ascii(info[8:12])
        sw_ver = regs_to_ascii(info[12:16])
        runtime = u32(info, 16)
        pwr_on = u32(info, 18)
        print(f"    DeviceID:  {device_id}")
        print(f"    HW ver:    {hw_ver}")
        print(f"    SW ver:    {sw_ver}")
        print(f"    Runtime:   {runtime}s ({runtime/3600:.1f}h)")
        print(f"    PWR-on:    {pwr_on}")
    except Exception as e:
        print(f"    FAIL: {e}")

    # Probe for serial number beyond offset 36
    print("\n  === Probing 0x1400 beyond offset 36 (serial number?) ===")
    for start in [0x1414, 0x1417, 0x1420, 0x1430]:
        try:
            regs = mbdev.read_registers(start, 10)
            text = regs_to_ascii(regs)
            raw = " ".join(f"{r:04x}" for r in regs)
            label = f"text='{text}'" if any(32 <= (r >> 8) < 127 or 32 <= (r & 0xFF) < 127 for r in regs) else "no ASCII"
            print(f"    0x{start:04X}: {label}  raw=[{raw}]")
        except Exception as e:
            print(f"    0x{start:04X}: {type(e).__name__}")

    # --- 3. Read status (0x1200) — cell voltages + key fields ---
    print("\n  === Status data (0x1200) ===")
    try:
        cells = mbdev.read_registers(0x1200, 16)
        print(f"    Cell voltages (mV): {cells}")
        print(f"    Cell voltages (V):  {[c/1000 for c in cells]}")
    except Exception as e:
        print(f"    Cell read FAIL: {e}")

    try:
        # TempMos at 0x1245, read block of 28 regs to 0x1260
        st = mbdev.read_registers(0x1245, 28)
        temp_mos = to_signed16(st[0]) / 10
        bat_vol = u32(st, 3) / 1000
        bat_cur = s32(st, 7) / 1000
        temp1 = to_signed16(st[9]) / 10
        temp2 = to_signed16(st[10]) / 10
        alarms = u32(st, 11)
        balan_sta = (st[14] >> 8) & 0xFF
        soc = st[14] & 0xFF
        cap_remain = s32(st, 15) / 1000
        cycles = u32(st, 19)
        soh = (st[23] >> 8) & 0xFF
        charge_fet = (st[27] >> 8) & 0xFF
        discharge_fet = st[27] & 0xFF

        print(f"    TempMos:     {temp_mos}°C (raw={st[0]})")
        print(f"    BatVol:      {bat_vol}V")
        print(f"    BatCurrent:  {bat_cur}A")
        print(f"    TempBat1:    {temp1}°C (raw={st[9]})")
        print(f"    TempBat2:    {temp2}°C (raw={st[10]})")
        print(f"    Alarms:      0x{alarms:08X}")
        print(f"    BalanSta:    {balan_sta}  SOC: {soc}%")
        print(f"    CapRemain:   {cap_remain}Ah")
        print(f"    Cycles:      {cycles}")
        print(f"    SOH:         {soh}%")
        print(f"    ChargeFET:   {charge_fet}  DischargeFET: {discharge_fet}")
    except Exception as e:
        print(f"    Status block FAIL: {e}")

    # Temp sensor presence + heating
    try:
        ts = mbdev.read_registers(0x1268, 1)
        temp_sens = (ts[0] >> 8) & 0xFF
        heating = ts[0] & 0xFF
        print(f"    TempSensor:  0b{temp_sens:08b}  Heating: {heating}")
    except Exception as e:
        print(f"    TempSensor FAIL: {e}")

    # Heat current
    try:
        hc = mbdev.read_register(0x1273)
        print(f"    HeatCurrent: {hc}mA ({hc/1000}A)")
    except Exception as e:
        print(f"    HeatCurrent FAIL: {e}")

    # TempBat3/4
    try:
        tb = mbdev.read_registers(0x127C, 2)
        temp3 = to_signed16(tb[0]) / 10
        temp4 = to_signed16(tb[1]) / 10
        print(f"    TempBat3:    {temp3}°C (raw={tb[0]})")
        print(f"    TempBat4:    {temp4}°C (raw={tb[1]})")
    except Exception as e:
        print(f"    TempBat3/4 FAIL: {e}")

    # --- 4. Read settings (0x1000) — key fields ---
    print("\n  === Settings data (0x1000) ===")
    try:
        s1 = mbdev.read_registers(0x1000, 40)
        cell_ov = u32(s1, 6) / 1000
        cell_uv = u32(s1, 2) / 1000
        charge_cur = u32(s1, 22) / 1000
        discharge_cur = u32(s1, 28) / 1000
        print(f"    CellOV:      {cell_ov}V")
        print(f"    CellUV:      {cell_uv}V")
        print(f"    ChargeCur:   {charge_cur}A")
        print(f"    DischargeCur:{discharge_cur}A")
    except Exception as e:
        print(f"    Settings block 1 FAIL: {e}")

    try:
        s2 = mbdev.read_registers(0x1036, 6)
        cell_count = u32(s2, 0)
        capacity = u32(s2, 4) / 1000
        print(f"    CellCount:   {cell_count}")
        print(f"    Capacity:    {capacity}Ah")
    except Exception as e:
        print(f"    Settings block 2 FAIL: {e}")

    # --- 5. Address filtering ---
    print("\n  === Address filtering (FC03 to all 4 addrs) ===")
    for addr in [0x01, 0x02, 0x03, 0x04]:
        try:
            mb = minimalmodbus.Instrument(PORT, slaveaddress=addr, mode="rtu", close_port_after_each_call=True)
            mb.serial.baudrate = BAUD
            mb.serial.parity = "N"
            mb.serial.stopbits = 1
            mb.serial.timeout = 0.5
            mb.handle_local_echo = False
            cells = mb.read_registers(0x1200, 4)
            print(f"    addr=0x{addr:02X}: cells(mV)={cells}")
        except Exception as e:
            print(f"    addr=0x{addr:02X}: {type(e).__name__}: {e}")

    print(f"\n  Done.")


# ============================================================
# TEST B: Passive listen (no TX at all)
# ============================================================
if TEST in ("all", "passive"):
    print(f"\n{'='*60}")
    print(f"TEST-B: PASSIVE LISTEN — no commands sent")
    print(f"{'='*60}")

    passive_results = []

    with serial.Serial(PORT, baudrate=BAUD, timeout=0.1) as ser:
        time.sleep(0.5)
        drain(ser, 100)

        for rnd in range(ROUNDS):
            print(f"\n--- Passive round {rnd+1}/{ROUNDS}: listen {LISTEN}s ---")

            time.sleep(0.5)
            ser.reset_input_buffer()

            events = read_timed(ser, LISTEN)
            if not events:
                print("  (silence — no data received)")
                passive_results.append(set())
            else:
                addrs_seen = report_events(events)
                passive_results.append(addrs_seen)
                print(f"\n  Addresses seen: {sorted('0x%02X' % a for a in addrs_seen)}")

    print(f"\n{'='*60}")
    print(f"TEST-B SUMMARY — {ROUNDS} rounds, {LISTEN}s passive listen each")
    print(f"{'='*60}")
    all_passive = set()
    any_data = False
    for i, addrs in enumerate(passive_results):
        all_passive |= addrs
        has_data = len(addrs) > 0
        any_data = any_data or has_data
        print(f"  Round {i+1}: {sorted('0x%02X' % a for a in addrs) if addrs else '(silence)'}")

    if any_data:
        print(f"\n  BMS units transmit UNSOLICITED data without any host command!")
        print(f"  Addresses seen: {sorted('0x%02X' % a for a in all_passive)}")
    else:
        print(f"\n  Bus is SILENT when no commands are sent.")
        print(f"  Cross-talk is triggered by host TX activity, not spontaneous.")
