#!/usr/bin/env python3
"""Analyze ESP32 Wi-Fi-idle RNG slices captured across repeated resets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from statistics import mean

try:
    import numpy as np
except ImportError:  # pragma: no cover - slow fallback for minimal hosts
    np = None

try:
    from scipy.stats import chi2
except ImportError:  # pragma: no cover
    chi2 = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "results" / "wifi_idle_reboot"
DEFAULT_RANDLAB = PROJECT_ROOT / "../../randlab/.venv/bin/randlab"
DEFAULT_TOOLS_ROOT = PROJECT_ROOT / "../../randlab/.randlab/tools"
DEFAULT_SUITES = [
    "ent",
    "entropy-iid",
    "entropy-non-iid",
    "borel",
    "ais31-p1-t0",
    "ais31-p1-t1-t5",
    "ais31-p2",
    "gmt-sts",
    "practrand",
    "testu01-rabbit",
]


BIT_COUNTS = [bin(i).count("1") for i in range(256)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--window-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--prefix-bytes", type=int, default=4096)
    parser.add_argument("--run-randlab", action="store_true")
    parser.add_argument("--randlab", type=Path, default=DEFAULT_RANDLAB)
    parser.add_argument("--tools-root", type=Path, default=DEFAULT_TOOLS_ROOT)
    parser.add_argument("--randlab-profile", default="paper")
    parser.add_argument("--suite", action="append", dest="suites", default=[])
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chi_square_exceed_percent(value: float, df: int = 255) -> float:
    if chi2 is not None:
        return float(chi2.sf(value, df) * 100.0)

    # Wilson-Hilferty normal approximation; enough for trend screening.
    z = ((value / df) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * df))) / math.sqrt(
        2.0 / (9.0 * df)
    )
    return 50.0 * math.erfc(z / math.sqrt(2.0))


def metrics_from_counts(
    counts: list[int],
    n: int,
    pair_n: int,
    pair_sum_x: float,
    pair_sum_y: float,
    pair_sum_x2: float,
    pair_sum_y2: float,
    pair_sum_xy: float,
) -> dict[str, float | int]:
    if n <= 0:
        raise ValueError("empty input")

    probs = [count / n for count in counts if count]
    entropy = -sum(p * math.log2(p) for p in probs)
    expected = n / 256.0
    chi_square = sum(((count - expected) ** 2) / expected for count in counts)
    max_count = max(counts)
    mcv_min_entropy = -math.log2(max_count / n)
    byte_sum = sum(i * count for i, count in enumerate(counts))
    mean_byte = byte_sum / n
    ones = sum(BIT_COUNTS[i] * count for i, count in enumerate(counts))
    ones_ratio = ones / (n * 8)

    serial = 0.0
    if pair_n > 1:
        numerator = pair_n * pair_sum_xy - pair_sum_x * pair_sum_y
        denom_x = pair_n * pair_sum_x2 - pair_sum_x * pair_sum_x
        denom_y = pair_n * pair_sum_y2 - pair_sum_y * pair_sum_y
        denom = math.sqrt(max(denom_x, 0.0) * max(denom_y, 0.0))
        serial = numerator / denom if denom else 0.0

    return {
        "bytes": n,
        "entropy_bits_per_byte": entropy,
        "mcv_min_entropy_bits_per_byte": mcv_min_entropy,
        "chi_square": chi_square,
        "chi_square_exceed_percent": chi_square_exceed_percent(chi_square),
        "mean_byte": mean_byte,
        "ones_ratio": ones_ratio,
        "serial_correlation": serial,
        "most_common_byte_count": max_count,
    }


def update_stats_from_bytes(
    data: bytes,
    state: dict[str, object],
) -> None:
    if np is None:
        values = list(data)
        counts = state["counts"]
        assert isinstance(counts, list)
        for value in values:
            counts[value] += 1
        seq = values
    else:
        arr = np.frombuffer(data, dtype=np.uint8)
        counts_np = np.bincount(arr, minlength=256).astype(np.uint64)
        counts = state["counts"]
        assert isinstance(counts, list)
        for i, count in enumerate(counts_np.tolist()):
            counts[i] += int(count)
        seq = arr

    n = len(data)
    state["n"] = int(state["n"]) + n
    if n == 0:
        return

    prev = state.get("prev")
    if np is None:
        if prev is not None:
            add_pair(state, int(prev), seq[0])
        for x, y in zip(seq[:-1], seq[1:]):
            add_pair(state, int(x), int(y))
        state["prev"] = seq[-1]
    else:
        arr = seq
        if prev is not None:
            add_pair(state, int(prev), int(arr[0]))
        if arr.size > 1:
            xs = arr[:-1].astype(np.float64)
            ys = arr[1:].astype(np.float64)
            state["pair_n"] = int(state["pair_n"]) + int(xs.size)
            state["pair_sum_x"] = float(state["pair_sum_x"]) + float(xs.sum())
            state["pair_sum_y"] = float(state["pair_sum_y"]) + float(ys.sum())
            state["pair_sum_x2"] = float(state["pair_sum_x2"]) + float((xs * xs).sum())
            state["pair_sum_y2"] = float(state["pair_sum_y2"]) + float((ys * ys).sum())
            state["pair_sum_xy"] = float(state["pair_sum_xy"]) + float((xs * ys).sum())
        state["prev"] = int(arr[-1])


def add_pair(state: dict[str, object], x: int, y: int) -> None:
    state["pair_n"] = int(state["pair_n"]) + 1
    state["pair_sum_x"] = float(state["pair_sum_x"]) + x
    state["pair_sum_y"] = float(state["pair_sum_y"]) + y
    state["pair_sum_x2"] = float(state["pair_sum_x2"]) + x * x
    state["pair_sum_y2"] = float(state["pair_sum_y2"]) + y * y
    state["pair_sum_xy"] = float(state["pair_sum_xy"]) + x * y


def empty_state() -> dict[str, object]:
    return {
        "counts": [0] * 256,
        "n": 0,
        "prev": None,
        "pair_n": 0,
        "pair_sum_x": 0.0,
        "pair_sum_y": 0.0,
        "pair_sum_x2": 0.0,
        "pair_sum_y2": 0.0,
        "pair_sum_xy": 0.0,
    }


def finalize_state(state: dict[str, object]) -> dict[str, float | int]:
    return metrics_from_counts(
        state["counts"],  # type: ignore[arg-type]
        int(state["n"]),
        int(state["pair_n"]),
        float(state["pair_sum_x"]),
        float(state["pair_sum_y"]),
        float(state["pair_sum_x2"]),
        float(state["pair_sum_y2"]),
        float(state["pair_sum_xy"]),
    )


def metrics_for_file(path: Path) -> dict[str, float | int]:
    state = empty_state()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            update_stats_from_bytes(chunk, state)
    metrics = finalize_state(state)
    metrics["sha256"] = sha256_file(path)  # type: ignore[assignment]
    return metrics


def iter_windows(path: Path, window_bytes: int):
    with path.open("rb") as fh:
        index = 0
        offset = 0
        while True:
            data = fh.read(window_bytes)
            if not data:
                break
            state = empty_state()
            update_stats_from_bytes(data, state)
            metrics = finalize_state(state)
            yield index, offset, metrics
            index += 1
            offset += len(data)


def load_manifest(root: Path) -> dict[str, object]:
    path = root / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"missing manifest: {path}")
    return json.loads(path.read_text())


def run_paths(root: Path, manifest: dict[str, object]) -> list[Path]:
    paths = []
    for run in manifest.get("runs", []):
        if not isinstance(run, dict) or run.get("status") != "captured":
            continue
        paths.append(root / str(run["path"]))
    return paths


def aggregate_path(root: Path, manifest: dict[str, object]) -> Path:
    aggregate = manifest.get("aggregate", {})
    if not isinstance(aggregate, dict) or "path" not in aggregate:
        raise ValueError("manifest does not name aggregate path")
    return root / str(aggregate["path"])


def linear_slope(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    x_mean = mean(xs)
    y_mean = mean(ys)
    denom = sum((x - x_mean) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denom


def stat_range(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "min": None, "mean": None, "max": None}
    return {
        "n": len(values),
        "min": min(values),
        "mean": mean(values),
        "max": max(values),
    }


def duplicate_run_groups(values: list[str]) -> list[list[int]]:
    seen: dict[str, list[int]] = {}
    for index, value in enumerate(values, start=1):
        seen.setdefault(value, []).append(index)
    return [runs for runs in seen.values() if len(runs) > 1]


def prefix_sha256(path: Path, prefix_bytes: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        digest.update(fh.read(prefix_bytes))
    return digest.hexdigest()


def compare_aligned_files(
    path_a: Path, path_b: Path, chunk_bytes: int = 1024 * 1024
) -> dict[str, float | int]:
    compared_bytes = 0
    equal_bytes = 0
    differing_bits = 0

    if np is not None:
        bit_lookup = np.array(BIT_COUNTS, dtype=np.uint8)

    with path_a.open("rb") as a_fh, path_b.open("rb") as b_fh:
        while True:
            a_data = a_fh.read(chunk_bytes)
            b_data = b_fh.read(chunk_bytes)
            if not a_data or not b_data:
                break
            if len(a_data) != len(b_data):
                keep = min(len(a_data), len(b_data))
                a_data = a_data[:keep]
                b_data = b_data[:keep]

            compared_bytes += len(a_data)
            if np is None:
                for a_byte, b_byte in zip(a_data, b_data):
                    equal_bytes += int(a_byte == b_byte)
                    differing_bits += BIT_COUNTS[a_byte ^ b_byte]
            else:
                a_arr = np.frombuffer(a_data, dtype=np.uint8)
                b_arr = np.frombuffer(b_data, dtype=np.uint8)
                equal_bytes += int((a_arr == b_arr).sum())
                differing_bits += int(bit_lookup[np.bitwise_xor(a_arr, b_arr)].sum())

            if len(a_data) != len(b_data):
                break

    bit_difference_ratio = (
        differing_bits / (compared_bytes * 8) if compared_bytes else 0.0
    )
    byte_equal_ratio = equal_bytes / compared_bytes if compared_bytes else 0.0
    return {
        "compared_bytes": compared_bytes,
        "differing_bits": differing_bits,
        "bit_difference_ratio": bit_difference_ratio,
        "equal_bytes": equal_bytes,
        "byte_equal_ratio": byte_equal_ratio,
    }


def cross_reboot_similarity(
    paths: list[Path], run_rows: list[dict[str, object]], prefix_bytes: int
) -> dict[str, object]:
    sha_values = [str(row["sha256"]) for row in run_rows]
    prefix_values = [prefix_sha256(path, prefix_bytes) for path in paths]

    adjacent = [
        compare_aligned_files(left, right) for left, right in zip(paths[:-1], paths[1:])
    ]
    bit_ratios = [float(item["bit_difference_ratio"]) for item in adjacent]
    byte_equal_ratios = [float(item["byte_equal_ratio"]) for item in adjacent]
    first_last = compare_aligned_files(paths[0], paths[-1]) if len(paths) > 1 else None

    return {
        "runs": len(paths),
        "unique_sha256": len(set(sha_values)),
        "duplicate_sha256_runs": duplicate_run_groups(sha_values),
        "prefix_bytes": prefix_bytes,
        "unique_prefix_sha256": len(set(prefix_values)),
        "duplicate_prefix_runs": duplicate_run_groups(prefix_values),
        "adjacent_pairs": len(adjacent),
        "adjacent_bit_difference_ratio": stat_range(bit_ratios),
        "adjacent_byte_equal_ratio": stat_range(byte_equal_ratios),
        "first_last": first_last,
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_randlab(args: argparse.Namespace, input_path: Path, out_dir: Path) -> None:
    randlab = args.randlab.resolve()
    tools_root = args.tools_root.resolve()
    if not randlab.exists():
        raise FileNotFoundError(f"randlab executable not found: {randlab}")

    suites = args.suites or DEFAULT_SUITES
    cmd = [
        str(randlab),
        "run",
        "--tools-root",
        str(tools_root),
        "--input",
        str(input_path),
        "--format",
        "raw",
        "--profile",
        args.randlab_profile,
    ]
    for suite in suites:
        cmd.extend(["--suite", suite])
    cmd.extend(["--out", str(out_dir)])

    print("Running randlab aggregate analysis:")
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def summarize_randlab(out_dir: Path) -> dict[str, object] | None:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text())
    summary: dict[str, object] = {}
    for result in manifest.get("results", []):
        suite = result.get("suite")
        if not suite:
            continue
        metrics = {
            metric.get("name"): metric.get("value")
            for metric in result.get("metrics", [])
            if isinstance(metric, dict)
        }
        summary[suite] = {
            "status": result.get("status"),
            "failed_metrics": result.get("failed_metrics", []),
            "metrics": metrics,
        }
    return summary


def fmt(value: object, digits: int = 6) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def write_markdown(
    path: Path,
    manifest: dict[str, object],
    aggregate_metrics: dict[str, object],
    run_rows: list[dict[str, object]],
    window_rows: list[dict[str, object]],
    summary: dict[str, object],
    randlab_summary: dict[str, object] | None,
) -> None:
    run_count = len(run_rows)
    lines = [
        "# Wi-Fi Idle Reboot RNG Analysis",
        "",
        "## Dataset",
        "",
        f"- Runs: {run_count}",
        f"- Bytes per run: {manifest.get('bytes_per_run'):,}",
        f"- Aggregate bytes: {manifest.get('aggregate_bytes'):,}",
        f"- Aggregate SHA-256: `{aggregate_metrics.get('sha256')}`",
        "",
        "## Aggregate Raw Metrics",
        "",
        "| Metric | Value |",
        "| :--- | ---: |",
        f"| Entropy (bits/byte) | {fmt(aggregate_metrics['entropy_bits_per_byte'])} |",
        f"| MCV min-entropy (bits/byte) | {fmt(aggregate_metrics['mcv_min_entropy_bits_per_byte'])} |",
        f"| Chi-square exceed % | {fmt(aggregate_metrics['chi_square_exceed_percent'])}% |",
        f"| Mean byte | {fmt(aggregate_metrics['mean_byte'])} |",
        f"| Ones ratio | {fmt(aggregate_metrics['ones_ratio'])} |",
        f"| Serial correlation | {fmt(aggregate_metrics['serial_correlation'])} |",
        "",
        "## Per-Reboot Ranges",
        "",
        "| Metric | Min | Mean | Max | Slope per reboot |",
        "| :--- | ---: | ---: | ---: | ---: |",
    ]

    for metric, label in (
        ("entropy_bits_per_byte", "Entropy"),
        ("mcv_min_entropy_bits_per_byte", "MCV min-entropy"),
        ("chi_square_exceed_percent", "Chi-square exceed %"),
        ("mean_byte", "Mean byte"),
        ("ones_ratio", "Ones ratio"),
        ("serial_correlation", "Serial correlation"),
    ):
        item = summary["per_run"][metric]
        lines.append(
            f"| {label} | {fmt(item['min'])} | {fmt(item['mean'])} | "
            f"{fmt(item['max'])} | {fmt(summary['slopes'][metric], 9)} |"
        )

    lines.extend(
        [
            "",
            "## Early vs Late Reboots",
            "",
            "| Metric | First quartile mean | Last quartile mean | Difference |",
            "| :--- | ---: | ---: | ---: |",
        ]
    )
    for metric, label in (
        ("entropy_bits_per_byte", "Entropy"),
        ("mcv_min_entropy_bits_per_byte", "MCV min-entropy"),
        ("chi_square_exceed_percent", "Chi-square exceed %"),
        ("serial_correlation", "Serial correlation"),
    ):
        item = summary["first_last_quartile"][metric]
        lines.append(
            f"| {label} | {fmt(item['first_mean'])} | {fmt(item['last_mean'])} | "
            f"{fmt(item['difference'], 9)} |"
            )

    cross = summary.get("cross_reboot_similarity")
    if isinstance(cross, dict):
        bit_range = cross.get("adjacent_bit_difference_ratio", {})
        byte_range = cross.get("adjacent_byte_equal_ratio", {})
        first_last = cross.get("first_last", {})
        if not isinstance(bit_range, dict):
            bit_range = {}
        if not isinstance(byte_range, dict):
            byte_range = {}
        if not isinstance(first_last, dict):
            first_last = {}
        lines.extend(
            [
                "",
                "## Cross-Reboot Similarity",
                "",
                "| Check | Value |",
                "| :--- | ---: |",
                f"| Unique full-slice SHA-256 values | {cross.get('unique_sha256')} / {cross.get('runs')} |",
                f"| Unique first-{cross.get('prefix_bytes')}-byte prefixes | {cross.get('unique_prefix_sha256')} / {cross.get('runs')} |",
                (
                    "| Adjacent aligned bit-difference ratio, min/mean/max | "
                    f"{fmt(bit_range.get('min'))} / {fmt(bit_range.get('mean'))} / {fmt(bit_range.get('max'))} |"
                ),
                (
                    "| Adjacent aligned byte-equal ratio, min/mean/max | "
                    f"{fmt(byte_range.get('min'))} / {fmt(byte_range.get('mean'))} / {fmt(byte_range.get('max'))} |"
                ),
                (
                    "| First-vs-last aligned bit-difference ratio | "
                    f"{fmt(first_last.get('bit_difference_ratio'))} |"
                ),
                (
                    "| First-vs-last aligned byte-equal ratio | "
                    f"{fmt(first_last.get('byte_equal_ratio'))} |"
                ),
            ]
        )

    if window_rows:
        lines.extend(
            [
                "",
                "## Within-Reboot Windows",
                "",
                "Window metrics are computed on fixed-size chunks inside each reboot slice.",
                "The summary below compares the first and last window of each reboot.",
                "",
                "| Metric | First-window mean | Last-window mean | Difference |",
                "| :--- | ---: | ---: | ---: |",
            ]
        )
        for metric, label in (
            ("entropy_bits_per_byte", "Entropy"),
            ("mcv_min_entropy_bits_per_byte", "MCV min-entropy"),
            ("serial_correlation", "Serial correlation"),
        ):
            item = summary["first_last_window"][metric]
            lines.append(
                f"| {label} | {fmt(item['first_mean'])} | {fmt(item['last_mean'])} | "
                f"{fmt(item['difference'], 9)} |"
            )

    if randlab_summary:
        lines.extend(["", "## Randlab Aggregate", "", "| Suite | Status / Key Metric |", "| :--- | :--- |"])
        for suite in DEFAULT_SUITES:
            result = randlab_summary.get(suite)
            if not isinstance(result, dict):
                continue
            metrics = result.get("metrics", {})
            if not isinstance(metrics, dict):
                metrics = {}
            if suite == "ent":
                detail = (
                    f"{result.get('status')}; entropy "
                    f"{fmt(metrics.get('entropy'))}, serial "
                    f"{fmt(metrics.get('serial_correlation'))}"
                )
            elif suite in {"entropy-iid", "entropy-non-iid"}:
                detail = (
                    f"{result.get('status')}; min-entropy "
                    f"{fmt(metrics.get('min(H_original, 8 X H_bitstring)'))}"
                )
            elif suite == "testu01-rabbit":
                detail = (
                    f"{result.get('status')}; suspect p-values "
                    f"{fmt(metrics.get('suspect_p_values'), 0)}"
                )
            else:
                detail = str(result.get("status"))
            lines.append(f"| {suite} | {detail} |")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This analysis checks whether post-association Wi-Fi-idle WDEV output changes",
            "across repeated reset-triggered captures. It does not prove boot-time",
            "conditional min-entropy and it is not a cold-power SRAM-startup test.",
            "A reboot-state weakness should show up as exact slice/prefix repeats,",
            "unusually high aligned similarity across boots, a visible per-reboot",
            "trend, a first-versus-last reboot shift, or a concentration of",
            "statistical failures in later slices rather than only an aggregate",
            "battery warning.",
            "",
            "Generated files:",
            "",
            "- `run_metrics.csv`: one row per reboot slice",
            "- `window_metrics.csv`: one row per fixed-size window",
            "- `summary.json`: machine-readable summary",
        ]
    )

    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    root = args.dataset_root.resolve()
    out_dir = (args.out_dir or (root / "results")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(root)
    paths = run_paths(root, manifest)
    aggregate = aggregate_path(root, manifest)
    if not paths:
        raise SystemExit("manifest contains no captured runs")
    if not aggregate.exists():
        raise SystemExit(f"aggregate file missing: {aggregate}")

    print(f"Analyzing {len(paths)} reboot slices from {root}")
    aggregate_metrics = metrics_for_file(aggregate)

    run_rows: list[dict[str, object]] = []
    window_rows: list[dict[str, object]] = []
    for run_index, path in enumerate(paths, start=1):
        print(f"Per-run metrics {run_index}/{len(paths)}: {path.name}")
        metrics = metrics_for_file(path)
        row = {"run": run_index, "path": str(path.relative_to(root)), **metrics}
        run_rows.append(row)

        for window_index, offset, window_metrics in iter_windows(path, args.window_bytes):
            window_rows.append(
                {
                    "run": run_index,
                    "window": window_index,
                    "offset": offset,
                    "window_bytes": window_metrics["bytes"],
                    **window_metrics,
                }
            )

    xs = [float(row["run"]) for row in run_rows]
    per_run_summary = {}
    slopes = {}
    first_last = {}
    quartile = max(1, len(run_rows) // 4)
    for metric in (
        "entropy_bits_per_byte",
        "mcv_min_entropy_bits_per_byte",
        "chi_square_exceed_percent",
        "mean_byte",
        "ones_ratio",
        "serial_correlation",
    ):
        values = [float(row[metric]) for row in run_rows]
        per_run_summary[metric] = stat_range(values)
        slopes[metric] = linear_slope(xs, values)
        first_mean = mean(values[:quartile])
        last_mean = mean(values[-quartile:])
        first_last[metric] = {
            "first_mean": first_mean,
            "last_mean": last_mean,
            "difference": last_mean - first_mean,
            "quartile_n": quartile,
        }

    first_last_window = {}
    if window_rows:
        max_window_by_run: dict[int, int] = {}
        for row in window_rows:
            max_window_by_run[int(row["run"])] = max(
                max_window_by_run.get(int(row["run"]), -1), int(row["window"])
            )
        first_rows = [row for row in window_rows if int(row["window"]) == 0]
        last_rows = [
            row
            for row in window_rows
            if int(row["window"]) == max_window_by_run[int(row["run"])]
        ]
        for metric in (
            "entropy_bits_per_byte",
            "mcv_min_entropy_bits_per_byte",
            "serial_correlation",
        ):
            first_values = [float(row[metric]) for row in first_rows]
            last_values = [float(row[metric]) for row in last_rows]
            first_mean = mean(first_values)
            last_mean = mean(last_values)
            first_last_window[metric] = {
                "first_mean": first_mean,
                "last_mean": last_mean,
                "difference": last_mean - first_mean,
                "n": min(len(first_values), len(last_values)),
            }

    prefix_bytes = min(args.prefix_bytes, int(manifest.get("bytes_per_run", args.prefix_bytes)))
    cross_similarity = cross_reboot_similarity(paths, run_rows, prefix_bytes)

    randlab_dir = out_dir / "randlab_aggregate"
    if args.run_randlab:
        run_randlab(args, aggregate, randlab_dir)

    randlab_summary = summarize_randlab(randlab_dir)
    summary = {
        "dataset_root": str(root),
        "manifest": manifest,
        "aggregate_metrics": aggregate_metrics,
        "per_run": per_run_summary,
        "slopes": slopes,
        "first_last_quartile": first_last,
        "first_last_window": first_last_window,
        "cross_reboot_similarity": cross_similarity,
        "randlab": randlab_summary,
    }

    write_csv(out_dir / "run_metrics.csv", run_rows)
    write_csv(out_dir / "window_metrics.csv", window_rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_markdown(
        out_dir / "analysis.md",
        manifest,
        aggregate_metrics,
        run_rows,
        window_rows,
        summary,
        randlab_summary,
    )

    print("Wrote:")
    print(f"  {out_dir / 'run_metrics.csv'}")
    print(f"  {out_dir / 'window_metrics.csv'}")
    print(f"  {out_dir / 'summary.json'}")
    print(f"  {out_dir / 'analysis.md'}")
    if randlab_summary:
        print(f"  {randlab_dir}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    raise SystemExit(main())
