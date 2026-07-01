#!/usr/bin/env python3
"""Analyze raw SRAM-PUF dumps printed by entropy-capsule-bootstrap."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from pathlib import Path


os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

RAW_RE = re.compile(r"^\[TEB_PUF_RAW\]\s+([0-9a-fA-F]+)\s*$")


def bits_of(sample: bytes) -> list[int]:
    bits: list[int] = []
    for b in sample:
        for i in range(8):
            bits.append((b >> (7 - i)) & 1)
    return bits


def hamming(a: bytes, b: bytes) -> int:
    return sum((x ^ y).bit_count() for x, y in zip(a, b))


def extract_samples(path: Path) -> list[bytes]:
    samples: list[bytes] = []
    for line in path.read_text(errors="replace").splitlines():
        match = RAW_RE.match(line.strip())
        if match:
            samples.append(bytes.fromhex(match.group(1)))
    return samples


def pairwise_distances(samples: list[bytes]) -> list[int]:
    distances = []
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            distances.append(hamming(samples[i], samples[j]))
    return distances


def summarize(samples: list[bytes]) -> dict[str, object]:
    if not samples:
        raise SystemExit("no [TEB_PUF_RAW] lines found")
    sizes = {len(s) for s in samples}
    if len(sizes) != 1:
        raise SystemExit(f"inconsistent sample sizes: {sorted(sizes)}")

    n = len(samples)
    bit_len = len(samples[0]) * 8
    bit_samples = [bits_of(s) for s in samples]
    ones = [sum(row[i] for row in bit_samples) for i in range(bit_len)]
    probs = [c / n for c in ones]
    per_bit_hmin = [
        -math.log2(max(p, 1.0 - p)) if 0.0 < p < 1.0 else 0.0 for p in probs
    ]
    stable_bits = sum(1 for p in probs if p in (0.0, 1.0))
    near_stable_bits = sum(1 for p in probs if p <= 0.05 or p >= 0.95)

    distances = pairwise_distances(samples)
    bit_len = len(samples[0]) * 8
    distance_rates = [d / bit_len for d in distances]

    return {
        "samples": n,
        "bytes_per_sample": len(samples[0]),
        "bits_per_sample": bit_len,
        "global_one_rate": sum(sum(row) for row in bit_samples) / (n * bit_len),
        "mean_per_bit_min_entropy": sum(per_bit_hmin) / bit_len,
        "sum_per_bit_min_entropy": sum(per_bit_hmin),
        "stable_bits": stable_bits,
        "stable_bit_rate": stable_bits / bit_len,
        "near_stable_bits_5pct": near_stable_bits,
        "near_stable_bit_rate_5pct": near_stable_bits / bit_len,
        "pairwise_hamming_mean": (
            sum(distances) / len(distances) if distances else 0.0
        ),
        "pairwise_hamming_min": min(distances) if distances else 0,
        "pairwise_hamming_max": max(distances) if distances else 0,
        "pairwise_hamming_rate_mean": (
            sum(distance_rates) / len(distance_rates) if distance_rates else 0.0
        ),
        "pairwise_hamming_rate_min": min(distance_rates) if distance_rates else 0.0,
        "pairwise_hamming_rate_max": max(distance_rates) if distance_rates else 0.0,
    }


def write_summary_csv(path: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["metric", "value"])
        for key in sorted(summary):
            writer.writerow([key, summary[key]])


def write_distances_csv(path: Path, samples: list[bytes]) -> None:
    bit_len = len(samples[0]) * 8 if samples else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sample_i", "sample_j", "hamming_bits", "hamming_rate"])
        for i in range(len(samples)):
            for j in range(i + 1, len(samples)):
                d = hamming(samples[i], samples[j])
                writer.writerow([i + 1, j + 1, d, d / bit_len if bit_len else 0.0])


def fmt_pct(value: object) -> str:
    return f"{float(value) * 100:.2f}\\%"


def fmt_num(value: object, digits: int = 2) -> str:
    number = float(value)
    if math.isclose(number, round(number)):
        return f"{number:.0f}"
    return f"{number:.{digits}f}"


def write_table_tex(path: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("Samples", fmt_num(summary["samples"], 0)),
        ("Bytes per sample", fmt_num(summary["bytes_per_sample"], 0)),
        ("Global one-rate", fmt_pct(summary["global_one_rate"])),
        (
            "Pairwise Hamming distance",
            (
                f"{fmt_pct(summary['pairwise_hamming_rate_mean'])} "
                f"[{fmt_pct(summary['pairwise_hamming_rate_min'])}, "
                f"{fmt_pct(summary['pairwise_hamming_rate_max'])}]"
            ),
        ),
        (
            "Stable bits",
            f"{fmt_num(summary['stable_bits'], 0)} ({fmt_pct(summary['stable_bit_rate'])})",
        ),
        (
            "Near-stable bits, 5\\%",
            f"{fmt_num(summary['near_stable_bits_5pct'], 0)} "
            f"({fmt_pct(summary['near_stable_bit_rate_5pct'])})",
        ),
        (
            "Sum per-bit min-entropy",
            f"{fmt_num(summary['sum_per_bit_min_entropy'], 1)} bits",
        ),
    ]
    body = "\n".join(f"{label} & {value} \\\\" for label, value in rows)
    path.write_text(
        "\\begin{tabular}{ll}\n"
        "\\toprule\n"
        "Metric & Result \\\\\n"
        "\\midrule\n"
        f"{body}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
    )


def plot_distances(path: Path, samples: list[bytes]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bit_len = len(samples[0]) * 8
    rates = [d / bit_len * 100 for d in pairwise_distances(samples)]

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Liberation Sans", "DejaVu Sans", "Arial"],
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "font.size": 9,
            "axes.labelsize": 10,
            "axes.titlesize": 11,
        }
    )

    fig, ax = plt.subplots(figsize=(5.4, 3.2))
    ax.grid(True, axis="y", linestyle="--", alpha=0.35)
    if rates:
        bins = min(20, max(5, int(math.sqrt(len(rates)))))
        ax.hist(rates, bins=bins, color="#2f6f9f", edgecolor="#222222", linewidth=0.5)
    else:
        ax.text(0.5, 0.5, "Need at least two captures", ha="center", va="center")
    ax.set_xlabel("Pairwise Hamming distance (%)")
    ax.set_ylabel("Pairs")
    ax.set_title("SRAM-PUF Boot-to-Boot Variation")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--distances-csv", type=Path)
    parser.add_argument("--raw-bin", type=Path)
    parser.add_argument("--table-tex", type=Path)
    parser.add_argument("--figure", type=Path)
    args = parser.parse_args()

    samples = extract_samples(args.log)
    summary = summarize(samples)

    print(json.dumps(summary, indent=2, sort_keys=True))

    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True))
    if args.csv:
        write_summary_csv(args.csv, summary)
    if args.distances_csv:
        write_distances_csv(args.distances_csv, samples)

    if args.raw_bin:
        args.raw_bin.parent.mkdir(parents=True, exist_ok=True)
        args.raw_bin.write_bytes(b"".join(samples))
    if args.table_tex:
        write_table_tex(args.table_tex, summary)
    if args.figure:
        plot_distances(args.figure, samples)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
