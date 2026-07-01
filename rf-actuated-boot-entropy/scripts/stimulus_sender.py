#!/usr/bin/env python3
"""Send public RF-actuation stimuli to the ESP32 over UDP."""

from __future__ import annotations

import argparse
import csv
import os
import socket
import struct
import time
from pathlib import Path


MAGIC = 0x31424541  # "AEB1" little endian
VERSION = 1
TYPE_START = 1
TYPE_BURST = 2
HEADER = struct.Struct("<IHHIIII16s")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--target-ip", required=True, help="ESP32 IPv4 address")
    p.add_argument("--port", type=int, default=7777, help="ESP32 UDP port")
    p.add_argument("--trials", type=int, default=100)
    p.add_argument(
        "--bursts",
        type=int,
        default=64,
        help="Burst packets per trial; 0 gives idle control",
    )
    p.add_argument(
        "--interval-us",
        type=int,
        default=1000,
        help="Nominal spacing between burst packets",
    )
    p.add_argument(
        "--payload-bytes",
        type=int,
        default=64,
        help="Total UDP payload bytes per packet",
    )
    p.add_argument(
        "--sample-bytes",
        type=int,
        default=4096,
        help="Device-local response bytes requested per trial",
    )
    p.add_argument(
        "--fixed-nonce",
        action="store_true",
        help="Reuse the same public nonce in every trial",
    )
    p.add_argument("--nonce-hex", help="Explicit 16-byte nonce as 32 hex chars")
    p.add_argument(
        "--start-gap-ms",
        type=int,
        default=20,
        help="Delay after START before BURST packets",
    )
    p.add_argument("--trial-gap-ms", type=int, default=250, help="Delay between trials")
    p.add_argument("--out", default="results/stimulus_log.csv")
    return p.parse_args()


def nonce_from_args(args: argparse.Namespace) -> bytes:
    if args.nonce_hex:
        nonce = bytes.fromhex(args.nonce_hex)
        if len(nonce) != 16:
            raise SystemExit("--nonce-hex must encode exactly 16 bytes")
        return nonce
    return b"\xa3" * 16 if args.fixed_nonce else os.urandom(16)


def make_msg(
    msg_type: int,
    trial: int,
    seq_or_count: int,
    interval_us: int,
    sample_bytes: int,
    nonce: bytes,
    payload_bytes: int,
) -> bytes:
    header = HEADER.pack(
        MAGIC, VERSION, msg_type, trial, seq_or_count, interval_us, sample_bytes, nonce
    )
    if payload_bytes <= len(header):
        return header
    pad = bytes((seq_or_count + i) & 0xFF for i in range(payload_bytes - len(header)))
    return header + pad


def sleep_until(target: float) -> None:
    while True:
        now = time.perf_counter()
        remaining = target - now
        if remaining <= 0:
            return
        if remaining > 0.001:
            time.sleep(remaining / 2)


def main() -> None:
    args = parse_args()
    if args.trials <= 0:
        raise SystemExit("--trials must be positive")
    if args.bursts < 0:
        raise SystemExit("--bursts cannot be negative")
    if args.sample_bytes <= 0:
        raise SystemExit("--sample-bytes must be positive")
    if args.payload_bytes < HEADER.size:
        raise SystemExit(f"--payload-bytes must be at least {HEADER.size}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dst = (args.target_ip, args.port)
    fixed_nonce = nonce_from_args(args) if args.fixed_nonce or args.nonce_hex else None

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "trial",
                "condition",
                "nonce",
                "bursts",
                "interval_us",
                "payload_bytes",
                "sample_bytes",
                "start_time_ns",
                "end_time_ns",
            ],
        )
        writer.writeheader()

        for trial in range(args.trials):
            nonce = fixed_nonce if fixed_nonce is not None else os.urandom(16)
            condition = "idle" if args.bursts == 0 else "burst"
            start_time = time.time_ns()
            start = make_msg(
                TYPE_START,
                trial,
                args.bursts,
                args.interval_us,
                args.sample_bytes,
                nonce,
                args.payload_bytes,
            )
            sock.sendto(start, dst)

            time.sleep(args.start_gap_ms / 1000.0)

            next_send = time.perf_counter()
            for seq in range(args.bursts):
                burst = make_msg(
                    TYPE_BURST,
                    trial,
                    seq,
                    args.interval_us,
                    args.sample_bytes,
                    nonce,
                    args.payload_bytes,
                )
                sock.sendto(burst, dst)
                next_send += args.interval_us / 1_000_000.0
                sleep_until(next_send)

            end_time = time.time_ns()
            writer.writerow(
                {
                    "trial": trial,
                    "condition": condition,
                    "nonce": nonce.hex(),
                    "bursts": args.bursts,
                    "interval_us": args.interval_us,
                    "payload_bytes": args.payload_bytes,
                    "sample_bytes": args.sample_bytes,
                    "start_time_ns": start_time,
                    "end_time_ns": end_time,
                }
            )
            f.flush()
            print(f"[stimulus] trial={trial} condition={condition} nonce={nonce.hex()}")
            time.sleep(args.trial_gap_ms / 1000.0)


if __name__ == "__main__":
    main()
