#!/usr/bin/env python3
"""Parse RF-actuated boot entropy serial logs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def parse_kv_line(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in line.strip().split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        fields[key] = value.rstrip(",")
    return fields


def hamming_hex(a: str, b: str) -> int:
    aa = bytes.fromhex(a)
    bb = bytes.fromhex(b)
    if len(aa) != len(bb):
        return 0
    return sum((x ^ y).bit_count() for x, y in zip(aa, bb))


def hamming_bytes(a: bytes, b: bytes) -> int:
    if len(a) != len(b):
        return 0
    return sum((x ^ y).bit_count() for x, y in zip(a, b))


def pairwise(values: list, distance_fn) -> list[int]:
    out: list[int] = []
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            out.append(distance_fn(values[i], values[j]))
    return out


def numeric(rows: list[dict[str, str]], key: str) -> list[int]:
    vals = []
    for row in rows:
        try:
            vals.append(int(row[key]))
        except (KeyError, ValueError):
            pass
    return vals


def summarize_dist(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "mean": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def byte_min_entropy(raw_windows: list[bytes]) -> dict[str, float | int | None]:
    if not raw_windows:
        return {"bytes": 0, "min_entropy_bits_per_byte": None}
    joined = b"".join(raw_windows)
    if not joined:
        return {"bytes": 0, "min_entropy_bits_per_byte": None}
    counts = Counter(joined)
    pmax = max(counts.values()) / len(joined)
    return {
        "bytes": len(joined),
        "max_count": max(counts.values()),
        "min_entropy_bits_per_byte": -math.log2(pmax),
    }


def parse_log(path: Path) -> tuple[list[dict[str, str]], dict[int, bytes]]:
    rows: list[dict[str, str]] = []
    raw_chunks: dict[int, dict[int, str]] = defaultdict(dict)

    with path.open(errors="replace") as f:
        for line in f:
            if "[AEB_RESULT]" in line:
                payload = line.split("[AEB_RESULT]", 1)[1]
                rows.append(parse_kv_line(payload))
            elif "[AEB_RAW_HEX_CHUNK]" in line:
                payload = line.split("[AEB_RAW_HEX_CHUNK]", 1)[1]
                fields = parse_kv_line(payload)
                try:
                    trial = int(fields["trial"])
                    offset = int(fields["offset"])
                    raw_chunks[trial][offset] = fields["hex"]
                except (KeyError, ValueError):
                    continue

    raw_windows: dict[int, bytes] = {}
    for trial, chunks in raw_chunks.items():
        hex_parts = [chunks[k] for k in sorted(chunks)]
        try:
            raw_windows[trial] = bytes.fromhex("".join(hex_parts))
        except ValueError:
            continue

    return rows, raw_windows


def build_summary(rows: list[dict[str, str]], raw_windows: dict[int, bytes]) -> dict:
    response_hashes = [r["response_sha256"] for r in rows if "response_sha256" in r]
    seed_hashes = [r["seed_sha256"] for r in rows if "seed_sha256" in r]
    raw_hashes = [r["raw_sha256"] for r in rows if "raw_sha256" in r]
    packets_expected = numeric(rows, "packets_expected")
    packets_seen = numeric(rows, "packets_seen")
    sample_bytes = numeric(rows, "sample_bytes")
    ones = numeric(rows, "ones")
    sample_us = numeric(rows, "sample_us")
    jitter_mean = numeric(rows, "jitter_mean_us")

    raw_values = [
        raw_windows[int(r["trial"])]
        for r in rows
        if r.get("trial", "").isdigit() and int(r["trial"]) in raw_windows
    ]
    raw_hamming = pairwise(raw_values, hamming_bytes)

    bit_totals = [b * 8 for b in sample_bytes]
    ones_ratio = []
    for o, total in zip(ones, bit_totals):
        if total:
            ones_ratio.append(o / total)

    packet_loss = []
    for exp, seen in zip(packets_expected, packets_seen):
        packet_loss.append(max(exp - seen, 0))

    return {
        "trials": len(rows),
        "conditions": sorted(set(r.get("condition", "unknown") for r in rows)),
        "unique_response_hashes": len(set(response_hashes)),
        "unique_raw_hashes": len(set(raw_hashes)),
        "unique_seed_hashes": len(set(seed_hashes)),
        "response_digest_hamming": summarize_dist(
            pairwise(response_hashes, hamming_hex)
        ),
        "seed_digest_hamming": summarize_dist(pairwise(seed_hashes, hamming_hex)),
        "raw_window_hamming": summarize_dist(raw_hamming),
        "sample_us": summarize_dist(sample_us),
        "jitter_mean_us": summarize_dist(jitter_mean),
        "packet_loss": summarize_dist(packet_loss),
        "ones_ratio": {
            "count": len(ones_ratio),
            "min": min(ones_ratio) if ones_ratio else None,
            "mean": statistics.fmean(ones_ratio) if ones_ratio else None,
            "max": max(ones_ratio) if ones_ratio else None,
        },
        "raw_byte_min_entropy": byte_min_entropy(raw_values),
        "raw_windows_present": len(raw_values),
        "notes": [
            "Digest Hamming distances show diversity of commitments, not an entropy proof.",
            "Use fixed-nonce trials to avoid mistaking public nonce changes for entropy.",
            "Raw-window statistics are only available when firmware is built with --dump-raw.",
        ],
    }


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    if not rows:
        path.write_text("")
        return
    keys = sorted(set().union(*(row.keys() for row in rows)))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--csv", type=Path, default=Path("results/aeb_trials.csv"))
    parser.add_argument(
        "--summary", type=Path, default=Path("results/aeb_summary.json")
    )
    args = parser.parse_args()

    rows, raw_windows = parse_log(args.log)
    summary = build_summary(rows, raw_windows)

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    write_csv(rows, args.csv)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
