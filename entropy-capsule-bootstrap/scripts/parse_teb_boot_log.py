#!/usr/bin/env python3
"""Parse asymmetric entropy capsule serial logs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from pathlib import Path
from statistics import mean, median, pstdev


os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")


META_RE = re.compile(r"^\[TEB_META\]\s+([^,]+),(.+)$")
METRIC_RE = re.compile(r"^\[TEB_METRIC\]\s+([^,]+),(.+)$")
POOL_RE = re.compile(r"^\[TEB_POOL\]\s+([^,]+),(.+)$")
PUF_RE = re.compile(r"^\[TEB_PUF\]\s+([^,]+),(.+)$")
ERR_RE = re.compile(r"^\[TEB_ERR\]\s+(.+)$")
RESULT_RE = re.compile(r"^\[TEB_RESULT\]\s+(.+)$")
IPV4_RE = re.compile(r"IPv4 ready: addr=([0-9.]+) gw=([0-9.]+)")
SERVER_RE = re.compile(r"^\[TEB_SERVER\]\s+served,.*seq=([0-9]+)")
PROFILE_RE = re.compile(r"^Profile:\s+(.+)$")

ED25519_CAPSULE_LEN = 160
PQ_CAPSULE_LEN = 3252


CSV_FIELDS = [
    "run",
    "result",
    "error",
    "profile",
    "device_id",
    "boot_counter",
    "ipv4_addr",
    "ipv4_gateway",
    "local_hw_refill",
    "time_to_seed_ms",
    "capsule_exchange_ms",
    "capsule_wait_ms",
    "hello_send_us",
    "verify_us",
    "kem_decaps_us",
    "hkdf_us",
    "hello_tx_bytes",
    "capsule_rx_bytes",
    "wireless_packets_min",
    "credited_bits",
    "external_bytes",
    "hw_bytes",
    "heap_free_before_wifi",
    "heap_used_before_wifi",
    "heap_peak_before_wifi",
    "heap_free_before_capsule",
    "heap_used_before_capsule",
    "heap_peak_before_capsule",
    "heap_free_after_capsule",
    "heap_used_after_capsule",
    "heap_peak_after_capsule",
    "sram_ones",
    "sram_one_rate",
    "sram_transitions",
    "sram_transition_rate",
    "timing_min_delta",
    "timing_max_delta",
    "timing_mean_delta",
    "gateway_time_ms",
    "gateway_sequence",
]


def parse_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value), 0)
    except ValueError:
        return None


def finalize_run(run: dict[str, object]) -> dict[str, object]:
    if not run:
        return run

    profile = str(run.get("profile", ""))
    run.setdefault("hello_tx_bytes", 88)
    run.setdefault(
        "capsule_rx_bytes",
        PQ_CAPSULE_LEN if profile == "pq-mlkem512-mldsa44" else ED25519_CAPSULE_LEN,
    )
    run.setdefault("wireless_packets_min", 2)
    if "result" not in run:
        run["result"] = "incomplete"

    sram_ones = parse_int(run.get("sram_ones"))
    if sram_ones is not None:
        run["sram_one_rate"] = sram_ones / (4096 * 8)

    transitions = parse_int(run.get("sram_transitions"))
    if transitions is not None:
        run["sram_transition_rate"] = transitions / (4096 * 8 - 1)

    timing_sum = parse_int(run.get("timing_sum_delta"))
    if timing_sum is not None:
        run["timing_mean_delta"] = timing_sum / 512

    return run


def parse_log(path: Path) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    current: dict[str, object] = {}

    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if line == "Asymmetric Entropy Capsule Bootstrap":
            if current:
                runs.append(finalize_run(current))
            current = {"run": len(runs) + 1}
            continue

        if not current:
            continue

        for regex, target in (
            (META_RE, current),
            (METRIC_RE, current),
            (POOL_RE, current),
            (PUF_RE, current),
        ):
            match = regex.match(line)
            if match:
                key, value = match.groups()
                target[key] = value
                break
        else:
            match = RESULT_RE.match(line)
            if match:
                current["result"] = match.group(1)
                runs.append(finalize_run(current))
                current = {}
                continue

            match = ERR_RE.match(line)
            if match:
                current["error"] = match.group(1)
                current["result"] = "failed"
                continue

            match = IPV4_RE.search(line)
            if match:
                current["ipv4_addr"] = match.group(1)
                current["ipv4_gateway"] = match.group(2)
                continue

            match = PROFILE_RE.match(line)
            if match:
                current["profile"] = match.group(1)

    if current:
        runs.append(finalize_run(current))

    return runs


def parse_server_log(path: Path | None) -> dict[str, object]:
    if path is None or not path.is_file():
        return {}

    served = 0
    sequences: list[int] = []
    for raw in path.read_text(errors="replace").splitlines():
        match = SERVER_RE.match(raw.strip())
        if match:
            served += 1
            sequences.append(int(match.group(1)))

    return {
        "server_capsules_served": served,
        "server_sequences": sequences,
    }


def numeric_values(runs: list[dict[str, object]], key: str) -> list[float]:
    values = []
    for run in runs:
        if run.get("result") != "seeded":
            continue
        raw = run.get(key)
        if raw in (None, ""):
            continue
        try:
            values.append(float(raw))
        except ValueError:
            continue
    return values


def stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
            "std": None,
        }
    return {
        "n": len(values),
        "mean": mean(values),
        "median": median(values),
        "min": min(values),
        "max": max(values),
        "std": pstdev(values) if len(values) > 1 else 0.0,
    }


def summarize(
    runs: list[dict[str, object]], server: dict[str, object]
) -> dict[str, object]:
    ok = [run for run in runs if run.get("result") == "seeded"]
    summary: dict[str, object] = {
        "runs_attempted": len(runs),
        "runs_seeded": len(ok),
        "runs_failed": len(runs) - len(ok),
        "profile": ok[0].get("profile", runs[0].get("profile", "")) if runs else "",
        "server": server,
    }
    for key in (
        "time_to_seed_ms",
        "capsule_exchange_ms",
        "capsule_wait_ms",
        "hello_send_us",
        "verify_us",
        "kem_decaps_us",
        "hkdf_us",
        "credited_bits",
        "external_bytes",
        "hw_bytes",
        "heap_peak_after_capsule",
        "heap_used_after_capsule",
        "sram_one_rate",
        "sram_transition_rate",
        "timing_mean_delta",
    ):
        summary[key] = stats(numeric_values(runs, key))
    return summary


def write_csv(path: Path, runs: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for run in runs:
            row = {field: run.get(field, "") for field in CSV_FIELDS}
            writer.writerow(row)


def fmt(value: object, digits: int = 1) -> str:
    if value is None:
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isclose(number, round(number)):
        return f"{number:.0f}"
    return f"{number:.{digits}f}"


def stat_text(
    summary: dict[str, object], key: str, unit: str = "", digits: int = 1
) -> str:
    item = summary[key]
    if not isinstance(item, dict) or item.get("n", 0) == 0:
        return "N/A"
    med = fmt(item.get("median"), digits)
    lo = fmt(item.get("min"), digits)
    hi = fmt(item.get("max"), digits)
    suffix = f" {unit}" if unit else ""
    if lo == hi:
        return f"{med}{suffix}"
    return f"{med}{suffix} [{lo}, {hi}]"


def tex_label(text: str) -> str:
    return text.replace("_", r"\_")


def write_table_tex(path: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_pq = summary.get("profile") == "pq-mlkem512-mldsa44"
    capsule_len = PQ_CAPSULE_LEN if is_pq else ED25519_CAPSULE_LEN
    rows = [
        ("Seeded boots", f"{summary['runs_seeded']} / {summary['runs_attempted']}"),
        ("Profile", str(summary.get("profile", "N/A"))),
        ("Time to first credited seed", stat_text(summary, "time_to_seed_ms", "ms")),
        (
            "BOOT_HELLO-to-capsule exchange",
            stat_text(summary, "capsule_exchange_ms", "ms"),
        ),
        ("Capsule receive wait", stat_text(summary, "capsule_wait_ms", "ms")),
        ("BOOT_HELLO send call", stat_text(summary, "hello_send_us", r"\si{\micro\second}")),
        ("Signature verify", stat_text(summary, "verify_us", r"\si{\micro\second}")),
        (
            "ML-KEM-512 decapsulation",
            stat_text(summary, "kem_decaps_us", r"\si{\micro\second}")
            if is_pq
            else "N/A",
        ),
        ("HKDF-SHA256", stat_text(summary, "hkdf_us", r"\si{\micro\second}")),
        ("BOOT_HELLO / capsule", f"88 B / {capsule_len} B"),
        ("Minimum wireless packets", "2 UDP datagrams"),
        ("Credited entropy", stat_text(summary, "credited_bits", "bits")),
        ("External pool input", stat_text(summary, "external_bytes", "B")),
        ("Local hardware bytes after gate", stat_text(summary, "hw_bytes", "B")),
        ("Heap peak after capsule", stat_text(summary, "heap_peak_after_capsule", "B")),
    ]
    body = "\n".join(f"{tex_label(label)} & {value} \\\\" for label, value in rows)
    path.write_text(
        "\\begin{tabular}{ll}\n"
        "\\toprule\n"
        "Metric & Result \\\\\n"
        "\\midrule\n"
        f"{body}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
    )


def plot_boot(
    summary: dict[str, object], runs: list[dict[str, object]], path: Path
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Liberation Sans", "DejaVu Sans", "Ubuntu", "Arial"],
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.labelweight": "bold",
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 7,
            "figure.dpi": 150,
            "savefig.dpi": 300,
        }
    )

    ok = [run for run in runs if run.get("result") == "seeded"]
    x = np.arange(1, len(ok) + 1)
    latency = np.array([float(run["time_to_seed_ms"]) for run in ok])

    c_mldsa = "#33acdc"
    c_ecdsa = "#9fcf69"
    color = c_mldsa if summary.get("profile") == "pq-mlkem512-mldsa44" else c_ecdsa

    fig, ax = plt.subplots(figsize=(6.0, 3.2))
    ax.grid(True, linestyle="--", which="both", color="grey", alpha=0.4)
    ax.set_axisbelow(True)
    if len(latency):
        ax.scatter(
            x,
            latency,
            s=42,
            marker="^",
            color=color,
            edgecolor="#222222",
            linewidth=0.5,
            label="Boot run",
            zorder=3,
        )
        item = summary["time_to_seed_ms"]
        mean_value = float(item["mean"])
        std_value = float(item["std"] or 0.0)
        mean_x = len(ok) + 1.15
        ax.errorbar(
            [mean_x],
            [mean_value],
            yerr=[std_value],
            fmt="o",
            markersize=5.5,
            color="#222222",
            ecolor=color,
            elinewidth=1.5,
            capsize=5,
            label="Mean +/- SD",
            zorder=4,
        )
        ax.axhline(
            mean_value, color="#999999", linewidth=0.9, linestyle="--", alpha=0.8
        )
        ax.set_xlim(0.35, mean_x + 0.6)
        ax.set_xticks([*x, mean_x])
        ax.set_xticklabels([*(str(int(i)) for i in x), "Mean\n+/- SD"])
    ax.set_xlabel("Automated reset-mode boot")
    ax.set_ylabel("Time to first credited seed (ms)")
    ax.legend(frameon=False, loc="best")

    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="Serial log captured from ESP32")
    parser.add_argument("--server-log", type=Path, help="Optional beacon-server log")
    parser.add_argument("--csv", type=Path, help="Per-run CSV output")
    parser.add_argument("--summary", type=Path, help="JSON summary output")
    parser.add_argument("--table-tex", type=Path, help="LaTeX tabular output")
    parser.add_argument("--figure", type=Path, help="PDF/PNG latency figure output")
    args = parser.parse_args()

    runs = parse_log(args.log)
    server = parse_server_log(args.server_log)
    summary = summarize(runs, server)

    if args.csv:
        write_csv(args.csv, runs)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if args.table_tex:
        write_table_tex(args.table_tex, summary)
    if args.figure:
        plot_boot(summary, runs, args.figure)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
