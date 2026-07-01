#!/usr/bin/env python3
"""Collect TEB boot-benchmark serial logs with a local capsule server."""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

import serial


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ED25519_SERVER = PROJECT_ROOT / "server" / "teb_beacon_server.py"
PQ_SERVER_SOURCE = PROJECT_ROOT / "server" / "teb_pq_beacon_server.c"
PQ_SERVER_BINARY = PROJECT_ROOT / "server" / "teb_pq_beacon_server"
PQ_SERVER_KEYS = PROJECT_ROOT / "server" / "teb_pq_server_keys.h"


def build_pq_server() -> Path:
    if not PQ_SERVER_KEYS.is_file():
        raise RuntimeError(
            f"missing {PQ_SERVER_KEYS}; run scripts/generate_pq_keys.py first"
        )

    deps = [PQ_SERVER_SOURCE, PQ_SERVER_KEYS]
    if PQ_SERVER_BINARY.is_file():
        binary_mtime = PQ_SERVER_BINARY.stat().st_mtime
        if all(dep.stat().st_mtime <= binary_mtime for dep in deps):
            return PQ_SERVER_BINARY

    liboqs = subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "liboqs"],
        text=True,
    ).split()
    openssl = subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "openssl"],
        text=True,
    ).split()
    cmd = [
        "gcc",
        "-O2",
        "-Wall",
        "-Wextra",
        "-std=c11",
        "-o",
        str(PQ_SERVER_BINARY),
        str(PQ_SERVER_SOURCE),
        *liboqs,
        *openssl,
    ]
    print("Building PQ capsule server")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)
    return PQ_SERVER_BINARY


def start_server(
    bind: str,
    port: int,
    log_path: Path,
    profile: str,
) -> subprocess.Popen[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("w", buffering=1)
    if profile == "ed25519":
        cmd = [sys.executable, str(ED25519_SERVER), "--bind", bind, "--port", str(port)]
    elif profile == "pq":
        server = build_pq_server()
        cmd = [str(server), "--bind", bind, "--port", str(port)]
    else:
        raise ValueError(f"unknown server profile: {profile}")

    print(f"Starting capsule server: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    ready = threading.Event()

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            log_fh.write(line)
            print(line, end="")
            if line.startswith("[TEB_SERVER] bind,"):
                ready.set()

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    if not ready.wait(timeout=5):
        proc.terminate()
        raise RuntimeError("capsule server did not start")
    print(f"Beacon server log: {log_path}")
    return proc


def reset_to_run_mode(ser: serial.Serial) -> None:
    ser.dtr = False
    ser.rts = True
    time.sleep(0.12)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.12)


def capture_run(
    ser: serial.Serial,
    log_fh,
    run: int,
    timeout_s: float,
    reset: str,
) -> bool:
    log_fh.write(f"[HOST_RUN] start,{run},{reset},{time.time():.3f}\n")
    if reset == "rts":
        reset_to_run_mode(ser)
    elif reset == "manual":
        print(f"Power-cycle the ESP32 now for run {run}; waiting for output.")
    elif reset != "none":
        raise ValueError(f"unknown reset mode: {reset}")

    deadline = time.monotonic() + timeout_s
    saw_result = False

    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode(errors="replace")
        log_fh.write(line)
        print(line, end="")
        if line.startswith("[TEB_RESULT]"):
            saw_result = True
            break
        if line.startswith("[TEB_ERR] request_capsule"):
            saw_result = True
            break

    log_fh.write(f"[HOST_RUN] end,{run},{int(saw_result)},{time.time():.3f}\n")
    log_fh.flush()
    return saw_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--reset", choices=["rts", "manual", "none"], default="rts")
    parser.add_argument("--server-bind", default="0.0.0.0")
    parser.add_argument("--server-port", type=int, default=6767)
    parser.add_argument("--server-profile", choices=["pq", "ed25519"], default="pq")
    parser.add_argument(
        "--log", type=Path, default=PROJECT_ROOT / "results" / "teb_boot.log"
    )
    parser.add_argument(
        "--server-log",
        type=Path,
        default=PROJECT_ROOT / "results" / "teb_server.log",
    )
    parser.add_argument("--no-server", action="store_true")
    args = parser.parse_args()

    server_proc: subprocess.Popen[str] | None = None
    if not args.no_server:
        server_proc = start_server(
            args.server_bind,
            args.server_port,
            args.server_log,
            args.server_profile,
        )

    args.log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
            ser.reset_input_buffer()
            with args.log.open("w", buffering=1) as log_fh:
                for run in range(1, args.runs + 1):
                    capture_run(ser, log_fh, run, args.timeout, args.reset)
                    time.sleep(1.0)
    finally:
        if server_proc is not None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_proc.kill()

    print(f"Serial log: {args.log}")
    print(f"Server log: {args.server_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
