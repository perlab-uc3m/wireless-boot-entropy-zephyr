#!/usr/bin/env python3
"""Collect Wi-Fi-idle ESP32 RNG slices across repeated resets.

The benchmark builds/flashes one wifi_idle firmware image configured for a
short raw dump, then repeatedly resets the board and captures one raw WDEV
slice per boot.  The per-boot files are kept separate and concatenated for
aggregate randlab analysis.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from glob import glob
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build.sh"
CAPTURE_SCRIPT = PROJECT_ROOT / "scripts" / "capture_binary.py"

DEFAULT_RUNS = 32
DEFAULT_BYTES_PER_RUN = 8 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyUSB0", help="Serial device or 'auto'.")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument(
        "--bytes-per-run",
        type=int,
        default=None,
        help="Bytes captured after each reset. Defaults to total-bytes/runs, or 8 MiB.",
    )
    parser.add_argument(
        "--total-bytes",
        type=int,
        default=None,
        help="Total aggregate bytes. If set with --runs, bytes-per-run is derived.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "results" / "wifi_idle_reboot",
        help="Dataset root containing raw/, logs/, and results/.",
    )
    parser.add_argument("--condition", default="wifi_idle", choices=["wifi_idle"])
    parser.add_argument("--wifi-ssid", default=None)
    parser.add_argument("--wifi-pass", default=None)
    parser.add_argument("--board", default=None)
    parser.add_argument("--build-dir", default=None)
    parser.add_argument("--raw-delay-ms", type=int, default=1000)
    parser.add_argument("--startup-timeout", type=float, default=90.0)
    parser.add_argument("--progress-interval", type=float, default=30.0)
    parser.add_argument("--drain-seconds", type=float, default=0.5)
    parser.add_argument("--post-capture-drain-seconds", type=float, default=1.0)
    parser.add_argument("--cooldown-seconds", type=float, default=1.0)
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Assume matching firmware is already flashed.",
    )
    parser.add_argument(
        "--no-flash",
        action="store_true",
        help="Build but do not flash before collecting.",
    )
    parser.add_argument(
        "--clean-build",
        action="store_true",
        help="Pass --clean to build.sh before the build.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Keep existing complete per-boot slices and continue missing runs.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def detect_serial_port() -> str:
    candidates: list[str] = []
    for pattern in ("/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*"):
        candidates.extend(glob(pattern))

    try:
        import serial.tools.list_ports

        for port in serial.tools.list_ports.comports():
            if port.device not in candidates:
                candidates.append(port.device)
    except Exception:
        pass

    candidates = sorted(dict.fromkeys(candidates))
    if not candidates:
        raise RuntimeError("no serial ports found")
    if len(candidates) == 1:
        return candidates[0]

    preferred = [
        path for path in candidates if "CP210" in path or "Silicon_Labs" in path
    ]
    if len(preferred) == 1:
        return preferred[0]

    raise RuntimeError("multiple serial ports found: " + ", ".join(candidates))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_command(cmd: list[str]) -> str:
    redacted = []
    hide_next = False
    for token in cmd:
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
            continue
        redacted.append(token)
        if token in {"--wifi-pass", "--password", "--pass"}:
            hide_next = True
    return " ".join(redacted)


def run_command(cmd: list[str], log_path: Path, env: dict[str, str] | None = None) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", buffering=1) as log_fh:
        log_fh.write(f"[HOST_CMD] {display_command(cmd)}\n")
        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log_fh.write(line)
            print(line, end="")
        ret = proc.wait()
    if ret != 0:
        raise subprocess.CalledProcessError(ret, cmd)


def derive_bytes_per_run(args: argparse.Namespace) -> int:
    if args.total_bytes is not None:
        if args.total_bytes <= 0:
            raise ValueError("--total-bytes must be positive")
        if args.bytes_per_run is not None:
            if args.bytes_per_run * args.runs != args.total_bytes:
                raise ValueError(
                    "--bytes-per-run * --runs must equal --total-bytes when both are set"
                )
            return args.bytes_per_run
        if args.total_bytes % args.runs != 0:
            raise ValueError("--total-bytes must divide evenly by --runs")
        return args.total_bytes // args.runs

    return args.bytes_per_run or DEFAULT_BYTES_PER_RUN


def build_firmware(args: argparse.Namespace, bytes_per_run: int, logs_dir: Path) -> None:
    if args.skip_build:
        return

    cmd = [
        str(BUILD_SCRIPT),
        "--condition",
        args.condition,
        "--raw-bytes",
        str(bytes_per_run),
        "--raw-delay-ms",
        str(args.raw_delay_ms),
        "--flash-port",
        args.port,
    ]
    if not args.no_flash:
        cmd.append("--flash")
    if args.clean_build:
        cmd.append("--clean")
    if args.board is not None:
        cmd.extend(["--board", args.board])
    if args.build_dir is not None:
        cmd.extend(["--build-dir", args.build_dir])

    env = os.environ.copy()
    if args.wifi_ssid is not None:
        env["WIFI_SSID"] = args.wifi_ssid
    if args.wifi_pass is not None:
        env["WIFI_PASS"] = args.wifi_pass
    run_command(cmd, logs_dir / "build_flash.log", env=env)


def capture_one(
    args: argparse.Namespace,
    output_path: Path,
    log_path: Path,
    bytes_per_run: int,
) -> float:
    cmd = [
        sys.executable,
        "-u",
        str(CAPTURE_SCRIPT),
        "--port",
        args.port,
        "--baud",
        str(args.baud),
        "--bytes",
        str(bytes_per_run),
        "--output",
        str(output_path),
        "--startup-timeout",
        str(args.startup_timeout),
        "--progress-interval",
        str(args.progress_interval),
        "--drain-seconds",
        str(args.drain_seconds),
        "--post-capture-drain-seconds",
        str(args.post_capture_drain_seconds),
        "--reset",
    ]

    start = time.monotonic()
    run_command(cmd, log_path)
    return time.monotonic() - start


def concatenate(files: list[Path], output_path: Path) -> str:
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp_path.open("wb") as out_fh:
        for path in files:
            with path.open("rb") as in_fh:
                shutil.copyfileobj(in_fh, out_fh, length=1024 * 1024)
    tmp_path.replace(output_path)
    return sha256_file(output_path)


def write_readme(
    root: Path,
    args: argparse.Namespace,
    bytes_per_run: int,
    concat_path: Path,
    concat_sha256: str,
    runs: list[dict[str, object]],
) -> None:
    seeded = sum(1 for run in runs if run.get("status") == "captured")
    total_bytes = bytes_per_run * len(runs)
    lines = [
        "# Wi-Fi Idle Reboot RNG Dataset",
        "",
        f"Collected: {utc_now()}",
        "",
        "## Parameters",
        "",
        f"- Condition: `{args.condition}`",
        f"- Runs requested: {args.runs}",
        f"- Runs captured: {seeded}",
        f"- Bytes per reboot: {bytes_per_run:,}",
        f"- Aggregate bytes: {total_bytes:,}",
        f"- Raw delay before trigger: {args.raw_delay_ms} ms",
        f"- Serial baud: {args.baud}",
        "",
        "## Files",
        "",
        "- `raw/wifi_idle_reboot_###_*.bin`: one reset-triggered Wi-Fi-idle slice per boot",
        f"- `raw/{concat_path.name}`: concatenation of captured slices in run order",
        "- `manifest.json`: machine-readable capture metadata",
        "- `logs/`: build, flash, and per-run capture logs",
        "",
        "## Aggregate",
        "",
        f"- File: `raw/{concat_path.name}`",
        f"- SHA-256: `{concat_sha256}`",
        "",
        "This benchmark tests post-association Wi-Fi-idle WDEV output after repeated",
        "ESP32 resets. It is not a first-instruction cold-boot entropy capture.",
    ]
    (root / "README.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    if args.runs <= 0:
        raise SystemExit("--runs must be positive")
    bytes_per_run = derive_bytes_per_run(args)
    if bytes_per_run <= 0:
        raise SystemExit("bytes per run must be positive")

    if args.port == "auto":
        try:
            args.port = detect_serial_port()
        except RuntimeError as exc:
            raise SystemExit(f"serial auto-detection failed: {exc}") from exc
        print(f"Auto-detected serial port: {args.port}")

    root = args.output_root.resolve()
    raw_dir = root / "raw"
    logs_dir = root / "logs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    started = utc_now()
    build_firmware(args, bytes_per_run, logs_dir)

    runs: list[dict[str, object]] = []
    captured_files: list[Path] = []
    width = max(3, len(str(args.runs)))

    for index in range(1, args.runs + 1):
        output = raw_dir / f"{args.condition}_reboot_{index:0{width}d}_{bytes_per_run}.bin"
        log_path = logs_dir / f"capture_{index:0{width}d}.log"

        if args.resume and output.exists() and output.stat().st_size == bytes_per_run:
            print(f"Run {index}/{args.runs}: reusing existing {output}")
            elapsed = None
        else:
            print(f"Run {index}/{args.runs}: reset and capture {bytes_per_run:,} bytes")
            elapsed = capture_one(args, output, log_path, bytes_per_run)

        size = output.stat().st_size if output.exists() else 0
        if size != bytes_per_run:
            raise RuntimeError(f"{output} has {size} bytes, expected {bytes_per_run}")

        digest = sha256_file(output)
        captured_files.append(output)
        runs.append(
            {
                "run": index,
                "status": "captured",
                "path": str(output.relative_to(root)),
                "bytes": size,
                "sha256": digest,
                "capture_seconds": elapsed,
            }
        )

        if args.cooldown_seconds > 0 and index != args.runs:
            time.sleep(args.cooldown_seconds)

    total_bytes = bytes_per_run * len(captured_files)
    concat_path = raw_dir / f"{args.condition}_reboot_concat_{total_bytes}.bin"
    concat_sha256 = concatenate(captured_files, concat_path)
    finished = utc_now()

    manifest = {
        "schema": "esp32-rf-rng-state-reboot-slices-v1",
        "started_utc": started,
        "finished_utc": finished,
        "condition": args.condition,
        "runs_requested": args.runs,
        "runs_captured": len(captured_files),
        "bytes_per_run": bytes_per_run,
        "aggregate_bytes": total_bytes,
        "serial_port": args.port,
        "baud": args.baud,
        "raw_delay_ms": args.raw_delay_ms,
        "startup_timeout_seconds": args.startup_timeout,
        "capture_script": str(CAPTURE_SCRIPT.relative_to(PROJECT_ROOT)),
        "aggregate": {
            "path": str(concat_path.relative_to(root)),
            "bytes": concat_path.stat().st_size,
            "sha256": concat_sha256,
        },
        "runs": runs,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    write_readme(root, args, bytes_per_run, concat_path, concat_sha256, runs)

    print("Wrote:")
    print(f"  {root / 'manifest.json'}")
    print(f"  {root / 'README.md'}")
    print(f"  {concat_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
