#!/usr/bin/env python3
"""Generate comparison artifacts for Ed25519 and PQ TEB boot profiles."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import parse_teb_boot_log as teb


os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")


PROFILE_LABELS = {
    "dev-ed25519-puf-beacon": "PUF + Ed25519 beacon",
    "pq-mlkem512-mldsa44": "ML-KEM-512 + ML-DSA-44",
}

C_ED25519 = "#9fcf69"
C_MLDSA = "#33acdc"
C_GREY = "#999999"


def stat(summary: dict[str, object], key: str) -> dict[str, object]:
    item = summary.get(key, {})
    return item if isinstance(item, dict) else {}


def value(summary: dict[str, object], key: str, name: str) -> object:
    return stat(summary, key).get(name)


def fmt(number: object, digits: int = 1) -> str:
    if number is None:
        return "N/A"
    try:
        value = float(number)
    except (TypeError, ValueError):
        return str(number)
    if math.isclose(value, round(value)):
        return f"{value:.0f}"
    return f"{value:.{digits}f}"


def median_range(
    summary: dict[str, object], key: str, unit: str = "", digits: int = 1
) -> str:
    item = stat(summary, key)
    if item.get("n", 0) == 0:
        return "N/A"
    text = f"{fmt(item.get('median'), digits)}"
    lo = fmt(item.get("min"), digits)
    hi = fmt(item.get("max"), digits)
    if lo != hi:
        text += f" [{lo}, {hi}]"
    if unit:
        text += f" {unit}"
    return text


def mean_sd(
    summary: dict[str, object], key: str, unit: str = "", digits: int = 1
) -> str:
    item = stat(summary, key)
    if item.get("n", 0) == 0:
        return "N/A"
    text = f"{fmt(item.get('mean'), digits)} $\\pm$ {fmt(item.get('std'), digits)}"
    if unit:
        text += f" {unit}"
    return text


def tex_escape(text: str) -> str:
    return text.replace("_", r"\_")


def capsule_len(summary: dict[str, object]) -> int:
    return (
        teb.PQ_CAPSULE_LEN
        if summary.get("profile") == "pq-mlkem512-mldsa44"
        else teb.ED25519_CAPSULE_LEN
    )


def build_profile(
    log: Path, server_log: Path | None
) -> tuple[list[dict[str, object]], dict[str, object]]:
    runs = teb.parse_log(log)
    summary = teb.summarize(runs, teb.parse_server_log(server_log))
    return runs, summary


def write_table(path: Path, ed: dict[str, object], pq: dict[str, object]) -> None:
    rows = [
        (
            "Seeded boots",
            f"{ed['runs_seeded']} / {ed['runs_attempted']}",
            f"{pq['runs_seeded']} / {pq['runs_attempted']}",
        ),
        (
            "Time to first credited seed, mean",
            mean_sd(ed, "time_to_seed_ms", "ms"),
            mean_sd(pq, "time_to_seed_ms", "ms"),
        ),
        (
            "Time to first credited seed, median",
            median_range(ed, "time_to_seed_ms", "ms"),
            median_range(pq, "time_to_seed_ms", "ms"),
        ),
        (
            "Signature verify",
            median_range(ed, "verify_us", r"\si{\micro\second}"),
            median_range(pq, "verify_us", r"\si{\micro\second}"),
        ),
        (
            "ML-KEM-512 decapsulation",
            "N/A",
            median_range(pq, "kem_decaps_us", r"\si{\micro\second}"),
        ),
        (
            "HKDF-SHA256",
            median_range(ed, "hkdf_us", r"\si{\micro\second}"),
            median_range(pq, "hkdf_us", r"\si{\micro\second}"),
        ),
        (
            "BOOT_HELLO / capsule",
            f"88 B / {capsule_len(ed)} B",
            f"88 B / {capsule_len(pq)} B",
        ),
        (
            "Server capsules",
            f"{ed['server']['server_capsules_served']} / {ed['runs_attempted']}",
            f"{pq['server']['server_capsules_served']} / {pq['runs_attempted']}",
        ),
        (
            "Heap peak after capsule",
            median_range(ed, "heap_peak_after_capsule", "B"),
            median_range(pq, "heap_peak_after_capsule", "B"),
        ),
        (
            "Credited entropy",
            median_range(ed, "credited_bits", "bits"),
            median_range(pq, "credited_bits", "bits"),
        ),
    ]
    body = "\n".join(
        f"{tex_escape(label)} & {ed_value} & {pq_value} \\\\"
        for label, ed_value, pq_value in rows
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\\begin{tabular}{p{0.32\\linewidth}p{0.27\\linewidth}p{0.27\\linewidth}}\n"
        "\\toprule\n"
        "Metric & PUF + Ed25519 beacon & ML-KEM-512 + ML-DSA-44 \\\\\n"
        "\\midrule\n"
        f"{body}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
    )


def write_summary(path: Path, ed: dict[str, object], pq: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "ed25519": ed,
                "pq": pq,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def plot(
    path: Path,
    ed_runs: list[dict[str, object]],
    pq_runs: list[dict[str, object]],
    ed: dict[str, object],
    pq: dict[str, object],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.lines import Line2D

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = [
        "Liberation Sans",
        "DejaVu Sans",
        "Ubuntu",
        "Arial",
    ]
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.labelsize": 10,
            "axes.labelweight": "bold",
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "figure.dpi": 150,
            "savefig.dpi": 300,
        }
    )

    profiles = [
        ("PUF + Ed25519\nbeacon", ed_runs, ed, C_ED25519, "s"),
        ("ML-KEM-512\n+ ML-DSA-44", pq_runs, pq, C_MLDSA, "^"),
    ]
    fig, ax = plt.subplots(figsize=(5.8, 3.4))
    ax.grid(True, linestyle="--", which="both", color="grey", alpha=0.4)
    ax.set_axisbelow(True)

    for idx, (_, runs, summary, color, marker) in enumerate(profiles):
        vals = np.array(
            [
                float(run["time_to_seed_ms"])
                for run in runs
                if run.get("result") == "seeded"
            ]
        )
        jitter = np.random.default_rng(42 + idx).uniform(-0.12, 0.12, size=len(vals))
        ax.scatter(
            np.full(len(vals), idx) + jitter,
            vals,
            c=[color],
            marker=marker,
            s=34,
            alpha=0.65,
            edgecolors="#222222",
            linewidths=0.5,
            zorder=3,
        )
        ax.errorbar(
            idx,
            float(value(summary, "time_to_seed_ms", "mean")),
            yerr=float(value(summary, "time_to_seed_ms", "std") or 0.0),
            fmt="o",
            markersize=4.5,
            color="#111111",
            markerfacecolor="#111111",
            markeredgecolor="#111111",
            capsize=5,
            capthick=1.2,
            elinewidth=1.2,
            zorder=5,
        )

    ax.set_xticks([0, 1])
    ax.set_xticklabels([profiles[0][0], profiles[1][0]])
    ax.set_ylabel("Time to first credited seed (ms)")
    ax.set_xlabel("Capsule profile")
    ax.set_xlim(-0.45, 1.45)
    ax.margins(y=0.14)
    ax.legend(
        handles=[
            Line2D(
                [0],
                [0],
                marker="s",
                color="w",
                markerfacecolor=C_ED25519,
                markeredgecolor="#222222",
                markersize=7,
                label="Ed25519 boot",
            ),
            Line2D(
                [0],
                [0],
                marker="^",
                color="w",
                markerfacecolor=C_MLDSA,
                markeredgecolor="#222222",
                markersize=7,
                label="PQ boot",
            ),
            Line2D(
                [0],
                [0],
                marker="o",
                color="#111111",
                markerfacecolor="#111111",
                markersize=5,
                label="Mean +/- SD",
            ),
        ],
        frameon=False,
        loc="upper left",
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ed-log", type=Path, required=True)
    parser.add_argument("--ed-server-log", type=Path)
    parser.add_argument("--pq-log", type=Path, required=True)
    parser.add_argument("--pq-server-log", type=Path)
    parser.add_argument("--table-tex", type=Path, required=True)
    parser.add_argument("--figure", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    ed_runs, ed_summary = build_profile(args.ed_log, args.ed_server_log)
    pq_runs, pq_summary = build_profile(args.pq_log, args.pq_server_log)
    write_table(args.table_tex, ed_summary, pq_summary)
    write_summary(args.summary, ed_summary, pq_summary)
    plot(args.figure, ed_runs, pq_runs, ed_summary, pq_summary)
    print(
        json.dumps({"ed25519": ed_summary, "pq": pq_summary}, indent=2, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
