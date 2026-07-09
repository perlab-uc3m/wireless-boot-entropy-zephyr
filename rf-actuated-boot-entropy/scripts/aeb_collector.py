#!/usr/bin/env python3
"""Client-initiated RF-actuated entropy collector.

The ESP32 sends a boot-style HELLO. This script replies with a public START
descriptor and BURST packets, then stores the ESP32's pre-hash WDEV bytes as raw
binary files suitable for entropy-test batteries.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import secrets
import socket
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple


MAGIC = 0x31424541
VERSION = 1

TYPE_START = 1
TYPE_BURST = 2
TYPE_HELLO = 3
TYPE_RAW_BEGIN = 4
TYPE_RAW_CHUNK = 5
TYPE_RAW_END = 6
TYPE_JITTER_CHUNK = 7

HEADER = struct.Struct("<IHHIIII16s")
Peer = Tuple[str, int]
StateKey = Tuple[Peer, int]


@dataclass
class Message:
    msg_type: int
    trial: int
    seq_or_count: int
    interval_us: int
    sample_bytes: int
    nonce: bytes
    payload: bytes


@dataclass
class TrialState:
    capture_id: int
    peer: Peer
    protocol_trial: int
    hello_nonce: bytes
    start_nonce: bytes
    bursts: int
    interval_us: int
    requested_sample_bytes: int
    raw_chunks_expected: Optional[int] = None
    raw_len: Optional[int] = None
    raw_chunks: Dict[int, bytes] = field(default_factory=dict)
    jitter_chunks: Dict[int, bytes] = field(default_factory=dict)
    result: Dict[str, str] = field(default_factory=dict)
    hello_time: float = field(default_factory=time.time)
    start_send_time: Optional[float] = None
    first_burst_send_time: Optional[float] = None
    last_burst_send_time: Optional[float] = None
    result_time: Optional[float] = None


def parse_args() -> argparse.Namespace:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(
        description="Collect raw WDEV bytes from the client-initiated AEB artifact"
    )
    parser.add_argument("--bind", default="0.0.0.0", help="IPv4 bind address")
    parser.add_argument("--port", type=int, default=7778, help="UDP bind port")
    parser.add_argument(
        "--trials",
        type=int,
        default=16,
        help="Stop after this many complete raw trials",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("results") / f"aeb_client_{stamp}"
    )
    parser.add_argument(
        "--sample-bytes", type=int, help="Override ESP32-requested bytes per trial"
    )
    parser.add_argument(
        "--bursts", type=int, help="Override ESP32-requested burst count"
    )
    parser.add_argument(
        "--interval-us", type=int, help="Override ESP32-requested burst spacing"
    )
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=64,
        help="Extra payload in each BURST packet",
    )
    parser.add_argument(
        "--payload-mode",
        choices=["constant", "counter", "random"],
        default="constant",
        help="BURST payload pattern; random is public metadata",
    )
    parser.add_argument(
        "--interval-jitter-us",
        type=int,
        default=0,
        help="Uniform per-packet interval jitter in +/- microseconds",
    )
    parser.add_argument(
        "--stimulus-seed",
        type=int,
        help="Seed for reproducible randomized stimulus schedules",
    )
    parser.add_argument(
        "--start-delay-ms",
        type=int,
        default=20,
        help="Delay between START and first BURST",
    )
    parser.add_argument(
        "--fixed-nonce",
        action="store_true",
        help="Use the same public START nonce for every trial",
    )
    parser.add_argument(
        "--nonce-hex", help="16-byte fixed nonce as hex; implies --fixed-nonce"
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=90.0,
        help="Stop after this many seconds with no UDP traffic",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to existing aeb_all.bin instead of replacing it",
    )
    return parser.parse_args()


def parse_message(data: bytes) -> Optional[Message]:
    if len(data) < HEADER.size:
        return None

    magic, version, msg_type, trial, seq, interval_us, sample_bytes, nonce = (
        HEADER.unpack_from(data)
    )
    if magic != MAGIC or version != VERSION:
        return None

    return Message(
        msg_type=msg_type,
        trial=trial,
        seq_or_count=seq,
        interval_us=interval_us,
        sample_bytes=sample_bytes,
        nonce=nonce,
        payload=data[HEADER.size :],
    )


def make_message(
    msg_type: int,
    trial: int,
    seq_or_count: int,
    interval_us: int,
    sample_bytes: int,
    nonce: bytes,
    payload: bytes = b"",
) -> bytes:
    return (
        HEADER.pack(
            MAGIC,
            VERSION,
            msg_type,
            trial,
            seq_or_count,
            interval_us,
            sample_bytes,
            nonce,
        )
        + payload
    )


def parse_result_line(data: bytes) -> Optional[Dict[str, str]]:
    try:
        text = data.decode("utf-8", errors="replace").strip()
    except UnicodeDecodeError:
        return None

    if not text.startswith("[AEB_RESULT]"):
        return None

    fields: Dict[str, str] = {}
    for token in text.split()[1:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        fields[key] = value
    return fields


def fixed_nonce(args: argparse.Namespace) -> bytes:
    if args.nonce_hex:
        raw = bytes.fromhex(args.nonce_hex)
        if len(raw) != 16:
            raise SystemExit("--nonce-hex must encode exactly 16 bytes")
        return raw
    return b"\x00" * 16


def pack_jitter_delta_u32le(values: list[int]) -> bytes:
    if not values:
        return b""
    return struct.pack("<" + "I" * len(values), *values)


def pack_jitter_residual_s32le(values: list[int], interval_us: int) -> bytes:
    if not values:
        return b""
    residuals = [value - interval_us for value in values]
    return struct.pack("<" + "i" * len(residuals), *residuals)


def prepare_outputs(args: argparse.Namespace) -> Tuple[Path, Path, Path, Path, Path, Path, Path, Path]:
    raw_dir = args.out_dir / "raw"
    jitter_dir = args.out_dir / "jitter"
    joint_dir = args.out_dir / "joint"
    stimulus_dir = args.out_dir / "stimulus"
    raw_dir.mkdir(parents=True, exist_ok=True)
    jitter_dir.mkdir(parents=True, exist_ok=True)
    joint_dir.mkdir(parents=True, exist_ok=True)
    stimulus_dir.mkdir(parents=True, exist_ok=True)

    all_bin = raw_dir / "aeb_all.bin"
    all_jitter_delta = jitter_dir / "aeb_jitter_delta_u32le_all.bin"
    all_jitter_residual = jitter_dir / "aeb_jitter_residual_s32le_all.bin"
    all_joint = joint_dir / "aeb_response_all.bin"
    csv_path = args.out_dir / "aeb_trials.csv"
    manifest_path = args.out_dir / "manifest.json"

    if not args.append:
        for path in (all_bin, all_jitter_delta, all_jitter_residual, all_joint):
            if path.exists():
                path.unlink()
    if csv_path.exists() and not args.append:
        csv_path.unlink()

    return (
        raw_dir,
        jitter_dir,
        joint_dir,
        all_bin,
        all_jitter_delta,
        all_jitter_residual,
        all_joint,
        csv_path,
        manifest_path,
    )


def write_manifest(path: Path, args: argparse.Namespace, completed: int) -> None:
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "bind": args.bind,
        "port": args.port,
        "target_complete_trials": args.trials,
        "completed_trials": completed,
        "sample_bytes_override": args.sample_bytes,
        "bursts_override": args.bursts,
        "interval_us_override": args.interval_us,
        "payload_bytes": args.payload_bytes,
        "payload_mode": args.payload_mode,
        "interval_jitter_us": args.interval_jitter_us,
        "stimulus_seed": args.stimulus_seed,
        "fixed_nonce": bool(args.fixed_nonce or args.nonce_hex),
        "raw_stream": "pre_hash_wdev_bytes",
        "jitter_stream": "packet_arrival_deltas_us_u32le_and_residual_s32le",
        "joint_stream": "pre_hash_wdev_bytes_followed_by_packet_arrival_deltas_us_u32le",
        "stimulus_stream": "public_start_and_burst_schedule",
        "host_timing": (
            "CSV records HELLO-to-START, START-to-last-BURST, first-to-last-BURST, "
            "and HELLO-to-AEB_RESULT wall-clock timings measured by the collector"
        ),
        "conditioning": "none for raw files; SHA-256/HKDF only in metadata",
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def ensure_csv(path: Path) -> csv.DictWriter:
    fieldnames = [
        "capture_id",
        "protocol_trial",
        "peer",
        "complete",
        "raw_file",
        "raw_bytes",
        "raw_chunks_expected",
        "raw_chunks_seen",
        "missing_chunks",
        "raw_sha256_file",
        "raw_sha256_reported",
        "raw_sha256_match",
        "jitter_file",
        "jitter_delta_file",
        "jitter_residual_file",
        "jitter_delta_bytes",
        "jitter_sha256_file",
        "jitter_sha256_reported",
        "jitter_sha256_match",
        "jitter_count",
        "response_file",
        "response_bytes",
        "response_sha256_file",
        "response_sha256_reported",
        "response_sha256_match",
        "condition",
        "packets_expected",
        "packets_seen",
        "interval_us",
        "sample_us",
        "host_hello_to_start_ms",
        "host_start_to_last_burst_ms",
        "host_first_to_last_burst_ms",
        "host_hello_to_result_ms",
        "ones",
        "transitions",
        "jitter_min_us",
        "jitter_mean_us",
        "jitter_max_us",
        "response_sha256",
        "seed_sha256",
        "hello_nonce",
        "start_nonce",
    ]
    exists = path.exists()
    handle = path.open("a", newline="")
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer._aeb_handle = handle  # type: ignore[attr-defined]
    if not exists:
        writer.writeheader()
        handle.flush()
    return writer


def close_csv(writer: csv.DictWriter) -> None:
    handle = getattr(writer, "_aeb_handle", None)
    if handle is not None:
        handle.close()


def jitter_values(state: TrialState) -> list[int]:
    values: list[int] = []
    for index in sorted(state.jitter_chunks):
        payload = state.jitter_chunks[index]
        usable = len(payload) - (len(payload) % 4)
        values.extend(struct.unpack("<" + "I" * (usable // 4), payload[:usable]))
    return values


def finalize_trial(
    state: TrialState,
    raw_dir: Path,
    jitter_dir: Path,
    joint_dir: Path,
    all_bin: Path,
    all_jitter_delta: Path,
    all_jitter_residual: Path,
    all_joint: Path,
    writer: csv.DictWriter,
) -> bool:
    expected = state.raw_chunks_expected
    raw_len = state.raw_len
    missing = []

    if expected is None:
        expected = max(state.raw_chunks.keys(), default=-1) + 1
    if raw_len is None:
        raw_len = int(state.result.get("sample_bytes", "0") or 0)

    missing = [i for i in range(expected) if i not in state.raw_chunks]
    complete = expected > 0 and raw_len > 0 and not missing

    raw_file = ""
    raw_hash = ""
    hash_match = ""
    raw_bytes = 0
    raw = b""

    if complete:
        raw = b"".join(state.raw_chunks[i] for i in range(expected))[:raw_len]
        raw_bytes = len(raw)
        complete = raw_bytes == raw_len
        if complete:
            raw_hash = hashlib.sha256(raw).hexdigest()
            raw_file_path = raw_dir / f"aeb_trial_{state.capture_id:06d}.bin"
            raw_file_path.write_bytes(raw)
            with all_bin.open("ab") as handle:
                handle.write(raw)
            raw_file = str(raw_file_path)
            reported = state.result.get("raw_sha256", "")
            hash_match = str(bool(reported and reported == raw_hash))

    if not complete and state.raw_chunks:
        partial = b"".join(state.raw_chunks[i] for i in sorted(state.raw_chunks))
        raw_bytes = len(partial)
        raw_hash = hashlib.sha256(partial).hexdigest()
        raw_file_path = raw_dir / f"aeb_trial_{state.capture_id:06d}.partial.bin"
        raw_file_path.write_bytes(partial)
        raw_file = str(raw_file_path)

    jitters = jitter_values(state)
    jitter_file = ""
    jitter_delta_file = ""
    jitter_residual_file = ""
    jitter_delta_hash = ""
    jitter_hash_match = ""
    jitter_delta_bytes = b""
    jitter_residual_bytes = b""
    if jitters:
        interval_us = int(state.result.get("interval_us", state.interval_us) or state.interval_us)
        jitter_delta_bytes = pack_jitter_delta_u32le(jitters)
        jitter_residual_bytes = pack_jitter_residual_s32le(jitters, interval_us)
        jitter_delta_hash = hashlib.sha256(jitter_delta_bytes).hexdigest()
        jitter_reported = state.result.get("jitter_sha256", "")
        jitter_hash_match = str(bool(jitter_reported and jitter_reported == jitter_delta_hash))

        jitter_file_path = jitter_dir / f"aeb_trial_{state.capture_id:06d}.csv"
        with jitter_file_path.open("w", newline="") as handle:
            jitter_writer = csv.writer(handle)
            jitter_writer.writerow(["index", "delta_us", "residual_us"])
            for index, value in enumerate(jitters):
                jitter_writer.writerow([index, value, value - interval_us])
        jitter_file = str(jitter_file_path)

        jitter_delta_path = jitter_dir / f"aeb_trial_{state.capture_id:06d}.delta_u32le.bin"
        jitter_delta_path.write_bytes(jitter_delta_bytes)
        jitter_delta_file = str(jitter_delta_path)

        jitter_residual_path = jitter_dir / f"aeb_trial_{state.capture_id:06d}.residual_s32le.bin"
        jitter_residual_path.write_bytes(jitter_residual_bytes)
        jitter_residual_file = str(jitter_residual_path)

        with all_jitter_delta.open("ab") as handle:
            handle.write(jitter_delta_bytes)
        with all_jitter_residual.open("ab") as handle:
            handle.write(jitter_residual_bytes)

    response_file = ""
    response_bytes_len = 0
    response_hash = ""
    response_hash_match = ""
    if complete:
        response_bytes = raw + jitter_delta_bytes
        response_bytes_len = len(response_bytes)
        response_hash = hashlib.sha256(response_bytes).hexdigest()
        response_reported = state.result.get("response_sha256", "")
        response_hash_match = str(bool(response_reported and response_reported == response_hash))
        response_file_path = joint_dir / f"aeb_trial_{state.capture_id:06d}.response.bin"
        response_file_path.write_bytes(response_bytes)
        with all_joint.open("ab") as handle:
            handle.write(response_bytes)
        response_file = str(response_file_path)

    hello_to_start_ms = ""
    start_to_last_burst_ms = ""
    first_to_last_burst_ms = ""
    hello_to_result_ms = ""
    if state.start_send_time is not None:
        hello_to_start_ms = f"{(state.start_send_time - state.hello_time) * 1000:.3f}"
    if state.start_send_time is not None and state.last_burst_send_time is not None:
        start_to_last_burst_ms = (
            f"{(state.last_burst_send_time - state.start_send_time) * 1000:.3f}"
        )
    if state.first_burst_send_time is not None and state.last_burst_send_time is not None:
        first_to_last_burst_ms = (
            f"{(state.last_burst_send_time - state.first_burst_send_time) * 1000:.3f}"
        )
    if state.result_time is not None:
        hello_to_result_ms = f"{(state.result_time - state.hello_time) * 1000:.3f}"

    writer.writerow(
        {
            "capture_id": state.capture_id,
            "protocol_trial": state.protocol_trial,
            "peer": f"{state.peer[0]}:{state.peer[1]}",
            "complete": str(complete),
            "raw_file": raw_file,
            "raw_bytes": raw_bytes,
            "raw_chunks_expected": expected,
            "raw_chunks_seen": len(state.raw_chunks),
            "missing_chunks": " ".join(str(i) for i in missing),
            "raw_sha256_file": raw_hash,
            "raw_sha256_reported": state.result.get("raw_sha256", ""),
            "raw_sha256_match": hash_match,
            "jitter_file": jitter_file,
            "jitter_delta_file": jitter_delta_file,
            "jitter_residual_file": jitter_residual_file,
            "jitter_delta_bytes": len(jitter_delta_bytes),
            "jitter_sha256_file": jitter_delta_hash,
            "jitter_sha256_reported": state.result.get("jitter_sha256", ""),
            "jitter_sha256_match": jitter_hash_match,
            "jitter_count": len(jitters),
            "response_file": response_file,
            "response_bytes": response_bytes_len,
            "response_sha256_file": response_hash,
            "response_sha256_reported": state.result.get("response_sha256", ""),
            "response_sha256_match": response_hash_match,
            "condition": state.result.get("condition", ""),
            "packets_expected": state.result.get("packets_expected", ""),
            "packets_seen": state.result.get("packets_seen", ""),
            "interval_us": state.result.get("interval_us", state.interval_us),
            "sample_us": state.result.get("sample_us", ""),
            "host_hello_to_start_ms": hello_to_start_ms,
            "host_start_to_last_burst_ms": start_to_last_burst_ms,
            "host_first_to_last_burst_ms": first_to_last_burst_ms,
            "host_hello_to_result_ms": hello_to_result_ms,
            "ones": state.result.get("ones", ""),
            "transitions": state.result.get("transitions", ""),
            "jitter_min_us": state.result.get("jitter_min_us", ""),
            "jitter_mean_us": state.result.get("jitter_mean_us", ""),
            "jitter_max_us": state.result.get("jitter_max_us", ""),
            "response_sha256": state.result.get("response_sha256", ""),
            "seed_sha256": state.result.get("seed_sha256", ""),
            "hello_nonce": state.hello_nonce.hex(),
            "start_nonce": state.start_nonce.hex(),
        }
    )
    csv_handle = getattr(writer, "_aeb_handle", None)
    if csv_handle is not None:
        csv_handle.flush()

    return complete


def send_stimulus(
    sock: socket.socket,
    peer: Peer,
    msg: Message,
    args: argparse.Namespace,
    nonce: bytes,
    rng: random.Random,
    capture_id: int,
    hello_time: float,
) -> TrialState:
    bursts = args.bursts if args.bursts is not None else msg.seq_or_count
    interval_us = args.interval_us if args.interval_us is not None else msg.interval_us
    sample_bytes = (
        args.sample_bytes if args.sample_bytes is not None else msg.sample_bytes
    )

    start = make_message(
        TYPE_START, msg.trial, bursts, interval_us, sample_bytes, nonce
    )
    start_send_time = time.time()
    sock.sendto(start, peer)
    time.sleep(args.start_delay_ms / 1000.0)
    first_burst_send_time: Optional[float] = None
    last_burst_send_time: Optional[float] = None

    stimulus_dir = args.out_dir / "stimulus"
    stimulus_dir.mkdir(parents=True, exist_ok=True)
    stimulus_path = stimulus_dir / f"aeb_trial_{capture_id:06d}.csv"
    with stimulus_path.open("w", newline="") as stimulus_handle:
        stimulus_writer = csv.writer(stimulus_handle)
        stimulus_writer.writerow(
            [
                "seq",
                "sleep_us",
                "payload_mode",
                "payload_len",
                "payload_hex",
                "payload_sha256",
            ]
        )

        for seq in range(bursts):
            sleep_us = interval_us
            if args.interval_jitter_us:
                sleep_us += rng.randint(
                    -args.interval_jitter_us, args.interval_jitter_us
                )
                sleep_us = max(0, sleep_us)

            if args.payload_mode == "random":
                payload_len = max(0, args.payload_bytes)
                if args.stimulus_seed is None:
                    payload = secrets.token_bytes(payload_len)
                else:
                    payload = rng.randbytes(payload_len)
            elif args.payload_mode == "counter":
                payload = bytes(
                    (seq + i) & 0xFF for i in range(max(0, args.payload_bytes))
                )
            else:
                payload = b"S" * max(0, args.payload_bytes)

            packet = make_message(
                TYPE_BURST, msg.trial, seq, sleep_us, sample_bytes, nonce, payload
            )
            now = time.time()
            if first_burst_send_time is None:
                first_burst_send_time = now
            last_burst_send_time = now
            sock.sendto(packet, peer)
            stimulus_writer.writerow(
                [
                    seq,
                    sleep_us,
                    args.payload_mode,
                    len(payload),
                    payload.hex(),
                    hashlib.sha256(payload).hexdigest() if payload else "",
                ]
            )
            if sleep_us > 0:
                time.sleep(sleep_us / 1_000_000.0)

    return TrialState(
        capture_id=capture_id,
        peer=peer,
        protocol_trial=msg.trial,
        hello_nonce=msg.nonce,
        start_nonce=nonce,
        bursts=bursts,
        interval_us=interval_us,
        requested_sample_bytes=sample_bytes,
        hello_time=hello_time,
        start_send_time=start_send_time,
        first_burst_send_time=first_burst_send_time,
        last_burst_send_time=last_burst_send_time,
    )


def main() -> None:
    args = parse_args()
    if args.nonce_hex:
        args.fixed_nonce = True
    if args.payload_bytes < 0:
        raise SystemExit("--payload-bytes must be >= 0")
    if args.interval_jitter_us < 0:
        raise SystemExit("--interval-jitter-us must be >= 0")

    (
        raw_dir,
        jitter_dir,
        joint_dir,
        all_bin,
        all_jitter_delta,
        all_jitter_residual,
        all_joint,
        csv_path,
        manifest_path,
    ) = prepare_outputs(args)
    write_manifest(manifest_path, args, completed=0)
    writer = ensure_csv(csv_path)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(1.0)

    states: Dict[StateKey, TrialState] = {}
    completed = 0
    capture_counter = 0
    last_rx = time.time()
    fixed = fixed_nonce(args) if args.fixed_nonce else None
    rng = random.Random(args.stimulus_seed)

    print(f"[collector] listening on {args.bind}:{args.port}")
    print(f"[collector] output: {args.out_dir}")
    print("[collector] raw stream: pre-hash WDEV bytes")

    try:
        while completed < args.trials:
            try:
                data, peer = sock.recvfrom(4096)
                rx_time = time.time()
            except socket.timeout:
                if time.time() - last_rx > args.idle_timeout:
                    print("[collector] idle timeout")
                    break
                continue

            last_rx = time.time()

            result = parse_result_line(data)
            if result is not None:
                trial = int(result.get("trial", "-1"))
                key = (peer, trial)
                state = states.get(key)
                if state is None:
                    continue
                state.result.update(result)
                state.result_time = rx_time
                print(
                    f"[collector] result trial={trial} raw_sha256="
                    f"{result.get('raw_sha256', '')[:16]}..."
                )
                continue

            msg = parse_message(data)
            if msg is None:
                continue

            key = (peer, msg.trial)

            if msg.msg_type == TYPE_HELLO:
                nonce = fixed if fixed is not None else msg.nonce
                capture_id = capture_counter
                capture_counter += 1
                state = send_stimulus(sock, peer, msg, args, nonce, rng, capture_id, rx_time)
                states[key] = state
                print(
                    f"[collector] hello capture={state.capture_id} "
                    f"trial={msg.trial} peer={peer[0]}:{peer[1]} "
                    f"bytes={state.requested_sample_bytes} bursts={state.bursts}"
                )
                continue

            state = states.get(key)
            if state is None:
                continue

            if msg.msg_type == TYPE_RAW_BEGIN:
                state.raw_chunks_expected = msg.seq_or_count
                state.raw_len = msg.sample_bytes
            elif msg.msg_type == TYPE_RAW_CHUNK:
                state.raw_chunks[msg.seq_or_count] = msg.payload[: msg.sample_bytes]
            elif msg.msg_type == TYPE_JITTER_CHUNK:
                state.jitter_chunks[msg.seq_or_count] = msg.payload[: msg.sample_bytes]
            elif msg.msg_type == TYPE_RAW_END:
                if state.raw_chunks_expected is None:
                    state.raw_chunks_expected = msg.seq_or_count
                if state.raw_len is None:
                    state.raw_len = msg.sample_bytes
                complete = finalize_trial(
                    state,
                    raw_dir,
                    jitter_dir,
                    joint_dir,
                    all_bin,
                    all_jitter_delta,
                    all_jitter_residual,
                    all_joint,
                    writer,
                )
                states.pop(key, None)
                if complete:
                    completed += 1
                    print(
                        f"[collector] complete capture={state.capture_id} "
                        f"({completed}/{args.trials})"
                    )
                else:
                    print(f"[collector] partial capture={state.capture_id}")

    except KeyboardInterrupt:
        print("\n[collector] interrupted")
    finally:
        close_csv(writer)
        write_manifest(manifest_path, args, completed=completed)
        sock.close()

    print(f"[collector] completed={completed}")
    print(f"[collector] raw_all={all_bin}")
    print(f"[collector] jitter_delta_all={all_jitter_delta}")
    print(f"[collector] jitter_residual_all={all_jitter_residual}")
    print(f"[collector] joint_all={all_joint}")
    print(f"[collector] csv={csv_path}")


if __name__ == "__main__":
    main()
