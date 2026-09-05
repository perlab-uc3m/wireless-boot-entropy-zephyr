#!/usr/bin/env python3
"""Collect one RF-actuated entropy trial after each ESP32 EN reset."""

from __future__ import annotations

import argparse
import csv
import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import serial


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(
        description="Run one fixed-burst capture after each ESP32 RTS/EN reset"
    )
    parser.add_argument("--serial", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--trials", type=int, default=128)
    parser.add_argument("--port", type=int, default=7778)
    parser.add_argument(
        "--out-dir", type=Path, default=Path("results") / f"aeb_restart_{stamp}"
    )
    parser.add_argument("--trial-timeout", type=float, default=60.0)
    parser.add_argument("--max-attempts", type=int, default=160)
    parser.add_argument("--reset-pulse-ms", type=float, default=100.0)
    parser.add_argument("--settle-ms", type=float, default=250.0)
    parser.add_argument("--sample-bytes", type=int, default=8192)
    parser.add_argument("--bursts", type=int, default=64)
    parser.add_argument("--interval-us", type=int, default=1000)
    parser.add_argument(
        "--nonce-hex", default="00000000000000000000000000000000"
    )
    return parser.parse_args()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def complete_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="") as handle:
            return [row for row in csv.DictReader(handle) if row.get("complete") == "True"]
    except (OSError, csv.Error):
        return []


def pulse_en(port: serial.Serial, pulse_seconds: float) -> None:
    # DTR remains inactive so GPIO0 stays high. RTS drives the auto-reset
    # circuit connected to EN on common ESP32 development boards.
    port.dtr = False
    port.rts = True
    time.sleep(pulse_seconds)
    port.rts = False


def write_serial_marker(handle, message: str) -> None:
    handle.write(f"\n[restart-runner {utc_now()}] {message}\n".encode())


def drain_serial(port: serial.Serial, handle) -> bool:
    try:
        waiting = port.in_waiting
        if waiting:
            handle.write(port.read(waiting))
        return True
    except (OSError, serial.SerialException):
        return False


def open_serial(path: str, baud: int, timeout: float = 30.0) -> serial.Serial:
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            port = serial.Serial()
            port.port = path
            port.baudrate = baud
            port.timeout = 0
            port.dtr = False
            port.rts = False
            port.open()
            port.dtr = False
            port.rts = False
            return port
        except (OSError, serial.SerialException) as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"could not open serial device {path}: {last_error}")


def wait_for_collector(log_path: Path, process: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"collector exited with status {process.returncode}")
        if log_path.exists() and "[collector] listening" in log_path.read_text(errors="replace"):
            return
        time.sleep(0.1)
    raise RuntimeError("collector did not become ready")


