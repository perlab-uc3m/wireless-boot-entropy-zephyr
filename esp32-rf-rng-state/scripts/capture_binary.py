#!/usr/bin/env python3
# esp32-rf-rng-state/scripts/capture_binary.py
#
# Copyright (C) 2026 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
#
# Captures raw binary TRNG output from the ESP32 UART console running at high speed,
# saving it directly to a file for offline statistical analysis.
#
# Usage:
#   python3 scripts/capture_binary.py --port /dev/ttyUSB0 --baud 921600 --bytes 268435456 --output scenario_idle.bin
#

import argparse
import sys
import time
import os
import re
import socket
import threading

DEFAULT_BYTES = 268435456

try:
    import serial
except ImportError:
    print("Error: 'pyserial' package is required.")
    print("Please install it using: pip install pyserial")
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Capture high-speed raw binary TRNG data from ESP32."
    )
    parser.add_argument(
        "-p",
        "--port",
        default="/dev/ttyUSB0",
        help="Serial port device (default: /dev/ttyUSB0)",
    )
    parser.add_argument(
        "-b", "--baud", type=int, default=921600, help="Baud rate (default: 921600)"
    )
    parser.add_argument(
        "-n",
        "--bytes",
        type=int,
        default=None,
        help="Total bytes to collect. Defaults to firmware raw_dump_bytes metadata, or 268435456 (256 MiB) if metadata is absent.",
    )
    parser.add_argument(
        "-o", "--output", default="raw_rng_data.bin", help="Output binary file path"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset the ESP32 after opening the serial port, then wait for the marker",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=90.0,
        help="Seconds to wait for the raw-start marker (default: 90)",
    )
    parser.add_argument(
        "--drain-seconds",
        type=float,
        default=1.0,
        help="Seconds to drain stale serial input before marker sync (default: 1)",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=30.0,
        help="Seconds between progress lines during binary capture (default: 30)",
    )
    parser.add_argument(
        "--udp-burst",
        action="store_true",
        help="Send deterministic UDP packets to the ESP32 while capturing raw bytes",
    )
    parser.add_argument(
        "--udp-target-ip",
        default=None,
        help="ESP32 IPv4 address for UDP bursts. If omitted, parse '[RF_WIFI] IPv4 ...' from serial output.",
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=9999,
        help="ESP32 UDP listen port for deterministic bursts (default: 9999)",
    )
    parser.add_argument(
        "--udp-payload-bytes",
        type=int,
        default=64,
        help="UDP payload size for deterministic bursts (default: 64)",
    )
    parser.add_argument(
        "--udp-byte",
        default="0x42",
        help="Repeated payload byte, decimal or hex (default: 0x42)",
    )
    parser.add_argument(
        "--udp-interval-us",
        type=int,
        default=1000,
        help="Interval between UDP burst packets (default: 1000 us)",
    )
    parser.add_argument(
        "--udp-start-delay",
        type=float,
        default=0.25,
        help="Seconds to send UDP bursts before triggering raw capture (default: 0.25)",
    )
    parser.add_argument(
        "--post-capture-drain-seconds",
        type=float,
        default=5.0,
        help="Seconds to read text logs after raw byte capture (default: 5)",
    )
    return parser.parse_args()


def parse_udp_byte(value):
    try:
        parsed = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid UDP byte value: {value}") from exc
    if parsed < 0 or parsed > 255:
        raise argparse.ArgumentTypeError("--udp-byte must be in [0, 255]")
    return parsed


class UdpBurstSender:
    def __init__(self, target_ip, port, payload_bytes, payload_byte, interval_us):
        self.target_ip = target_ip
        self.port = port
        self.payload = bytes([payload_byte]) * payload_bytes
        self.interval_s = interval_us / 1_000_000.0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="udp_burst_sender", daemon=True)
        self.sent_packets = 0
        self.sent_bytes = 0
        self.errors = 0

    def start(self):
        print(
            f"Starting UDP burst sender to {self.target_ip}:{self.port} "
            f"({len(self.payload)} B every {self.interval_s * 1_000_000:.0f} us)."
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2.0)

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        next_send = time.perf_counter()
        try:
            while not self.stop_event.is_set():
                try:
                    sent = sock.sendto(self.payload, (self.target_ip, self.port))
                    self.sent_packets += 1
                    self.sent_bytes += sent
                except OSError:
                    self.errors += 1

                next_send += self.interval_s
                sleep_for = next_send - time.perf_counter()
                if sleep_for > 0:
                    self.stop_event.wait(sleep_for)
                else:
                    next_send = time.perf_counter()
        finally:
            sock.close()


IPV4_RE = re.compile(rb"((?:\d{1,3}\.){3}\d{1,3})")


def extract_ipv4_from_line(line):
    if b"IPv4" not in line:
        return None
    match = IPV4_RE.search(line)
    if not match:
        return None
    return match.group(1).decode("ascii", errors="replace")


