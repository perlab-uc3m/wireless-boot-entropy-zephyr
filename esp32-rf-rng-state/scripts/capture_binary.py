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
    return parser.parse_args()


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


def main():
    args = parse_args()

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

        if armed_marker in scan_buf and time.time() - last_trigger_time > 0.25:
            ser.write(b"G")
            last_trigger_time = time.time()

        while b"\n" in scan_buf:
            raw_line, _, remainder = scan_buf.partition(b"\n")
            line = bytes(raw_line).strip()
            if line.startswith(b"["):
                decoded_line = line.decode("utf-8", errors="replace")
                print(f"[ESP32] {decoded_line}")
            if line.startswith(meta_prefix):
                try:
                    firmware_bytes = int(line.rsplit(b",", 1)[1])
                except ValueError:
                    pass
            scan_buf = bytearray(remainder)

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

    except KeyboardInterrupt:
        print("\n\nCollection interrupted by user (Ctrl+C).")
        print(f"Partially captured data saved to: {args.output} ({collected:,} bytes)")
    except Exception as e:
        print(f"\n\nError during collection: {e}")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
