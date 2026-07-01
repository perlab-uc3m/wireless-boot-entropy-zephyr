#!/usr/bin/env python3
"""Plot ESP32 entropy-renewal benchmark results.

The script expects parser outputs produced by parse_renewal_log.py:
one *_summary.json file and, when available, a matching per-iteration CSV.
It generates two high-density figures:

  1. Supply stack with lambda_out markers.
  2. Credited pool-balance traces.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Liberation Sans", "DejaVu Sans", "Ubuntu", "Arial"]
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["ps.fonttype"] = 42

C_LOCAL = "#9fcf69"
C_NET = "#33acdc"
C_OUT = "#222222"
C_FAIL = "#c44e52"
C_GREY = "#bbbbbb"

RC = {
    "font.size": 9,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.labelsize": 11,
    "axes.labelweight": "bold",
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "figure.dpi": 150,
    "savefig.dpi": 300,
}
plt.rcParams.update(RC)


def style_ax(ax):
    ax.grid(True, linestyle="--", which="both", color="grey", alpha=0.4)
    ax.set_axisbelow(True)


def discover_summary_paths(inputs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for item in inputs:
        if item.is_dir():
            paths.extend(sorted(item.glob("*_summary.json")))
            paths.extend(sorted(item.glob("*.summary.json")))
        elif item.is_file():
            paths.append(item)
    seen = set()
    unique = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(path)
            seen.add(resolved)
    return unique


def matching_csv(summary_path: Path) -> Path | None:
    candidates = []
    name = summary_path.name
    if name.endswith("_summary.json"):
        candidates.append(
            summary_path.with_name(name[: -len("_summary.json")] + ".csv")
        )
    if name.endswith(".summary.json"):
        candidates.append(
            summary_path.with_name(name[: -len(".summary.json")] + ".csv")
        )
    candidates.append(summary_path.with_suffix(".csv"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def run_label(summary_path: Path, summary: dict) -> str:
    meta = summary.get("metadata", {})
    kex = meta.get("key_exchange", "unknown")
    sig = meta.get("signature", "unknown")
    net = meta.get("network_injection", "disabled")
    local = meta.get("local_hw_refill_after_bootstrap", "enabled")

    if local == "disabled":
        mode = "remote-assisted"
    elif net == "enabled":
        mode = "local+remote"
    else:
        mode = "local-only"

    short_kex = kex.replace("ML-KEM-", "K").replace("ECDHE-", "")
    short_sig = sig.replace("ML-DSA-", "D").replace("ECDSA-", "")
    return f"{short_kex}+{short_sig}\n{mode}"


def load_runs(summary_paths: list[Path]) -> list[dict]:
    runs = []
    for summary_path in summary_paths:
        summary = json.loads(summary_path.read_text())
        csv_path = matching_csv(summary_path)
        runs.append(
            {
                "summary_path": summary_path,
                "csv_path": csv_path,
                "summary": summary,
                "label": run_label(summary_path, summary),
            }
        )
    return runs


def value(summary: dict, key: str) -> float:
    raw = summary.get(key)
    return float(raw) if raw is not None else 0.0


def plot_rates(runs: list[dict], output: Path) -> None:
    labels = [run["label"] for run in runs]
    local = np.array([value(run["summary"], "lambda_local_wall_Bps") for run in runs])
    net = np.array([value(run["summary"], "lambda_net_wall_Bps") for run in runs])
    out = np.array([value(run["summary"], "lambda_out_wall_Bps") for run in runs])
    mu = np.array([value(run["summary"], "mu_rng_output_bytes_mean") for run in runs])
    ratio = np.array(
        [value(run["summary"], "renewal_supply_to_out_ratio") for run in runs]
    )

    x = np.arange(len(runs), dtype=float)
    fig, ax1 = plt.subplots(figsize=(max(6.4, 1.3 * len(runs)), 4.6))
    style_ax(ax1)

    ax1.bar(
        x,
        local,
        width=0.62,
        color=C_LOCAL,
        edgecolor="black",
        linewidth=0.5,
        label="local supply",
    )
    ax1.bar(
        x,
        net,
        width=0.62,
        bottom=local,
        color=C_NET,
        edgecolor="black",
        linewidth=0.5,
        label="remote supply",
    )
    ax1.scatter(x, out, marker="D", s=46, color=C_OUT, label="pool debit", zorder=5)

    for i, (supply, out_i, ratio_i) in enumerate(zip(local + net, out, ratio)):
        color = C_OUT if supply >= out_i else C_FAIL
        text = "inf" if out_i == 0 else f"{ratio_i:.2f}x"
        ax1.text(
            i,
            max(supply, out_i) * 1.04 + 0.2,
            text,
            ha="center",
            va="bottom",
            fontsize=8,
            color=color,
        )

    ax1.set_ylabel("Wall-clock rate (B/s)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels)
    ax1.set_title("Renewal Supply vs Pool Debit")

    ax2 = ax1.twinx()
    ax2.plot(x, mu, color=C_GREY, marker="o", linestyle=":", linewidth=1.2, label="mu")
    ax2.set_ylabel("RNG-output demand, mu (B/handshake)")
    ax2.grid(False)

    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(
        handles1 + handles2,
        labels1 + labels2,
        loc="upper center",
        ncol=4,
        bbox_to_anchor=(0.5, -0.18),
    )

    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {output}")


def load_trace(csv_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = []
    y = []
    ret = []
    with csv_path.open() as fh:
        for row in csv.DictReader(fh):
            if row.get("cumulative_wall_s"):
                x.append(float(row["cumulative_wall_s"]))
            else:
                x.append(float(row["iter"]))
            y.append(float(row["pool_credit_bits_post"]) / 8.0)
            ret.append(float(row["ret"]))
    return np.array(x), np.array(y), np.array(ret)


def plot_pool_traces(runs: list[dict], output: Path) -> None:
    trace_runs = [run for run in runs if run["csv_path"]]
    if not trace_runs:
        print("Skipping pool trace: no matching CSV files")
        return

    fig, ax = plt.subplots(figsize=(7.2, 4.5))
    style_ax(ax)
    colors = [C_LOCAL, C_NET, "#777777", "#7aa6c2", "#b4d889", "#444444"]

    for idx, run in enumerate(trace_runs):
        x, y, ret = load_trace(run["csv_path"])
        color = colors[idx % len(colors)]
        ax.plot(
            x,
            y,
            marker="o",
            markersize=2.8,
            linewidth=1.2,
            color=color,
            label=run["label"].replace("\n", " / "),
        )
        failed = ret != 0
        if np.any(failed):
            ax.scatter(x[failed], y[failed], marker="x", s=38, color=C_FAIL, zorder=6)

    ax.set_xlabel("Cumulative wall time (s)")
    ax.set_ylabel("Credited pool balance (B)")
    ax.set_title("Credited Pool Balance During Fresh Sessions")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2)

    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {output}")


def write_table(runs: list[dict], output: Path) -> None:
    fields = [
        "label",
        "iterations_ok",
        "iterations_failed",
        "mu_rng_output_bytes_mean",
        "lambda_out_wall_Bps",
        "lambda_local_wall_Bps",
        "lambda_net_wall_Bps",
        "lambda_supply_wall_Bps",
        "renewal_margin_wall_Bps",
        "renewal_supply_to_out_ratio",
        "drbg_expansion_factor",
        "pool_credit_delta_bits",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            summary = run["summary"]
            row = {field: summary.get(field, "") for field in fields}
            row["label"] = run["label"].replace("\n", " / ")
            writer.writerow(row)
    print(f"Saved: {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", nargs="+", type=Path, help="Summary JSON files or directories"
    )
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("figures"))
    args = parser.parse_args()

    summary_paths = discover_summary_paths(args.inputs)
    if not summary_paths:
        raise SystemExit("No summary JSON files found")

    runs = load_runs(summary_paths)
    plot_rates(runs, args.output_dir / "fig_renewal_rates.pdf")
    plot_pool_traces(runs, args.output_dir / "fig_renewal_pool_trace.pdf")
    write_table(runs, args.output_dir / "renewal_summary_table.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
