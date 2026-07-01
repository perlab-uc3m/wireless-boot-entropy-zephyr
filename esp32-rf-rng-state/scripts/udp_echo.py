#!/usr/bin/env python3
"""Small UDP echo server for the wifi_traffic benchmark."""

import argparse
import socket


def parse_args():
    parser = argparse.ArgumentParser(
        description="UDP echo server for ESP32 traffic benchmark"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=9999, help="UDP port (default: 9999)"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    print(f"udp_echo listening on {args.host}:{args.port}", flush=True)

    while True:
        data, addr = sock.recvfrom(2048)
        sock.sendto(data, addr)


if __name__ == "__main__":
    main()
