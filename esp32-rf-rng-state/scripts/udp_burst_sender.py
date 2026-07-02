#!/usr/bin/env python3
"""Send deterministic UDP bursts to the ESP32 RF-state benchmark."""

import argparse
import socket
import time


def parse_byte(value):
    parsed = int(value, 0)
    if parsed < 0 or parsed > 255:
        raise argparse.ArgumentTypeError("byte value must be in [0, 255]")
    return parsed


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-ip", required=True, help="ESP32 IPv4 address")
    parser.add_argument("--port", type=int, default=9999, help="UDP port (default: 9999)")
    parser.add_argument(
        "--payload-bytes", type=int, default=64, help="Payload size (default: 64)"
    )
    parser.add_argument(
        "--byte", type=parse_byte, default="0x42", help="Repeated payload byte (default: 0x42)"
    )
    parser.add_argument(
        "--interval-us", type=int, default=1000, help="Packet interval (default: 1000 us)"
    )
    parser.add_argument(
        "--duration-s", type=float, default=30.0, help="Send duration (default: 30 s)"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.port <= 0 or args.port > 65535:
        raise SystemExit(f"invalid port: {args.port}")
    if args.payload_bytes <= 0 or args.payload_bytes > 1400:
        raise SystemExit(f"invalid payload size: {args.payload_bytes}")
    if args.interval_us <= 0:
        raise SystemExit(f"invalid interval: {args.interval_us}")

    payload = bytes([args.byte]) * args.payload_bytes
    interval_s = args.interval_us / 1_000_000.0
    deadline = time.perf_counter() + args.duration_s
    next_send = time.perf_counter()
    packets = 0
    total_bytes = 0

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        print(
            f"Sending {args.payload_bytes} B UDP bursts to {args.target_ip}:{args.port} "
            f"every {args.interval_us} us for {args.duration_s:.1f} s",
            flush=True,
        )
        while time.perf_counter() < deadline:
            total_bytes += sock.sendto(payload, (args.target_ip, args.port))
            packets += 1
            next_send += interval_s
            sleep_for = next_send - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_send = time.perf_counter()
    finally:
        sock.close()

    print(f"Sent {packets:,} packets / {total_bytes:,} bytes")


if __name__ == "__main__":
    main()