def reset_esp32(ser):
    # Keep GPIO0 high and pulse EN low through the common ESP32 USB-UART wiring.
    ser.dtr = False
    ser.rts = False
    time.sleep(0.05)
    ser.rts = True
    time.sleep(0.1)
    ser.rts = False
    time.sleep(0.5)
    ser.reset_input_buffer()


def drain_input(ser, seconds):
    deadline = time.time() + seconds
    previous_timeout = ser.timeout
    ser.timeout = 0
    try:
        while time.time() < deadline:
            waiting = ser.in_waiting
            if waiting:
                ser.read(waiting)
            else:
                time.sleep(0.01)
    finally:
        ser.timeout = previous_timeout
        ser.reset_input_buffer()


def drain_post_capture_logs(ser, seconds):
    if seconds <= 0:
        return

    print(f"Reading post-capture firmware logs for {seconds:.1f} s...")
    deadline = time.time() + seconds
    previous_timeout = ser.timeout
    ser.timeout = 0.1
    line_buf = bytearray()

    try:
        while time.time() < deadline:
            data = ser.read(256)
            if not data:
                continue
            line_buf.extend(data)
            while b"\n" in line_buf:
                raw_line, _, remainder = line_buf.partition(b"\n")
                line = bytes(raw_line).strip()
                if line.startswith(b"["):
                    decoded_line = line.decode("utf-8", errors="replace")
                    print(f"[ESP32] {decoded_line}")
                line_buf = bytearray(remainder)
    finally:
        ser.timeout = previous_timeout


