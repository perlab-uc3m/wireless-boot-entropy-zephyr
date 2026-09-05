#!/usr/bin/env python3
"""Build, flash, capture, and analyze the SRAM preceding pattern experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import serial


PATTERNS = (0x00, 0xFF, 0xAA, 0x55)
REGION_BYTES = 4096


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(
        description="Run the SRAM startup state preceding pattern experiment"
    )
    parser.add_argument(
        "--serial",
        default="/dev/ttyUSB0",
        help="Serial device. A /dev/serial/by-id path is recommended.",
    )
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--board", default="esp32s3_devkitc/esp32s3/procpu"
    )
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument(
        "--conditions",
        default="rts",
        help="Comma-separated list containing rts, power-1, power-10, power-30",
    )
    parser.add_argument(
        "--power-off-command",
        help="Command used to remove board power. Shell operators are not supported.",
    )
    parser.add_argument(
        "--power-on-command",
        help="Command used to restore board power. Shell operators are not supported.",
    )
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument(
        "--out-dir", type=Path, default=Path("results") / f"sram_state_{stamp}"
    )
    parser.add_argument("--boot-timeout", type=float, default=20.0)
    parser.add_argument("--device-timeout", type=float, default=60.0)
    parser.add_argument("--reset-pulse-ms", type=float, default=100.0)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-flash", action="store_true")
    parser.add_argument("--keep-build", action="store_true")
    return parser.parse_args()


def west_command(app_root: Path) -> list[str]:
    west = shutil.which("west")
    if west:
        return [west]

    candidates = [
        app_root.parent / "entropy-capsule-bootstrap" / ".venv" / "bin" / "python",
        app_root.parent / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate), "-m", "west"]
    raise RuntimeError("west was not found in PATH or a repository virtual environment")


def build_environment(app_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault("CCACHE_DISABLE", "1")
    if environment.get("ZEPHYR_SDK_INSTALL_DIR"):
        return environment

    candidates = [
        app_root.parent.parent / "code" / "zephyr-sdk-0.17.0",
        Path.home() / "zephyr-sdk-0.17.0",
        Path("/opt/zephyr-sdk-0.17.0"),
    ]
    for candidate in candidates:
        if (candidate / "sdk_version").exists():
            environment["ZEPHYR_SDK_INSTALL_DIR"] = str(candidate.resolve())
            return environment
    return environment


def run_checked(command: list[str], cwd: Path, environment: dict[str, str]) -> None:
    printable = " ".join(shlex.quote(item) for item in command)
    print(f"[host] {printable}", flush=True)
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def open_serial(path: str, baud: int) -> serial.Serial:
    port = serial.Serial()
    port.port = path
    port.baudrate = baud
    port.timeout = 0.25
    port.dtr = False
    port.rts = False
    port.open()
    port.dtr = False
    port.rts = False
    return port


def pulse_en(port: serial.Serial, pulse_ms: float) -> None:
    port.dtr = False
    port.rts = True
    time.sleep(pulse_ms / 1000.0)
    port.rts = False


def wait_for_device(path: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return
        time.sleep(0.1)
    raise RuntimeError(f"serial device did not reappear within {timeout:g} seconds")


def read_capture(
    port: serial.Serial, log_handle, timeout: float
) -> tuple[bytes, int]:
    deadline = time.monotonic() + timeout
    captured = bytearray(REGION_BYTES)
    received: set[int] = set()
    address: int | None = None
    began = False
    ended = False

    while time.monotonic() < deadline:
        raw = port.readline()
        if not raw:
            continue
        log_handle.write(raw)
        log_handle.flush()
        line = raw.decode("ascii", errors="replace").strip()

        if line.startswith("[SRAM_CAPTURE_BEGIN]"):
            fields = dict(
                token.split("=", 1) for token in line.split()[1:] if "=" in token
            )
            if int(fields.get("bytes", "0")) != REGION_BYTES:
                raise RuntimeError("firmware reported an unexpected SRAM region size")
            address = int(fields["address"], 16)
            received.clear()
            began = True
            ended = False
        elif began and line.startswith("[SRAM_DATA]"):
            fields = dict(
                token.split("=", 1) for token in line.split()[1:] if "=" in token
            )
            offset = int(fields["offset"])
            data = bytes.fromhex(fields["hex"])
            if offset < 0 or offset + len(data) > REGION_BYTES:
                raise RuntimeError("firmware reported an invalid SRAM data offset")
            captured[offset : offset + len(data)] = data
            received.update(range(offset, offset + len(data)))
        elif began and line == "[SRAM_CAPTURE_END]":
            ended = True
        elif line.startswith("[SRAM_READY]") and ended:
            if len(received) != REGION_BYTES or address is None:
                raise RuntimeError("SRAM capture was incomplete")
            return bytes(captured), address

    raise RuntimeError("timed out while waiting for a complete SRAM capture")


def arm_pattern(
    port: serial.Serial, log_handle, pattern: int, timeout: float
) -> None:
    port.write(f"FILL {pattern:02x}\n".encode("ascii"))
    port.flush()
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        raw = port.readline()
        if not raw:
            continue
        log_handle.write(raw)
        log_handle.flush()
        line = raw.decode("ascii", errors="replace").strip()
        if line.startswith("[SRAM_ARMED]"):
            fields = dict(
                token.split("=", 1) for token in line.split()[1:] if "=" in token
            )
            if fields.get("pattern") != f"{pattern:02x}" or fields.get("verify") != "ok":
                raise RuntimeError(f"firmware could not verify pattern {pattern:02x}")
            return
    raise RuntimeError(f"timed out while arming pattern {pattern:02x}")


def power_duration(condition: str) -> float | None:
    if condition == "rts":
        return None
    if condition.startswith("power-"):
        return float(condition.split("-", 1)[1])
    raise ValueError(f"unsupported condition {condition!r}")


def perform_transition(
    condition: str,
    port: serial.Serial,
    args: argparse.Namespace,
    app_root: Path,
    environment: dict[str, str],
) -> serial.Serial:
    duration = power_duration(condition)
    if duration is None:
        pulse_en(port, args.reset_pulse_ms)
        return port

    if not args.power_off_command or not args.power_on_command:
        raise RuntimeError(
            "power conditions require --power-off-command and --power-on-command"
        )

    port.close()
    run_checked(shlex.split(args.power_off_command), app_root, environment)
    try:
        time.sleep(duration)
    finally:
        run_checked(shlex.split(args.power_on_command), app_root, environment)
    wait_for_device(args.serial, args.device_timeout)
    return open_serial(args.serial, args.baud)


def bit_agreement(data: bytes, pattern: int) -> float:
    differing = sum((value ^ pattern).bit_count() for value in data)
    return 1.0 - differing / (8.0 * len(data))


def hamming_fraction(left: bytes, right: bytes) -> float:
    differing = sum((a ^ b).bit_count() for a, b in zip(left, right))
    return differing / (8.0 * len(left))


def stable_bit_fraction(captures: list[bytes]) -> float:
    if not captures:
        return 0.0
    stable = 0
    for byte_index in range(len(captures[0])):
        for bit in range(8):
            values = {(capture[byte_index] >> bit) & 1 for capture in captures}
            stable += len(values) == 1
    return stable / (8.0 * len(captures[0]))


def summarize(rows: list[dict], payloads: list[bytes]) -> dict:
    groups: dict[tuple[str, int], list[int]] = {}
    for index, row in enumerate(rows):
        groups.setdefault((row["condition"], row["pattern"]), []).append(index)

    group_summaries = []
    for (condition, pattern), indices in sorted(groups.items()):
        captures = [payloads[index] for index in indices]
        agreements = [rows[index]["bit_agreement"] for index in indices]
        byte_matches = [rows[index]["byte_match_fraction"] for index in indices]
        pairwise = [
            hamming_fraction(captures[i], captures[j])
            for i in range(len(captures))
            for j in range(i + 1, len(captures))
        ]
        group_summaries.append(
            {
                "condition": condition,
                "preceding_pattern": f"0x{pattern:02x}",
                "captures": len(indices),
                "mean_bit_agreement_with_pattern": statistics.fmean(agreements),
                "mean_byte_match_with_pattern": statistics.fmean(byte_matches),
                "within_group_stable_bit_fraction": stable_bit_fraction(captures),
                "within_group_pairwise_hamming_mean": (
                    statistics.fmean(pairwise) if pairwise else None
                ),
                "unique_capture_hashes": len({rows[index]["sha256"] for index in indices}),
            }
        )

    all_pairwise = [
        hamming_fraction(payloads[i], payloads[j])
        for i in range(len(payloads))
        for j in range(i + 1, len(payloads))
    ]
    all_agreements = [row["bit_agreement"] for row in rows]
    group_mean_agreements = [
        group["mean_bit_agreement_with_pattern"] for group in group_summaries
    ]

    return {
        "analysis_scope": "retention and repeatability diagnostics; no entropy estimate",
        "capture_count": len(rows),
        "region_bytes": REGION_BYTES,
        "overall": {
            "mean_pairwise_hamming": (
                statistics.fmean(all_pairwise) if all_pairwise else None
            ),
            "preceding_pattern_agreement_min": min(all_agreements),
            "preceding_pattern_agreement_max": max(all_agreements),
            "group_mean_agreement_min": min(group_mean_agreements),
            "group_mean_agreement_max": max(group_mean_agreements),
            "stable_bit_fraction": stable_bit_fraction(payloads),
            "unique_capture_hashes": len({row["sha256"] for row in rows}),
        },
        "groups": group_summaries,
    }


def linker_evidence(build_dir: Path) -> str:
    map_path = build_dir / "zephyr" / "zephyr.map"
    if not map_path.exists():
        return "linker map unavailable\n"
    lines = map_path.read_text(errors="replace").splitlines()
    selected: set[int] = set()
    for index, line in enumerate(lines):
        if "sram_startup_region" in line:
            selected.update(range(max(0, index - 2), min(len(lines), index + 3)))
    return "\n".join(lines[index] for index in sorted(selected)) + "\n"


def main() -> int:
    args = parse_args()
    if args.repetitions <= 0:
        raise SystemExit("--repetitions must be positive")

    conditions = [item.strip() for item in args.conditions.split(",") if item.strip()]
    if not conditions:
        raise SystemExit("at least one condition is required")
    for condition in conditions:
        power_duration(condition)

    app_root = Path(__file__).resolve().parent.parent
    workspace = app_root.parent
    build_dir = args.build_dir if args.build_dir.is_absolute() else app_root / args.build_dir
    out_dir = args.out_dir if args.out_dir.is_absolute() else app_root / args.out_dir
    if out_dir.exists():
        raise SystemExit(f"output directory already exists: {out_dir}")
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True)

    environment = build_environment(app_root)
    west = west_command(app_root)
    if not args.skip_build:
        pristine = "auto" if args.keep_build else "always"
        run_checked(
            west
            + [
                "build",
                "-p",
                pristine,
                "-d",
                str(build_dir),
                "-b",
                args.board,
                str(app_root),
            ],
            workspace,
            environment,
        )
    if not args.skip_flash:
        run_checked(
            west
            + [
                "flash",
                "-d",
                str(build_dir),
                "--esp-device",
                args.serial,
            ],
            workspace,
            environment,
        )

    manifest = {
        "created_utc": utc_now(),
        "board": args.board,
        "serial_device": args.serial,
        "region_bytes": REGION_BYTES,
        "patterns": [f"0x{value:02x}" for value in PATTERNS],
        "pattern_order": "interleaved 00, ff, aa, 55",
        "repetitions_per_pattern_per_condition": args.repetitions,
        "conditions": conditions,
        "capture_hook": "Zephyr SYS_INIT at EARLY priority 0",
        "entropy_credit_bits": 0,
        "power_controller_present": bool(
            args.power_off_command and args.power_on_command
        ),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    (out_dir / "linker_evidence.txt").write_text(linker_evidence(build_dir))

    rows: list[dict] = []
    payloads: list[bytes] = []
    port = open_serial(args.serial, args.baud)
    try:
        with (out_dir / "serial.log").open("ab", buffering=0) as serial_log:
            pulse_en(port, args.reset_pulse_ms)
            read_capture(port, serial_log, args.boot_timeout)

            capture_index = 0
            for condition in conditions:
                for repetition in range(args.repetitions):
                    for pattern in PATTERNS:
                        arm_pattern(port, serial_log, pattern, args.boot_timeout)
                        transitioned_utc = utc_now()
                        port = perform_transition(
                            condition, port, args, app_root, environment
                        )
                        data, address = read_capture(
                            port, serial_log, args.boot_timeout
                        )
                        sha256 = hashlib.sha256(data).hexdigest()
                        filename = (
                            f"capture_{capture_index:04d}_{condition}_"
                            f"rep{repetition:02d}_after_{pattern:02x}.bin"
                        )
                        (raw_dir / filename).write_bytes(data)
                        row = {
                            "capture_index": capture_index,
                            "condition": condition,
                            "repetition": repetition,
                            "pattern": pattern,
                            "pattern_hex": f"0x{pattern:02x}",
                            "transitioned_utc": transitioned_utc,
                            "captured_utc": utc_now(),
                            "runtime_address": f"0x{address:x}",
                            "sha256": sha256,
                            "ones_fraction": sum(v.bit_count() for v in data)
                            / (8.0 * len(data)),
                            "bit_agreement": bit_agreement(data, pattern),
                            "byte_match_fraction": data.count(pattern) / len(data),
                            "file": f"raw/{filename}",
                        }
                        rows.append(row)
                        payloads.append(data)
                        print(
                            f"[host] capture {capture_index + 1}/"
                            f"{len(conditions) * args.repetitions * len(PATTERNS)} "
                            f"condition={condition} after={pattern:02x} "
                            f"agreement={row['bit_agreement']:.6f}",
                            flush=True,
                        )
                        capture_index += 1
    finally:
        port.close()

    csv_fields = list(rows[0].keys())
    with (out_dir / "captures.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = summarize(rows, payloads)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    with (out_dir / "summary.csv").open("w", newline="") as handle:
        fields = list(summary["groups"][0].keys())
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary["groups"])

    print(f"[host] completed {len(rows)} captures")
    print(f"[host] results {out_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, serial.SerialException) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