def main() -> int:
    args = parse_args()
    if args.trials <= 0 or args.max_attempts < args.trials:
        raise SystemExit("--trials must be positive and --max-attempts must cover it")
    if args.trial_timeout <= 0 or args.reset_pulse_ms <= 0 or args.settle_ms < 0:
        raise SystemExit("timeouts must be positive and --settle-ms must not be negative")
    if len(bytes.fromhex(args.nonce_hex)) != 16:
        raise SystemExit("--nonce-hex must contain exactly 16 bytes")

    project_root = Path(__file__).resolve().parent.parent
    collector_script = project_root / "scripts" / "aeb_collector.py"
    args.out_dir = args.out_dir.resolve()
    args.out_dir.mkdir(parents=True, exist_ok=False)

    status_path = args.out_dir / "status.json"
    collector_log_path = args.out_dir / "collector.log"
    trial_csv_path = args.out_dir / "aeb_trials.csv"
    restart_csv_path = args.out_dir / "restart_trials.csv"
    metadata_path = args.out_dir / "restart_experiment.json"
    serial_log_path = args.out_dir / "serial.log"

    status = {
        "phase": "starting",
        "target_complete_trials": args.trials,
        "complete_trials": 0,
        "reset_attempts": 0,
        "serial_device": args.serial,
        "updated_utc": utc_now(),
    }
    write_json(status_path, status)
    write_json(
        metadata_path,
        {
            "created_utc": utc_now(),
            "reset_method": "USB serial RTS pulse through the board auto-reset circuit",
            "reset_line": "EN",
            "power_removed": False,
            "cold_power_cycle": False,
            "one_firmware_trial_per_reset_required": True,
            "target_complete_trials": args.trials,
            "serial_device": args.serial,
            "serial_baud": args.baud,
            "reset_pulse_ms": args.reset_pulse_ms,
            "sample_bytes": args.sample_bytes,
            "bursts": args.bursts,
            "interval_us": args.interval_us,
            "fixed_nonce_hex": args.nonce_hex.lower(),
        },
    )

    collector_command = [
        sys.executable,
        "-u",
        str(collector_script),
        "--port",
        str(args.port),
        "--trials",
        str(args.trials),
        "--out-dir",
        str(args.out_dir),
        "--sample-bytes",
        str(args.sample_bytes),
        "--bursts",
        str(args.bursts),
        "--interval-us",
        str(args.interval_us),
        "--payload-bytes",
        "64",
        "--payload-mode",
        "constant",
        "--nonce-hex",
        args.nonce_hex,
        "--idle-timeout",
        str(max(300.0, args.trial_timeout * 2.0)),
    ]

    collector: Optional[subprocess.Popen] = None
    serial_port: Optional[serial.Serial] = None
    collector_log = None
    restart_handle = None
    serial_log = None

    def stop_collector() -> None:
        if collector is not None and collector.poll() is None:
            collector.send_signal(signal.SIGINT)
            try:
                collector.wait(timeout=5)
            except subprocess.TimeoutExpired:
                collector.terminate()

    try:
        serial_port = open_serial(args.serial, args.baud)
        serial_log = serial_log_path.open("ab", buffering=0)
        drain_serial(serial_port, serial_log)

        collector_log = collector_log_path.open("w", buffering=1)
        collector = subprocess.Popen(
            collector_command,
            cwd=project_root,
            stdout=collector_log,
            stderr=subprocess.STDOUT,
        )
        status["collector_pid"] = collector.pid
        status["phase"] = "waiting_for_collector"
        status["updated_utc"] = utc_now()
        write_json(status_path, status)
        wait_for_collector(collector_log_path, collector, timeout=10.0)

        restart_handle = restart_csv_path.open("w", newline="", buffering=1)
        restart_writer = csv.DictWriter(
            restart_handle,
            fieldnames=[
                "reset_attempt",
                "successful_restart_index",
                "capture_id",
                "reset_utc",
                "completed_utc",
                "elapsed_seconds",
                "outcome",
            ],
        )
        restart_writer.writeheader()

        completed = len(complete_rows(trial_csv_path))
        attempts = 0
        while completed < args.trials and attempts < args.max_attempts:
            if collector.poll() is not None:
                raise RuntimeError(f"collector exited with status {collector.returncode}")

            attempts += 1
            before = completed
            reset_utc = utc_now()
            started = time.monotonic()
            if not drain_serial(serial_port, serial_log):
                if serial_port.is_open:
                    serial_port.close()
                serial_port = open_serial(args.serial, args.baud)
                write_serial_marker(serial_log, "serial device reconnected")
            write_serial_marker(serial_log, f"reset attempt {attempts}")
            pulse_en(serial_port, args.reset_pulse_ms / 1000.0)

            status.update(
                {
                    "phase": "waiting_for_trial",
                    "complete_trials": completed,
                    "reset_attempts": attempts,
                    "last_reset_utc": reset_utc,
                    "updated_utc": utc_now(),
                }
            )
            write_json(status_path, status)

            deadline = started + args.trial_timeout
            rows = complete_rows(trial_csv_path)
            while len(rows) <= before and time.monotonic() < deadline:
                if collector.poll() is not None:
                    raise RuntimeError(
                        f"collector exited with status {collector.returncode}"
                    )
                if not drain_serial(serial_port, serial_log):
                    if serial_port.is_open:
                        serial_port.close()
                    serial_port = open_serial(args.serial, args.baud)
                    write_serial_marker(serial_log, "serial device reconnected")
                time.sleep(0.25)
                rows = complete_rows(trial_csv_path)

            if not drain_serial(serial_port, serial_log):
                if serial_port.is_open:
                    serial_port.close()
                serial_port = open_serial(args.serial, args.baud)
                write_serial_marker(serial_log, "serial device reconnected")

            elapsed = time.monotonic() - started
            if len(rows) > before:
                completed = len(rows)
                row = rows[-1]
                restart_writer.writerow(
                    {
                        "reset_attempt": attempts,
                        "successful_restart_index": completed - 1,
                        "capture_id": row.get("capture_id", ""),
                        "reset_utc": reset_utc,
                        "completed_utc": utc_now(),
                        "elapsed_seconds": f"{elapsed:.3f}",
                        "outcome": "complete",
                    }
                )
            else:
                restart_writer.writerow(
                    {
                        "reset_attempt": attempts,
                        "successful_restart_index": "",
                        "capture_id": "",
                        "reset_utc": reset_utc,
                        "completed_utc": utc_now(),
                        "elapsed_seconds": f"{elapsed:.3f}",
                        "outcome": "timeout",
                    }
                )

            status.update(
                {
                    "phase": "running" if completed < args.trials else "finishing",
                    "complete_trials": completed,
                    "reset_attempts": attempts,
                    "updated_utc": utc_now(),
                }
            )
            write_json(status_path, status)
            if completed < args.trials:
                time.sleep(args.settle_ms / 1000.0)

        if completed != args.trials:
            raise RuntimeError(
                f"stopped after {attempts} reset attempts with {completed}/{args.trials} complete"
            )

        collector_status = collector.wait(timeout=10)
        if collector_status != 0:
            raise RuntimeError(f"collector exited with status {collector_status}")

        status.update(
            {
                "phase": "complete",
                "complete_trials": completed,
                "reset_attempts": attempts,
                "finished_utc": utc_now(),
                "updated_utc": utc_now(),
            }
        )
        write_json(status_path, status)
        return 0
    except Exception as exc:
        status.update(
            {
                "phase": "failed",
                "error": str(exc),
                "updated_utc": utc_now(),
            }
        )
        write_json(status_path, status)
        print(f"restart experiment failed: {exc}", file=sys.stderr)
        return 1
    finally:
        stop_collector()
        if restart_handle is not None:
            restart_handle.close()
        if collector_log is not None:
            collector_log.close()
        if serial_log is not None:
            if serial_port is not None and serial_port.is_open:
                drain_serial(serial_port, serial_log)
            serial_log.close()
        if serial_port is not None and serial_port.is_open:
            serial_port.dtr = False
            serial_port.rts = False
            serial_port.close()


if __name__ == "__main__":
    raise SystemExit(main())
