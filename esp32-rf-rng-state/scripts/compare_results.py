import argparse
import json
from pathlib import Path


RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Print a Markdown comparison table from randlab manifests."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=RESULTS_DIR,
        help=f"Directory containing <condition>_256m/manifest.json (default: {RESULTS_DIR})",
    )
    return parser.parse_args()


def load_manifest(path):
    with open(path) as f:
        return json.load(f)


def get_suite_result(manifest, suite_name):
    if not manifest:
        return None
    for result in manifest.get("results", []):
        if result["suite"] == suite_name:
            return result
    return None


def get_metric(suite_result, metric_name):
    if not suite_result:
        return "N/A"
    for metric in suite_result.get("metrics", []):
        if metric["name"] == metric_name:
            val = metric["value"]
            if isinstance(val, float):
                return f"{val:.6f}"
            return str(val)
    return "N/A"


def get_gmt_stats(suite_result):
    if not suite_result:
        return "N/A"
    gmt_tests = [
        "mono_bit_frequency",
        "frequency_within_block_m10000",
        "poker_m4",
        "poker_m8",
        "overlapping_m3_p1",
        "overlapping_m3_p2",
        "overlapping_m5_p1",
        "overlapping_m5_p2",
        "runs",
        "runs_distribution",
        "longest_run_ones_m10000",
        "longest_run_zeros_m10000",
        "binary_derivative_k3",
        "binary_derivative_k7",
        "autocorrelation_d1",
        "autocorrelation_d2",
        "autocorrelation_d8",
        "autocorrelation_d16",
        "matrix_rank",
        "cumulative_forward",
        "cumulative_backward",
        "approximate_entropy_m2",
        "approximate_entropy_m5",
        "linear_complexity_m500",
        "maurers_universal",
        "discrete_fourier_transform",
    ]
    passed_count = 0
    total_count = 0
    for metric in suite_result.get("metrics", []):
        if metric["name"] in gmt_tests:
            total_count += 1
            if metric.get("passed") is True:
                passed_count += 1
    return f"{passed_count} / {total_count}"


def get_status(suite_result):
    if not suite_result:
        return "N/A"
    return suite_result.get("status", "N/A")


def main():
    args = parse_args()
    results_dir = args.results_dir
    conditions = ["rf_disabled", "wifi_idle", "wifi_scan", "udp_burst", "urandom"]
    labels = {
        "rf_disabled": "RF Disabled",
        "wifi_idle": "Wi-Fi Idle",
        "wifi_scan": "Wi-Fi Scan",
        "udp_burst": "UDP Burst",
        "urandom": "Linux /dev/urandom",
    }
    manifests = {}

    for cond in conditions:
        manifest_path = results_dir / f"{cond}_256m" / "manifest.json"
        if manifest_path.exists():
            manifests[cond] = load_manifest(manifest_path)
        else:
            manifests[cond] = None

    # Generate Markdown Table
    print("# ESP32 TRNG Statistical Analysis Comparison\n")
    print("| Test Suite / Metric | " + " | ".join(labels[c] for c in conditions) + " |")
    print("| :--- | " + " | ".join(":---:" for _ in conditions) + " |")

    def print_metric_row(title, results_by_condition, metric_name, suffix=""):
        values = [
            f"{get_metric(results_by_condition[c], metric_name)}{suffix}"
            for c in conditions
        ]
        print(f"| {title} | " + " | ".join(values) + " |")

    def print_value_row(title, values_by_condition):
        values = [str(values_by_condition[c]) for c in conditions]
        print(f"| {title} | " + " | ".join(values) + " |")

    # ENT
    ent_res = {c: get_suite_result(manifests[c], "ent") for c in manifests}
    print_metric_row("**ENT** Entropy (bits/byte)", ent_res, "entropy")
    print_metric_row(
        "**ENT** Chi-Square Exceed %", ent_res, "chi_square_exceed_percent", "%"
    )
    print_metric_row("**ENT** Mean Value", ent_res, "arithmetic_mean")
    print_metric_row("**ENT** Monte Carlo Pi", ent_res, "monte_carlo_pi")
    print_metric_row("**ENT** Serial Correlation", ent_res, "serial_correlation")

    # SP 800-90B
    iid_res = {c: get_suite_result(manifests[c], "entropy-iid") for c in manifests}
    non_iid_res = {
        c: get_suite_result(manifests[c], "entropy-non-iid") for c in manifests
    }
    restart_res = {
        c: get_suite_result(manifests[c], "entropy-restart") for c in manifests
    }
    print_metric_row(
        "**SP800-90B IID** Min-Entropy (bits/sample)",
        iid_res,
        "min(H_original, 8 X H_bitstring)",
    )
    print_metric_row(
        "**SP800-90B Non-IID** Min-Entropy (bits/sample)",
        non_iid_res,
        "min(H_original, 8 X H_bitstring)",
    )
    print_metric_row(
        "**SP800-90B Restart** Min-Entropy (bits/sample)",
        restart_res,
        "min(H_r, H_c, H_I)",
    )

    # Borel
    borel_res = {c: get_suite_result(manifests[c], "borel") for c in manifests}
    print_metric_row("**Borel** Normality Metric", borel_res, "borel_normality_metric")

    # AIS31
    p1t0_res = {c: get_suite_result(manifests[c], "ais31-p1-t0") for c in manifests}
    p1t15_res = {c: get_suite_result(manifests[c], "ais31-p1-t1-t5") for c in manifests}
    p2_res = {c: get_suite_result(manifests[c], "ais31-p2") for c in manifests}
    print_metric_row("**AIS31** P1-T0 Passed", p1t0_res, "T0")
    print_metric_row(
        "**AIS31** P1-T1-T5 Passed (Outcomes/5)",
        p1t15_res,
        "ais31_outcomes_passed",
    )
    print_metric_row(
        "**AIS31** P2 Passed (Outcomes/6)", p2_res, "ais31_outcomes_passed"
    )

    # GM/T
    gmt_res = {c: get_suite_result(manifests[c], "gmt-sts") for c in manifests}
    print_value_row(
        "**GM/T 0005-2021** Sub-tests Passed",
        {c: get_gmt_stats(gmt_res[c]) for c in conditions},
    )

    # Practrand
    pract_res = {c: get_suite_result(manifests[c], "practrand") for c in manifests}
    print_value_row(
        "**Practrand** Status", {c: get_status(pract_res[c]) for c in conditions}
    )

    # TestU01
    rabbit_res = {
        c: get_suite_result(manifests[c], "testu01-rabbit") for c in manifests
    }
    print_metric_row(
        "**TestU01 Rabbit** Suspect P-values", rabbit_res, "suspect_p_values"
    )


if __name__ == "__main__":
    main()
