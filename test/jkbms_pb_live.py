#!/usr/bin/env python3
"""Live JKBMS PB protocol analyser for LA1010 streams.

Reads raw samples from stdin (16-bit LE words, bit 0 = sample 0 for channel 0,
single-channel capture), decodes UART 115200 8N1, frames by inter-byte gap,
and classifies each frame per JKBMS-PB.md.  Reports live stats and anomalies.

Usage:
    ssh root@host 'python3 /opt/la1010/la1010_capture.py --raw \
        --channels 0 --sample-rate 500000 --duration 99999' \
    | python3 jkbms_pb_live.py
"""
import sys
import time
from collections import Counter

SAMPLE_RATE = 500_000
BAUD = 115200
SPB = SAMPLE_RATE / BAUD          # 4.3403 samples per bit
BYTE_SAMPLES = int(10 * SPB) + 2  # max samples to decode one UART byte

# JKBMS PB protocol constants
PAYLOAD_LEN = 300
HDR_55AA = b"\x55\xaa\xeb\x90"
FRAME_TYPES = {0x0001: "settings", 0x0002: "status", 0x0003: "about"}
TRIGGER_REGS = {0x1620: "status", 0x161E: "settings", 0x161C: "about"}
# Inter-byte gap defining a frame boundary.  11-byte requests take
# ~1 ms; 310-byte 0x55AA bursts take ~27 ms; bus is idle for >=120 ms
# between commands.  2 ms is comfortably above intra-frame jitter.
FRAME_GAP_US = 2_000


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def decode_uart(samples: bytearray, start: int, base: int):
    """Decode UART bytes from sample buffer.

    Returns (list of (byte_val, sample_pos, framing_ok), next_index).
    Stops when fewer than one byte-span of samples remain after `start`.
    """
    out = []
    i = start
    n = len(samples)
    while i + BYTE_SAMPLES < n:
        if samples[i] != 0:
            i += 1
            continue
        # Validate start bit at its centre
        c = i + SPB / 2
        if samples[int(c)] != 0:
            i += 1
            continue
        byte_val = 0
        for b in range(8):
            if samples[int(c + (1 + b) * SPB)]:
                byte_val |= 1 << b
        stop_ok = samples[int(c + 9 * SPB)] == 1
        out.append((byte_val, base + i, stop_ok))
        # Always advance past the byte, even on framing error
        i = int(i + 10 * SPB)
    return out, i