def main():
    args = parse_args()
    try:
        udp_payload_byte = parse_udp_byte(args.udp_byte)
    except argparse.ArgumentTypeError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if args.udp_port <= 0 or args.udp_port > 65535:
        print(f"Error: invalid --udp-port value: {args.udp_port}")
        sys.exit(1)
    if args.udp_payload_bytes <= 0 or args.udp_payload_bytes > 1400:
        print(f"Error: invalid --udp-payload-bytes value: {args.udp_payload_bytes}")
        sys.exit(1)
    if args.udp_interval_us <= 0:
        print(f"Error: invalid --udp-interval-us value: {args.udp_interval_us}")
        sys.exit(1)

    print("=============================================")
    print("RF-TRNG Binary Stream Capture")
    print(f"  Serial Port: {args.port}")
    print(f"  Baud Rate:   {args.baud}")
    print(f"  Output File: {args.output}")
    if args.bytes is not None:
        print(
            f"  Target Size: {args.bytes / (1024 * 1024 * 1024):.3f} GB ({args.bytes:,} bytes)"
        )
    else:
        print("  Target Size: firmware metadata, fallback 256 MiB")
    if args.udp_burst:
        print(
            f"  UDP Burst:   {args.udp_payload_bytes} B, byte=0x{udp_payload_byte:02x}, "
            f"interval={args.udp_interval_us} us"
        )
    print("=============================================")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=5)
        ser.dtr = False
        ser.rts = False
        time.sleep(0.05)
        ser.reset_input_buffer()
        if args.drain_seconds > 0:
            drain_input(ser, args.drain_seconds)
        if args.reset:
            print("Resetting ESP32...")
            reset_esp32(ser)
    except Exception as e:
        print(f"Error opening serial port {args.port}: {e}")
        print(
            "Please verify the device path, permissions, and make sure it is not open in another monitor."
        )
        sys.exit(1)

    print("Waiting for boot and '[BENCH_RAW_START]' marker...")

    # Wait for the start marker. Scan bytes rather than lines so accidental
    # newlines in stale/binary data cannot hide the marker.
    marker = b"[BENCH_RAW_START]"
    armed_marker = b"[BENCH_RAW_ARMED]"
    meta_prefix = b"[BENCH_META] raw_dump_bytes,"
    start_time = time.time()
    firmware_bytes = None
    last_trigger_time = 0.0
    firmware_ipv4 = args.udp_target_ip
    raw_armed = False
    trigger_sent = False
    udp_wait_reported = False
    udp_sender = None

    scan_buf = bytearray()
    initial_data = b""

    while True:
        data = ser.read(256)
        if not data:
            if time.time() - start_time > args.startup_timeout:
                print(
                    "Error: Timeout waiting for ESP32 output. Reset the board or check connection."
                )
                ser.close()
                sys.exit(1)
            continue

        scan_buf.extend(data)

        marker_index = scan_buf.find(marker)
        if marker_index >= 0:
            after_marker_index = marker_index + len(marker)
            while (
                after_marker_index < len(scan_buf)
                and scan_buf[after_marker_index] in b"\r\n"
            ):
                after_marker_index += 1
            initial_data = bytes(scan_buf[after_marker_index:])
            break

        if armed_marker in scan_buf:
            raw_armed = True

        while b"\n" in scan_buf:
            raw_line, _, remainder = scan_buf.partition(b"\n")
            line = bytes(raw_line).strip()
            if line.startswith(b"["):
                decoded_line = line.decode("utf-8", errors="replace")
                print(f"[ESP32] {decoded_line}")
            parsed_ipv4 = extract_ipv4_from_line(line)
            if parsed_ipv4 is not None:
                firmware_ipv4 = firmware_ipv4 or parsed_ipv4
            if line.startswith(meta_prefix):
                try:
                    firmware_bytes = int(line.rsplit(b",", 1)[1])
                except ValueError:
                    pass
            if line.startswith(armed_marker):
                raw_armed = True
            scan_buf = bytearray(remainder)

        if raw_armed and not trigger_sent and time.time() - last_trigger_time > 0.25:
            if args.udp_burst:
                target_ip = args.udp_target_ip or firmware_ipv4
                if not target_ip:
                    if not udp_wait_reported:
                        print("UDP burst mode armed; waiting for ESP32 IPv4 before raw trigger...")
                        udp_wait_reported = True
                    last_trigger_time = time.time()
                    continue
                if udp_sender is None:
                    udp_sender = UdpBurstSender(
                        target_ip,
                        args.udp_port,
                        args.udp_payload_bytes,
                        udp_payload_byte,
                        args.udp_interval_us,
                    )
                    udp_sender.start()
                    if args.udp_start_delay > 0:
                        time.sleep(args.udp_start_delay)

            ser.write(b"G")
            trigger_sent = True
            last_trigger_time = time.time()

        if len(scan_buf) > 4096:
            del scan_buf[: -len(marker)]

    print("\nStart marker detected! Initiating binary collection...")
    target_bytes = (
        args.bytes if args.bytes is not None else (firmware_bytes or DEFAULT_BYTES)
    )
    if firmware_bytes is not None and target_bytes != firmware_bytes:
        print(
            f"Warning: host target ({target_bytes:,}) differs from firmware raw_dump_bytes ({firmware_bytes:,})."
        )
    print(
        f"Collecting {target_bytes / (1024 * 1024 * 1024):.3f} GB ({target_bytes:,} bytes)."
    )

    # Create directory if it doesn't exist
    out_dir = os.path.dirname(args.output)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    collected = 0
    chunk_size = 65536
    transfer_start_time = time.time()
    last_ui_update = 0

    try:
        with open(args.output, "wb") as f:
            if initial_data:
                initial_chunk = initial_data[:target_bytes]
                f.write(initial_chunk)
                collected += len(initial_chunk)

            consecutive_timeouts = 0
            while collected < target_bytes:
                to_read = min(chunk_size, target_bytes - collected)
                data = ser.read(to_read)

                if not data:
                    print("\nWarning: Read timeout. Retrying...")
                    consecutive_timeouts += 1
                    if consecutive_timeouts >= 3:
                        raise TimeoutError(
                            "Serial stream stopped before the requested byte count was captured"
                        )
                    continue
                consecutive_timeouts = 0

                f.write(data)
                collected += len(data)

                # Keep long captures friendly to terminal scrollback and VS Code memory.
                current_time = time.time()
                if (
                    current_time - last_ui_update >= args.progress_interval
                    or collected == target_bytes
                ):
                    elapsed = current_time - transfer_start_time
                    rate = (collected / 1024) / elapsed if elapsed > 0 else 0  # KB/s
                    pct = (collected / target_bytes) * 100

                    # Estimate remaining time
                    remaining_bytes = target_bytes - collected
                    eta_sec = (remaining_bytes / 1024) / rate if rate > 0 else 0

                    # Construct time strings
                    elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))
                    eta_str = time.strftime("%H:%M:%S", time.gmtime(eta_sec))

                    print(
                        f"Collected: {collected / (1024 * 1024):.2f} MB / {target_bytes / (1024 * 1024):.2f} MB ({pct:.2f}%) | "
                        f"Speed: {rate:.2f} KB/s | Time: {elapsed_str} | ETA: {eta_str}",
                        flush=True,
                    )
                    last_ui_update = current_time

        print("\n\nSuccess: Binary collection complete!")
        print(f"Total Bytes Saved: {collected:,} bytes")
        print(
            f"Total Elapsed Time: {time.strftime('%H:%M:%S', time.gmtime(time.time() - transfer_start_time))}"
        )
        drain_post_capture_logs(ser, args.post_capture_drain_seconds)

    except KeyboardInterrupt:
        print("\n\nCollection interrupted by user (Ctrl+C).")
        print(f"Partially captured data saved to: {args.output} ({collected:,} bytes)")
    except Exception as e:
        print(f"\n\nError during collection: {e}")
    finally:
        if udp_sender is not None:
            udp_sender.stop()
            print(
                f"UDP burst sender stopped: {udp_sender.sent_packets:,} packets, "
                f"{udp_sender.sent_bytes:,} bytes, errors={udp_sender.errors}"
            )
        ser.close()


if __name__ == "__main__":
    main()
