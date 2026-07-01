#!/usr/bin/env python3
"""Plot RF-actuated boot entropy collector results."""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
from collections import Counter
from pathlib import Path
from typing import Iterable

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

C_RF = "#33acdc"
C_ENTROPY = "#9fcf69"
C_GREY = "#999999"
C_DARK = "#111111"
C_MAGENTA = "#aa3377"

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Liberation Sans", "DejaVu Sans", "Ubuntu", "Arial"]
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["ps.fonttype"] = 42
plt.rcParams.update(
    {
        "font.size": 8,
        "axes.titlesize": 9,
        "axes.titleweight": "bold",
        "axes.labelsize": 9,
        "axes.labelweight": "bold",
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "figure.dpi": 150,
        "savefig.dpi": 300,
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a compact summary plot for an AEB collector run"
    )
    parser.add_argument("run_dir", type=Path, help="Collector run directory")
    parser.add_argument("--out", type=Path, help="Output image path")
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=2000,
        help="Maximum raw-file pairs for Hamming distances",
    )
    parser.add_argument(
        "--seed", type=int, default=1, help="Sampling seed for pair subsampling"
    )
    parser.add_argument("--title", default="", help="Figure title")
    return parser.parse_args()


def load_rows(run_dir: Path) -> list[dict[str, str]]:
    csv_path = run_dir / "aeb_trials.csv"
    with csv_path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def resolve_path(run_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.exists():
        return path

    parts = path.parts
    for marker in ("raw", "jitter", "stimulus"):
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
    candidate = run_dir / "raw" / path.name
    if candidate.exists():
        return candidate
    return path


def hamming_bits(a: bytes, b: bytes) -> int:
    return sum((x ^ y).bit_count() for x, y in zip(a, b))


def byte_shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def byte_mcv_min_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    most_common = max(Counter(data).values())
    return -math.log2(most_common / len(data))


def byte_frequency_deviation(data: bytes) -> list[float]:
    if not data:
        return [0.0] * 256
    counts = Counter(data)
    expected = len(data) / 256
    return [100 * (counts.get(value, 0) - expected) / expected for value in range(256)]


def pair_indices(n: int, max_pairs: int, seed: int) -> Iterable[tuple[int, int]]:
    all_pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    if len(all_pairs) <= max_pairs:
        return all_pairs
    rng = random.Random(seed)
    return rng.sample(all_pairs, max_pairs)


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    rows = load_rows(run_dir)
    complete_rows = [r for r in rows if r.get("complete") == "True"]

    trials = [int(r["capture_id"]) for r in complete_rows]
    raw_bytes = [int(r["raw_bytes"]) for r in complete_rows]
    ones_ratio = [int(r["ones"]) / (int(r["raw_bytes"]) * 8) for r in complete_rows]
    raw_hashes = [r["raw_sha256_file"] for r in complete_rows]
    raw_paths = [resolve_path(run_dir, r["raw_file"]) for r in complete_rows]
    raw_data = [p.read_bytes() for p in raw_paths]
    raw_stream = b"".join(raw_data)
    frequency_deviation = byte_frequency_deviation(raw_stream)
    shannon_entropy = [byte_shannon_entropy(data) for data in raw_data]
    mcv_min_entropy = [byte_mcv_min_entropy(data) for data in raw_data]
    distances = []
    for i, j in pair_indices(len(raw_data), args.max_pairs, args.seed):
        denom = min(len(raw_data[i]), len(raw_data[j])) * 8
        if denom:
            distances.append(100 * hamming_bits(raw_data[i], raw_data[j]) / denom)

    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.0))
    layout_top = 0.98
    if args.title:
        fig.suptitle(args.title, fontsize=10, fontweight="bold")
        layout_top = 0.95

    ax = axes[0][0]
    ax.plot(
        trials,
        ones_ratio,
        marker="o",
        linewidth=1.1,
        markersize=2.8,
        color=C_RF,
        markeredgecolor="#222222",
        markeredgewidth=0.25,
    )
    ax.axhline(0.5, color=C_DARK, linestyle="--", linewidth=0.9)
    ax.set_title("Raw WDEV Bit Balance")
    ax.set_xlabel("Trial")
    ax.set_ylabel("Ones ratio")
    ax.set_ylim(0.485, 0.515)
    ax.grid(True, linestyle="--", color=C_GREY, alpha=0.35)
    ax.set_axisbelow(True)

    ax = axes[0][1]
    ax.hist(distances, bins=18, color=C_RF, edgecolor="white", linewidth=0.5)
    ax.axvline(50, color=C_DARK, linestyle="--", linewidth=0.9)
    ax.set_title("Pairwise Raw-Window Hamming Distance")
    ax.set_xlabel("Differing bits (%)")
    ax.set_ylabel("Trial pairs")
    ax.grid(True, axis="y", linestyle="--", color=C_GREY, alpha=0.35)
    ax.set_axisbelow(True)

    ax = axes[1][0]
    byte_values = list(range(256))
    ax.bar(byte_values, frequency_deviation, color=C_MAGENTA, width=1.0)
    ax.axhline(0.0, color=C_DARK, linestyle="--", linewidth=0.9)
    ax.set_title("Aggregate Raw Byte Frequency")
    ax.set_xlabel("Byte value")
    ax.set_ylabel("Deviation from uniform (%)")
    ax.set_xlim(-1, 256)
    ax.grid(True, axis="y", linestyle="--", color=C_GREY, alpha=0.35)
    ax.set_axisbelow(True)

    ax = axes[1][1]
    ax.plot(
        trials,
        shannon_entropy,
        marker="o",
        linewidth=1.1,
        markersize=4,
        color=C_RF,
        markeredgecolor="#222222",
        markeredgewidth=0.25,
        label="Shannon entropy",
    )
    ax.plot(
        trials,
        mcv_min_entropy,
        marker="s",
        linewidth=1.0,
        markersize=2.7,
        color=C_ENTROPY,
        markeredgecolor="#222222",
        markeredgewidth=0.25,
        label="MCV min-entropy",
    )
    ax.axhline(8.0, color=C_DARK, linestyle="--", linewidth=0.9)
    ax.set_title("Per-Trial Raw Byte Statistics")
    ax.set_xlabel("Trial")
    ax.set_ylabel("Bits per byte")
    floor = min(mcv_min_entropy + shannon_entropy + [7.0])
    ax.set_ylim(max(0, floor - 0.1), 8.05)
    ax.legend(frameon=False)
    ax.grid(True, linestyle="--", color=C_GREY, alpha=0.35)
    ax.set_axisbelow(True)

    subtitle = (
        f"{len(complete_rows)} complete trials, "
        f"{sum(raw_bytes):,} pre-hash WDEV bytes, "
        f"{len(distances)} Hamming pairs, "
        f"{len(set(raw_hashes))}/{len(complete_rows)} unique raw hashes"
    )
    fig.text(0.5, 0.018, subtitle, ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, layout_top))

    out = args.out
    if out is None:
        out = run_dir / "aeb_summary_plot.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight")
    print(out)


if __name__ == "__main__":
    main()