class Analyser:
    def __init__(self):
        self.start = time.monotonic()
        self.frame = bytearray()
        self.last_byte_us = -10**9

        self.total_bytes = 0
        self.framing_errors = 0
        self.total_frames = 0
        self.frame_fe = 0  # framing errors inside current frame

        self.fc16_req = Counter()   # (addr, reg_name) -> count
        self.fc16_ack = Counter()   # (addr, reg_name) -> count
        self.payloads = Counter()   # ftype_name -> count
        self.cksum_ok = 0
        self.cksum_bad = 0
        self.echo_offsets = Counter()  # bytes of TX echo seen before 0x55AA

        self.req_intervals_ms = []  # per (addr,reg) delta
        self.last_req_us = {}

        self.anomalies = Counter()
        self.examples = {}

    def feed_byte(self, byte_val: int, ts_us: int, framing_ok: bool):
        self.total_bytes += 1
        if not framing_ok:
            self.framing_errors += 1
        if self.frame and ts_us - self.last_byte_us > FRAME_GAP_US:
            self._classify(bytes(self.frame), self.frame_fe, self.last_byte_us)
            self.frame.clear()
            self.frame_fe = 0
        self.frame.append(byte_val)
        if not framing_ok:
            self.frame_fe += 1
        self.last_byte_us = ts_us

    def flush(self):
        if self.frame:
            self._classify(bytes(self.frame), self.frame_fe, self.last_byte_us)
            self.frame.clear()
            self.frame_fe = 0

    def _classify(self, f: bytes, fe: int, ts_us: int):
        self.total_frames += 1
        # Frames with >30% framing errors are likely RS485 line-turnaround
        # glitches or noise on an idle bus — classify separately.
        if fe * 10 >= len(f) * 3:
            self._anom(f"noise_fe{fe}/{len(f)}", f, kind="noise")
            return

        # 11-byte FC16 trigger request
        if len(f) == 11 and f[1] == 0x10:
            if modbus_crc16(f[:-2]) == (f[-2] | f[-1] << 8):
                reg = (f[2] << 8) | f[3]
                name = TRIGGER_REGS.get(reg, f"reg_0x{reg:04X}")
                self.fc16_req[(f[0], name)] += 1
                key = (f[0], reg)
                if key in self.last_req_us:
                    self.req_intervals_ms.append((ts_us - self.last_req_us[key]) / 1000)
                self.last_req_us[key] = ts_us
                if reg not in TRIGGER_REGS:
                    self._anom(f"req_unknown_reg_0x{reg:04X}", f)
            else:
                self._anom("req_bad_crc", f)
            return

        # 8-byte FC16 ACK
        if len(f) == 8 and f[1] == 0x10:
            if modbus_crc16(f[:-2]) == (f[-2] | f[-1] << 8):
                reg = (f[2] << 8) | f[3]
                name = TRIGGER_REGS.get(reg, f"reg_0x{reg:04X}")
                self.fc16_ack[(f[0], name)] += 1
            else:
                self._anom("ack_bad_crc", f)
            return

        # 0x55AA payload, optionally preceded by 0..11 bytes of TX echo.
        # Length is typically 300 bytes from HDR onwards; occasionally the
        # trailing 0x00 pad gets glued on (gap <2ms), making it 301.
        idx = f.find(HDR_55AA)
        if idx >= 0:
            self.echo_offsets[idx] += 1
            after = len(f) - idx
            if after < PAYLOAD_LEN:
                self._anom(f"55aa_truncated_after_hdr_len={after}", f)
                return
            payload = f[idx:idx + PAYLOAD_LEN]
            ftype = payload[4] | payload[5] << 8
            self.payloads[FRAME_TYPES.get(ftype, f"ftype_0x{ftype:04X}")] += 1
            if ftype not in FRAME_TYPES:
                self._anom(f"unknown_ftype_0x{ftype:04X}", payload)
            cksum = sum(payload[:299]) & 0xFF
            if cksum == payload[299]:
                self.cksum_ok += 1
            else:
                self.cksum_bad += 1
                # Probe: does the extra byte at offset 300 match sum[0:300]?
                if len(f) - idx >= 301:
                    alt = sum(f[idx:idx + 300]) & 0xFF
                    tag = "_alt301_OK" if alt == f[idx + 300] else "_alt301_NO"
                    self._anom(f"55aa_bad_checksum{tag}", f[idx:idx + 301], kind="noise")
                else:
                    self._anom("55aa_bad_checksum", payload, kind="noise")
            # Any extra bytes after the 300-byte payload
            extra = f[idx + PAYLOAD_LEN:]
            if len(extra) == 1 and extra[0] == 0x00:
                pass  # expected trailing pad byte glued onto payload frame
            elif len(extra) > 0:
                self._anom(f"55aa_extra_after_payload_len={len(extra)}", extra, kind="noise")
            return

        # Anything else
        if len(f) <= 12:
            self._anom(f"short_frame_len={len(f)}", f, kind="noise")
        else:
            self._anom(f"unknown_frame_len={len(f)}", f)

    def _anom(self, sig: str, ctx: bytes, kind: str = "wire"):
        """kind = 'wire' (real protocol) or 'noise' (sampling artifact)."""
        self.anomalies[(kind, sig)] += 1
        if (kind, sig) not in self.examples:
            self.examples[(kind, sig)] = ctx[:48].hex()

    def report(self):
        elapsed = time.monotonic() - self.start
        rate = self.total_bytes / elapsed if elapsed > 0 else 0
        print(f"\n=== [{elapsed/60:6.1f} min]  bytes={self.total_bytes} "
              f"frames={self.total_frames}  rate={rate:.0f} B/s  "
              f"framing_err={self.framing_errors} ===", flush=True)

        req_n = sum(self.fc16_req.values())
        ack_n = sum(self.fc16_ack.values())
        pay_n = sum(self.payloads.values())
        print(f"  FC16 req={req_n} ack={ack_n}  0x55AA={pay_n}  "
              f"cksum ok/bad={self.cksum_ok}/{self.cksum_bad}", flush=True)

        if self.fc16_req:
            per_addr = Counter()
            for (addr, _), c in self.fc16_req.items():
                per_addr[addr] += c
            print(f"  REQ per addr: {dict(sorted(per_addr.items()))}", flush=True)
            per_reg = Counter()
            for (_, name), c in self.fc16_req.items():
                per_reg[name] += c
            print(f"  REQ per reg: {dict(per_reg)}", flush=True)

        if self.fc16_ack:
            per_addr = Counter()
            for (addr, _), c in self.fc16_ack.items():
                per_addr[addr] += c
            print(f"  ACK per addr: {dict(sorted(per_addr.items()))}", flush=True)

        if self.payloads:
            print(f"  0x55AA types: {dict(self.payloads)}", flush=True)

        if self.echo_offsets:
            print(f"  TX-echo bytes before 0x55AA: {dict(sorted(self.echo_offsets.items()))}", flush=True)

        if self.req_intervals_ms:
            iv = self.req_intervals_ms
            print(f"  req intervals ms: avg={sum(iv)/len(iv):.0f} "
                  f"min={min(iv):.0f} max={max(iv):.0f} n={len(iv)}", flush=True)

        wire = Counter()
        noise = Counter()
        for (kind, sig), cnt in self.anomalies.items():
            (wire if kind == "wire" else noise)[sig] = cnt

        wire_total = sum(wire.values())
        noise_total = sum(noise.values())
        print(f"  WIRE anomalies (driver-visible): {wire_total} "
              f"({100*wire_total/max(1,self.total_frames):.3f}% of frames)", flush=True)
        for sig, cnt in wire.most_common(10):
            print(f"    {cnt:6d}x  {sig}  ex={self.examples[('wire', sig)][:60]}", flush=True)
        print(f"  SAMPLING noise (LA1010 only): {noise_total} "
              f"({100*noise_total/max(1,self.total_frames):.3f}% of frames)", flush=True)
        for sig, cnt in noise.most_common(10):
            print(f"    {cnt:6d}x  {sig}  ex={self.examples[('noise', sig)][:60]}", flush=True)


