#!/usr/bin/env python3
"""Parse ESP32 entropy-renewal serial logs.

The parser intentionally uses only the Python standard library so it can run in
the Zephyr virtual environment without extra dependencies.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import median, mean, pstdev


BASE_FIELDS = [
    "iter",
    "ret",
    "elapsed_us",
    "response_bytes",
    "accepted_net_bytes",
    "rng_block_calls",
    "rng_block_bytes",
    "rng_byte_calls",
    "rng_byte_bytes",
    "local_hw_bytes",
    "external_bytes",
    "pool_debit_bytes",
    "thread_get_bytes",
    "isr_get_bytes",
    "fast_refill_bytes",
    "pool_credit_bits_pre",
    "pool_credit_bits_post",
]

EXTRA_FIELDS_V2 = [
    "rng_block_cycles",
    "rng_byte_cycles",
    "rng_errors",
    "pool_timestamp_us_pre",
    "pool_timestamp_us_post",
]

DERIVED_FIELDS = [
    "rng_output_bytes",
    "supply_bytes",
    "pool_credit_delta_bits",
    "out_minus_supply_bytes",
    "iteration_active_s",
    "iteration_wall_s",
    "cumulative_wall_s",
]

FIELDS = BASE_FIELDS + EXTRA_FIELDS_V2
CSV_FIELDS = FIELDS + DERIVED_FIELDS


def parse_log(
    path: Path,
) -> tuple[dict[str, str], list[dict[str, int]], list[str], list[str]]:
    meta: dict[str, str] = {}
    rows: list[dict[str, int]] = []
    warnings: list[str] = []
    errors: list[str] = []

    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("[RENEWAL_META] "):
            payload = line[len("[RENEWAL_META] ") :]
            if "," in payload:
                key, value = payload.split(",", 1)
                meta[key] = value
        elif line.startswith("[RENEWAL_ITER] "):
            payload = line[len("[RENEWAL_ITER] ") :]
            parts = payload.split(",")
            if len(parts) == len(BASE_FIELDS):
                field_names = BASE_FIELDS
            elif len(parts) == len(FIELDS):
                field_names = FIELDS
            else:
                warnings.append(f"malformed_iter_row:{payload}")
                continue
            row = {key: int(value) for key, value in zip(field_names, parts)}
            for key in FIELDS:
                row.setdefault(key, 0)
            rows.append(row)
        elif line.startswith("[RENEWAL_WARN] "):
            warnings.append(line[len("[RENEWAL_WARN] ") :])
        elif line.startswith("[RENEWAL_ERR] "):
            errors.append(line[len("[RENEWAL_ERR] ") :])

    return meta, add_derived_fields(meta, rows), warnings, errors


def add_derived_fields(
    meta: dict[str, str], rows: list[dict[str, int]]
) -> list[dict[str, int | float]]:
    inter_ms = int(meta.get("inter_handshake_ms", "0"))
    cumulative_wall_s = 0.0
    enriched: list[dict[str, int | float]] = []

    for row in rows:
        item: dict[str, int | float] = dict(row)
        active_s = row["elapsed_us"] / 1_000_000
        wall_s = active_s + inter_ms / 1000
        cumulative_wall_s += wall_s

        item["rng_output_bytes"] = row["rng_block_bytes"] + row["rng_byte_bytes"]
        item["supply_bytes"] = row["local_hw_bytes"] + row["external_bytes"]
        item["pool_credit_delta_bits"] = (
            row["pool_credit_bits_post"] - row["pool_credit_bits_pre"]
        )
        item["out_minus_supply_bytes"] = row["pool_debit_bytes"] - item["supply_bytes"]
        item["iteration_active_s"] = active_s
        item["iteration_wall_s"] = wall_s
        item["cumulative_wall_s"] = cumulative_wall_s
        enriched.append(item)

    return enriched


def summarize(
    meta: dict[str, str],
    rows: list[dict[str, int | float]],
    warnings: list[str],
    errors: list[str],
) -> dict[str, object]:
    ok = [row for row in rows if row["ret"] == 0]
    failed = len(rows) - len(ok)
    inter_ms = int(meta.get("inter_handshake_ms", "0"))

    summary: dict[str, object] = {
        "iterations_logged": len(rows),
        "iterations_ok": len(ok),
        "iterations_failed": failed,
        "metadata": meta,
        "warnings": warnings,
        "errors": errors,
    }

    if not ok:
        return summary

    elapsed_s_active = sum(row["elapsed_us"] for row in ok) / 1_000_000
    elapsed_s_wall = elapsed_s_active + len(ok) * inter_ms / 1000
    rng_bytes = [int(row["rng_output_bytes"]) for row in ok]
    pool_debit = sum(row["pool_debit_bytes"] for row in ok)
    local_hw = sum(row["local_hw_bytes"] for row in ok)
    external = sum(row["external_bytes"] for row in ok)
    accepted_net = sum(row["accepted_net_bytes"] for row in ok)
    supply = local_hw + external
    final_balance_delta_bits = int(ok[-1]["pool_credit_bits_post"]) - int(
        ok[0]["pool_credit_bits_pre"]
    )

    summary.update(
        {
            "mu_rng_output_bytes_mean": mean(rng_bytes),
            "mu_rng_output_bytes_median": median(rng_bytes),
            "mu_rng_output_bytes_std": pstdev(rng_bytes) if len(rng_bytes) > 1 else 0.0,
            "rng_output_bytes_total": sum(rng_bytes),
            "pool_debit_bytes_total": pool_debit,
            "local_hw_bytes_total": local_hw,
            "external_bytes_total": external,
            "supply_bytes_total": supply,
            "accepted_network_bytes_total": accepted_net,
            "elapsed_s_active": elapsed_s_active,
            "elapsed_s_wall": elapsed_s_wall,
            "handshake_rate_active_hz": len(ok) / elapsed_s_active
            if elapsed_s_active
            else None,
            "handshake_rate_wall_hz": len(ok) / elapsed_s_wall
            if elapsed_s_wall
            else None,
            "lambda_out_active_Bps": pool_debit / elapsed_s_active
            if elapsed_s_active
            else None,
            "lambda_out_wall_Bps": pool_debit / elapsed_s_wall
            if elapsed_s_wall
            else None,
            "lambda_local_wall_Bps": local_hw / elapsed_s_wall
            if elapsed_s_wall
            else None,
            "lambda_net_wall_Bps": external / elapsed_s_wall
            if elapsed_s_wall
            else None,
            "lambda_supply_wall_Bps": supply / elapsed_s_wall
            if elapsed_s_wall
            else None,
            "renewal_margin_wall_Bps": (supply - pool_debit) / elapsed_s_wall
            if elapsed_s_wall
            else None,
            "renewal_supply_to_out_ratio": supply / pool_debit if pool_debit else None,
            "drbg_expansion_factor": (sum(rng_bytes) / pool_debit)
            if pool_debit
            else None,
            "pool_credit_bits_start": ok[0]["pool_credit_bits_pre"],
            "pool_credit_bits_end": ok[-1]["pool_credit_bits_post"],
            "pool_credit_delta_bits": final_balance_delta_bits,
            "rng_errors_total": sum(int(row["rng_errors"]) for row in ok),
        }
    )
    return summary


def write_csv(path: Path, rows: list[dict[str, int | float]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "log", type=Path, help="Serial log captured from west espressif monitor"
    )
    parser.add_argument("--csv", type=Path, help="Optional per-iteration CSV output")
    parser.add_argument("--summary", type=Path, help="Optional JSON summary output")
    args = parser.parse_args()

    meta, rows, warnings, errors = parse_log(args.log)
    summary = summarize(meta, rows, warnings, errors)

    if args.csv:
        write_csv(args.csv, rows)
    if args.summary:
        args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
