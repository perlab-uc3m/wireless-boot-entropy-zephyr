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


def prepare_outputs(args: argparse.Namespace) -> Tuple[Path, Path, Path, Path, Path]:
    raw_dir = args.out_dir / "raw"
    jitter_dir = args.out_dir / "jitter"
    stimulus_dir = args.out_dir / "stimulus"
    raw_dir.mkdir(parents=True, exist_ok=True)
    jitter_dir.mkdir(parents=True, exist_ok=True)
    stimulus_dir.mkdir(parents=True, exist_ok=True)

    all_bin = raw_dir / "aeb_all.bin"
    csv_path = args.out_dir / "aeb_trials.csv"
    manifest_path = args.out_dir / "manifest.json"

    if all_bin.exists() and not args.append:
        all_bin.unlink()
    if csv_path.exists() and not args.append:
        csv_path.unlink()

    return raw_dir, jitter_dir, all_bin, csv_path, manifest_path


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
        "jitter_stream": "packet_arrival_deltas_us",
        "stimulus_stream": "public_start_and_burst_schedule",
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
        "jitter_count",
        "condition",
        "packets_expected",
        "packets_seen",
        "interval_us",
        "sample_us",
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
    all_bin: Path,
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
    if jitters:
        jitter_file_path = jitter_dir / f"aeb_trial_{state.capture_id:06d}.csv"
        with jitter_file_path.open("w", newline="") as handle:
            jitter_writer = csv.writer(handle)
            jitter_writer.writerow(["index", "delta_us"])
            for index, value in enumerate(jitters):
                jitter_writer.writerow([index, value])
        jitter_file = str(jitter_file_path)

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
            "jitter_count": len(jitters),
            "condition": state.result.get("condition", ""),
            "packets_expected": state.result.get("packets_expected", ""),
            "packets_seen": state.result.get("packets_seen", ""),
            "interval_us": state.result.get("interval_us", state.interval_us),
            "sample_us": state.result.get("sample_us", ""),
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
) -> TrialState:
    bursts = args.bursts if args.bursts is not None else msg.seq_or_count
    interval_us = args.interval_us if args.interval_us is not None else msg.interval_us
    sample_bytes = (
        args.sample_bytes if args.sample_bytes is not None else msg.sample_bytes
    )

    start = make_message(
        TYPE_START, msg.trial, bursts, interval_us, sample_bytes, nonce
    )
    sock.sendto(start, peer)
    time.sleep(args.start_delay_ms / 1000.0)

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
    )


def main() -> None:
    args = parse_args()
    if args.nonce_hex:
        args.fixed_nonce = True
    if args.payload_bytes < 0:
        raise SystemExit("--payload-bytes must be >= 0")
    if args.interval_jitter_us < 0:
        raise SystemExit("--interval-jitter-us must be >= 0")

    raw_dir, jitter_dir, all_bin, csv_path, manifest_path = prepare_outputs(args)
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
                state = send_stimulus(sock, peer, msg, args, nonce, rng, capture_id)
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
                complete = finalize_trial(state, raw_dir, jitter_dir, all_bin, writer)
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
    print(f"[collector] csv={csv_path}")


if __name__ == "__main__":
    main()