def main():
    a = Analyser()
    sample_buf = bytearray()
    base = 0
    last_report = time.monotonic()
    report_every_s = 30.0

    while True:
        chunk = sys.stdin.buffer.read(65536)
        if not chunk:
            break

        # Each 16-bit LE word encodes 16 consecutive samples (bit0 = first)
        n_words = len(chunk) // 2
        for wi in range(n_words):
            w = chunk[2*wi] | (chunk[2*wi+1] << 8)
            for b in range(16):
                sample_buf.append((w >> b) & 1)

        bytes_out, next_idx = decode_uart(sample_buf, 0, base)
        for bv, pos, ok in bytes_out:
            if not ok:
                a.framing_errors += 1
                continue  # drop UART glitches from the classified stream
            ts_us = pos * 1_000_000 // SAMPLE_RATE
            a.feed_byte(bv, ts_us, True)

        # Retain a safety tail so start-bit search doesn't miss boundary bytes
        if next_idx > BYTE_SAMPLES:
            keep = next_idx - BYTE_SAMPLES
            sample_buf = sample_buf[keep:]
            base += keep

        now = time.monotonic()
        if now - last_report >= report_every_s:
            # If bus idle for >5 ms, close any pending frame before reporting
            cur_us = (base + len(sample_buf)) * 1_000_000 // SAMPLE_RATE
            if a.frame and cur_us - a.last_byte_us > 5_000:
                a.flush()
            a.report()
            last_report = now

    a.flush()
    a.report()


if __name__ == "__main__":
    main()
