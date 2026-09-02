#!/usr/bin/env python3
"""Analyze AEB source streams separately and jointly.

The collector preserves three distinct views of each trial:

* W_i: pre-hash WDEV bytes.
* J_i: packet-arrival deltas, encoded as uint32 little-endian microseconds.
* R_i: W_i followed by J_i, matching the firmware response hash input.

This script produces source-separated binary streams and conservative screening
summaries. Jitter estimates are reported per packet position because timing
deltas are structured by the public burst schedule.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import shutil
import struct
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze WDEV, jitter, residual jitter, and joint AEB streams"
    )
    parser.add_argument("run_dir", type=Path, help="Collector run directory")
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Output directory (default: <run_dir>/source_analysis)",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=2000,
        help="Maximum trial pairs sampled for Hamming distances",
    )
    parser.add_argument("--seed", type=int, default=1, help="Pair sampling seed")
    return parser.parse_args()


def load_rows(run_dir: Path) -> list[dict[str, str]]:
    with (run_dir / "aeb_trials.csv").open(newline="") as handle:
        return list(csv.DictReader(handle))


def load_manifest(run_dir: Path) -> dict:
    path = run_dir / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def resolve_path(run_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.exists():
        return path

    parts = path.parts
    for marker in ("raw", "jitter", "joint", "stimulus"):
        if marker in parts:
            suffix = Path(*parts[parts.index(marker) :])
            candidate = run_dir / suffix
            if candidate.exists():
                return candidate

    candidate = run_dir / path
    if candidate.exists():
        return candidate
    bench_dir = run_dir.parent.parent
    candidate = bench_dir / path
    if candidate.exists():
        return candidate
    for marker in ("raw", "jitter", "joint"):
        candidate = run_dir / marker / path.name
        if candidate.exists():
            return candidate
    return path


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def byte_shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def mcv_min_entropy(values: Iterable[int]) -> float:
    counts = Counter(values)
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return -math.log2(max(counts.values()) / total)


def byte_mcv_min_entropy(data: bytes) -> float:
    return mcv_min_entropy(data)


def bit_count(data: bytes) -> int:
    return sum(value.bit_count() for value in data)


def bit_transitions(data: bytes) -> int:
    transitions = 0
    previous = None
    for value in data:
        for bit in range(7, -1, -1):
            current = (value >> bit) & 1
            if previous is not None and current != previous:
                transitions += 1
            previous = current
    return transitions


def hamming_bits(a: bytes, b: bytes) -> int:
    return sum((x ^ y).bit_count() for x, y in zip(a, b))


def pair_indices(n: int, max_pairs: int, seed: int) -> Iterable[tuple[int, int]]:
    all_pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    if len(all_pairs) <= max_pairs:
        return all_pairs
    rng = random.Random(seed)
    return rng.sample(all_pairs, max_pairs)


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "mean": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "mean": mean(values),
        "max": max(values),
    }


def binary_stats(data: bytes) -> dict[str, float | int | str | None]:
    if not data:
        return {
            "bytes": 0,
            "sha256": sha256_hex(data),
            "byte_shannon_entropy": 0.0,
            "byte_mcv_min_entropy": 0.0,
            "ones_ratio": None,
            "transition_ratio": None,
        }
    bits = len(data) * 8
    transitions_den = bits - 1 if bits > 1 else 1
    return {
        "bytes": len(data),
        "sha256": sha256_hex(data),
        "byte_shannon_entropy": byte_shannon_entropy(data),
        "byte_mcv_min_entropy": byte_mcv_min_entropy(data),
        "ones_ratio": bit_count(data) / bits,
        "transition_ratio": bit_transitions(data) / transitions_den,
    }


def load_jitter_csv(path: Path) -> tuple[list[int], list[int]]:
    deltas: list[int] = []
    residuals: list[int] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            delta = int(row["delta_us"])
            if "residual_us" in row and row["residual_us"] != "":
                residual = int(row["residual_us"])
            else:
                residual = delta
            deltas.append(delta)
            residuals.append(residual)
    return deltas, residuals


def pack_u32(values: list[int]) -> bytes:
    if not values:
        return b""
    return struct.pack("<" + "I" * len(values), *values)


def pack_s32(values: list[int]) -> bytes:
    if not values:
        return b""
    return struct.pack("<" + "i" * len(values), *values)


def low8(values: list[int]) -> bytes:
    return bytes(value & 0xFF for value in values)


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def source_pair_hamming(windows: list[bytes], max_pairs: int, seed: int) -> dict:
    distances: list[float] = []
    for i, j in pair_indices(len(windows), max_pairs, seed):
        denom = min(len(windows[i]), len(windows[j])) * 8
        if denom:
            distances.append(100 * hamming_bits(windows[i], windows[j]) / denom)
    return summarize(distances)


def consecutive_hamming(windows: list[bytes]) -> dict:
    distances: list[float] = []
    for first, second in zip(windows, windows[1:]):
        denominator = min(len(first), len(second)) * 8
        if denominator:
            distances.append(100 * hamming_bits(first, second) / denominator)
    return summarize(distances)


def byte_pearson(first: bytes, second: bytes) -> float | None:
    pairs = list(zip(first, second))
    if not pairs:
        return None
    first_mean = mean(value for value, _ in pairs)
    second_mean = mean(value for _, value in pairs)
    covariance = sum(
        (first_value - first_mean) * (second_value - second_mean)
        for first_value, second_value in pairs
    )
    first_variance = sum((value - first_mean) ** 2 for value, _ in pairs)
    second_variance = sum((value - second_mean) ** 2 for _, value in pairs)
    denominator = math.sqrt(first_variance * second_variance)
    if denominator == 0:
        return None
    return covariance / denominator


def consecutive_byte_pearson(windows: list[bytes]) -> dict:
    correlations = [
        correlation
        for first, second in zip(windows, windows[1:])
        if (correlation := byte_pearson(first, second)) is not None
    ]
    return summarize(correlations)


def deterministic_stimulus(manifest: dict) -> bool:
    return bool(
        manifest.get("fixed_nonce")
        and manifest.get("payload_mode") == "constant"
        and int(manifest.get("interval_jitter_us") or 0) == 0
    )


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    out_dir = (args.out_dir or (run_dir / "source_analysis")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_rows(run_dir)
    manifest = load_manifest(run_dir)
    complete_rows = [row for row in rows if row.get("complete") == "True"]

    raw_windows: list[bytes] = []
    jitter_vectors: list[list[int]] = []
    residual_vectors: list[list[int]] = []
    joint_windows: list[bytes] = []
    trial_rows: list[dict[str, object]] = []

    for row in complete_rows:
        capture_id = int(row["capture_id"])
        raw_path = resolve_path(run_dir, row["raw_file"])
        jitter_path = resolve_path(run_dir, row["jitter_file"])
        raw = raw_path.read_bytes()
        deltas, residuals = load_jitter_csv(jitter_path)
        delta_bytes = pack_u32(deltas)
        residual_bytes = pack_s32(residuals)
        joint = raw + delta_bytes

        raw_windows.append(raw)
        jitter_vectors.append(deltas)
        residual_vectors.append(residuals)
        joint_windows.append(joint)

        trial_rows.append(
            {
                "capture_id": capture_id,
                "raw_bytes": len(raw),
                "jitter_count": len(deltas),
                "raw_sha256": sha256_hex(raw),
                "jitter_delta_sha256": sha256_hex(delta_bytes),
                "jitter_residual_sha256": sha256_hex(residual_bytes),
                "joint_response_sha256": sha256_hex(joint),
                "raw_byte_mcv_min_entropy": byte_mcv_min_entropy(raw),
                "jitter_delta_mcv_min_entropy": mcv_min_entropy(deltas),
                "jitter_residual_mcv_min_entropy": mcv_min_entropy(residuals),
                "jitter_delta_unique": len(set(deltas)),
                "jitter_residual_unique": len(set(residuals)),
            }
        )

    raw_all = b"".join(raw_windows)
    jitter_delta_values = [value for vector in jitter_vectors for value in vector]
    jitter_residual_values = [value for vector in residual_vectors for value in vector]
    jitter_delta_u32 = pack_u32(jitter_delta_values)
    jitter_residual_s32 = pack_s32(jitter_residual_values)
    jitter_delta_low8 = low8(jitter_delta_values)
    jitter_residual_low8 = low8(jitter_residual_values)
    joint_all = b"".join(joint_windows)

    write_bytes(out_dir / "wdev_all.bin", raw_all)
    write_bytes(out_dir / "jitter_delta_u32le_all.bin", jitter_delta_u32)
    write_bytes(out_dir / "jitter_residual_s32le_all.bin", jitter_residual_s32)
    write_bytes(out_dir / "jitter_delta_low8_all.bin", jitter_delta_low8)
    write_bytes(out_dir / "jitter_residual_low8_all.bin", jitter_residual_low8)
    write_bytes(out_dir / "response_joint_all.bin", joint_all)

    # Keep checksums close to the exported streams for paper/data curation.
    for path in out_dir.glob("*.bin"):
        (path.with_suffix(path.suffix + ".sha256")).write_text(
            f"{sha256_hex(path.read_bytes())}  {path.name}\n"
        )

    with (out_dir / "source_trials.csv").open("w", newline="") as handle:
        fieldnames = list(trial_rows[0].keys()) if trial_rows else []
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(trial_rows)

    by_position_delta: dict[int, list[int]] = defaultdict(list)
    by_position_residual: dict[int, list[int]] = defaultdict(list)
    for deltas, residuals in zip(jitter_vectors, residual_vectors):
        for index, value in enumerate(deltas):
            by_position_delta[index].append(value)
        for index, value in enumerate(residuals):
            by_position_residual[index].append(value)

    position_rows: list[dict[str, object]] = []
    for index in sorted(set(by_position_delta) | set(by_position_residual)):
        deltas = by_position_delta[index]
        residuals = by_position_residual[index]
        delta_counts = Counter(deltas)
        residual_counts = Counter(residuals)
        delta_mcv = max(delta_counts.values()) if delta_counts else 0
        residual_mcv = max(residual_counts.values()) if residual_counts else 0
        position_rows.append(
            {
                "index": index,
                "count": len(deltas),
                "delta_unique": len(delta_counts),
                "delta_mcv_count": delta_mcv,
                "delta_mcv_probability": delta_mcv / len(deltas) if deltas else "",
                "delta_mcv_min_entropy": mcv_min_entropy(deltas),
                "delta_min_us": min(deltas) if deltas else "",
                "delta_mean_us": mean(deltas) if deltas else "",
                "delta_max_us": max(deltas) if deltas else "",
                "residual_unique": len(residual_counts),
                "residual_mcv_count": residual_mcv,
                "residual_mcv_probability": residual_mcv / len(residuals)
                if residuals
                else "",
                "residual_mcv_min_entropy": mcv_min_entropy(residuals),
                "residual_min_us": min(residuals) if residuals else "",
                "residual_mean_us": mean(residuals) if residuals else "",
                "residual_max_us": max(residuals) if residuals else "",
            }
        )

    with (out_dir / "jitter_position_summary.csv").open("w", newline="") as handle:
        fieldnames = list(position_rows[0].keys()) if position_rows else []
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(position_rows)

    raw_hashes = [sha256_hex(value) for value in raw_windows]
    jitter_hashes = [sha256_hex(pack_u32(value)) for value in jitter_vectors]
    residual_hashes = [sha256_hex(pack_s32(value)) for value in residual_vectors]
    joint_hashes = [sha256_hex(value) for value in joint_windows]
    jitter_position_entropy = [
        float(row["delta_mcv_min_entropy"]) for row in position_rows
    ]
    residual_position_entropy = [
        float(row["residual_mcv_min_entropy"]) for row in position_rows
    ]

    summary = {
        "run_dir": str(run_dir),
        "out_dir": str(out_dir),
        "deterministic_stimulus": deterministic_stimulus(manifest),
        "manifest": manifest,
        "complete_trials": len(complete_rows),
        "wdev": {
            **binary_stats(raw_all),
            "unique_trial_hashes": len(set(raw_hashes)),
            "trial_hamming_percent": source_pair_hamming(
                raw_windows, args.max_pairs, args.seed
            ),
            "consecutive_trial_hamming_percent": consecutive_hamming(raw_windows),
            "consecutive_trial_byte_pearson": consecutive_byte_pearson(raw_windows),
            "stream": "wdev_all.bin",
        },
        "jitter_delta_u32le": {
            **binary_stats(jitter_delta_u32),
            "samples": len(jitter_delta_values),
            "unique_samples": len(set(jitter_delta_values)),
            "unique_trial_vectors": len(set(jitter_hashes)),
            "per_position_mcv_min_entropy": summarize(jitter_position_entropy),
            "per_position_mcv_min_entropy_sum_not_independence_credit": sum(
                jitter_position_entropy
            ),
            "stream": "jitter_delta_u32le_all.bin",
        },
        "jitter_residual_s32le": {
            **binary_stats(jitter_residual_s32),
            "samples": len(jitter_residual_values),
            "unique_samples": len(set(jitter_residual_values)),
            "unique_trial_vectors": len(set(residual_hashes)),
            "per_position_mcv_min_entropy": summarize(residual_position_entropy),
            "per_position_mcv_min_entropy_sum_not_independence_credit": sum(
                residual_position_entropy
            ),
            "stream": "jitter_residual_s32le_all.bin",
        },
        "jitter_delta_low8": {
            **binary_stats(jitter_delta_low8),
            "stream": "jitter_delta_low8_all.bin",
        },
        "jitter_residual_low8": {
            **binary_stats(jitter_residual_low8),
            "stream": "jitter_residual_low8_all.bin",
        },
        "joint_response": {
            **binary_stats(joint_all),
            "unique_trial_hashes": len(set(joint_hashes)),
            "trial_hamming_percent": source_pair_hamming(
                joint_windows, args.max_pairs, args.seed
            ),
            "stream": "response_joint_all.bin",
            "encoding": "per trial W_i bytes followed by J_i delta_us uint32le bytes",
        },
        "caution": (
            "Jitter timing is schedule-structured and adversary-influenceable; "
            "per-position MCV estimates should not be summed as entropy credit "
            "without an independence/dependence argument."
        ),
    }

    (out_dir / "source_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    with (out_dir / "source_summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "metric", "value"])
        for source in (
            "wdev",
            "jitter_delta_u32le",
            "jitter_residual_s32le",
            "jitter_delta_low8",
            "jitter_residual_low8",
            "joint_response",
        ):
            for key, value in summary[source].items():
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, sort_keys=True)
                writer.writerow([source, key, value])

    # Copy the run manifest and trial CSV for a self-contained paper/data bundle.
    for name in ("manifest.json", "aeb_trials.csv"):
        src = run_dir / name
        if src.exists():
            shutil.copy2(src, out_dir / name)

    print(out_dir / "source_summary.json")


if __name__ == "__main__":
    main()
