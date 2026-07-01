import json
from pathlib import Path


RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


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


def main():
    results_dir = RESULTS_DIR
    conditions = ["rf_disabled", "wifi_idle", "wifi_scan", "urandom"]
    manifests = {}

    for cond in conditions:
        manifest_path = results_dir / f"{cond}_256m" / "manifest.json"
        if manifest_path.exists():
            manifests[cond] = load_manifest(manifest_path)
        else:
            manifests[cond] = None

    # Generate Markdown Table
    print("# ESP32 TRNG Statistical Analysis Comparison\n")
    print(
        "| Test Suite / Metric | RF Disabled | Wi-Fi Idle | Wi-Fi Scan | Linux /dev/urandom |"
    )
    print("| :--- | :---: | :---: | :---: | :---: |")

    # ENT
    ent_res = {c: get_suite_result(manifests[c], "ent") for c in manifests}
    print(
        f"| **ENT** Entropy (bits/byte) | {get_metric(ent_res['rf_disabled'], 'entropy')} | {get_metric(ent_res['wifi_idle'], 'entropy')} | {get_metric(ent_res['wifi_scan'], 'entropy')} | {get_metric(ent_res['urandom'], 'entropy')} |"
    )
    print(
        f"| **ENT** Chi-Square Exceed % | {get_metric(ent_res['rf_disabled'], 'chi_square_exceed_percent')}% | {get_metric(ent_res['wifi_idle'], 'chi_square_exceed_percent')}% | {get_metric(ent_res['wifi_scan'], 'chi_square_exceed_percent')}% | {get_metric(ent_res['urandom'], 'chi_square_exceed_percent')}% |"
    )
    print(
        f"| **ENT** Mean Value | {get_metric(ent_res['rf_disabled'], 'arithmetic_mean')} | {get_metric(ent_res['wifi_idle'], 'arithmetic_mean')} | {get_metric(ent_res['wifi_scan'], 'arithmetic_mean')} | {get_metric(ent_res['urandom'], 'arithmetic_mean')} |"
    )
    print(
        f"| **ENT** Monte Carlo Pi | {get_metric(ent_res['rf_disabled'], 'monte_carlo_pi')} | {get_metric(ent_res['wifi_idle'], 'monte_carlo_pi')} | {get_metric(ent_res['wifi_scan'], 'monte_carlo_pi')} | {get_metric(ent_res['urandom'], 'monte_carlo_pi')} |"
    )
    print(
        f"| **ENT** Serial Correlation | {get_metric(ent_res['rf_disabled'], 'serial_correlation')} | {get_metric(ent_res['wifi_idle'], 'serial_correlation')} | {get_metric(ent_res['wifi_scan'], 'serial_correlation')} | {get_metric(ent_res['urandom'], 'serial_correlation')} |"
    )

    # SP 800-90B
    iid_res = {c: get_suite_result(manifests[c], "entropy-iid") for c in manifests}
    non_iid_res = {
        c: get_suite_result(manifests[c], "entropy-non-iid") for c in manifests
    }
    restart_res = {
        c: get_suite_result(manifests[c], "entropy-restart") for c in manifests
    }
    print(
        f"| **SP800-90B IID** Min-Entropy (bits/sample) | {get_metric(iid_res['rf_disabled'], 'min(H_original, 8 X H_bitstring)')} | {get_metric(iid_res['wifi_idle'], 'min(H_original, 8 X H_bitstring)')} | {get_metric(iid_res['wifi_scan'], 'min(H_original, 8 X H_bitstring)')} | {get_metric(iid_res['urandom'], 'min(H_original, 8 X H_bitstring)')} |"
    )
    print(
        f"| **SP800-90B Non-IID** Min-Entropy (bits/sample) | {get_metric(non_iid_res['rf_disabled'], 'min(H_original, 8 X H_bitstring)')} | {get_metric(non_iid_res['wifi_idle'], 'min(H_original, 8 X H_bitstring)')} | {get_metric(non_iid_res['wifi_scan'], 'min(H_original, 8 X H_bitstring)')} | {get_metric(non_iid_res['urandom'], 'min(H_original, 8 X H_bitstring)')} |"
    )
    print(
        f"| **SP800-90B Restart** Min-Entropy (bits/sample) | {get_metric(restart_res['rf_disabled'], 'min(H_r, H_c, H_I)')} | {get_metric(restart_res['wifi_idle'], 'min(H_r, H_c, H_I)')} | {get_metric(restart_res['wifi_scan'], 'min(H_r, H_c, H_I)')} | {get_metric(restart_res['urandom'], 'min(H_r, H_c, H_I)')} |"
    )

    # Borel
    borel_res = {c: get_suite_result(manifests[c], "borel") for c in manifests}
    print(
        f"| **Borel** Normality Metric | {get_metric(borel_res['rf_disabled'], 'borel_normality_metric')} | {get_metric(borel_res['wifi_idle'], 'borel_normality_metric')} | {get_metric(borel_res['wifi_scan'], 'borel_normality_metric')} | {get_metric(borel_res['urandom'], 'borel_normality_metric')} |"
    )

    # AIS31
    p1t0_res = {c: get_suite_result(manifests[c], "ais31-p1-t0") for c in manifests}
    p1t15_res = {c: get_suite_result(manifests[c], "ais31-p1-t1-t5") for c in manifests}
    p2_res = {c: get_suite_result(manifests[c], "ais31-p2") for c in manifests}
    print(
        f"| **AIS31** P1-T0 Passed | {get_metric(p1t0_res['rf_disabled'], 'T0')} | {get_metric(p1t0_res['wifi_idle'], 'T0')} | {get_metric(p1t0_res['wifi_scan'], 'T0')} | {get_metric(p1t0_res['urandom'], 'T0')} |"
    )
    print(
        f"| **AIS31** P1-T1-T5 Passed (Outcomes/5) | {get_metric(p1t15_res['rf_disabled'], 'ais31_outcomes_passed')} | {get_metric(p1t15_res['wifi_idle'], 'ais31_outcomes_passed')} | {get_metric(p1t15_res['wifi_scan'], 'ais31_outcomes_passed')} | {get_metric(p1t15_res['urandom'], 'ais31_outcomes_passed')} |"
    )
    print(
        f"| **AIS31** P2 Passed (Outcomes/6) | {get_metric(p2_res['rf_disabled'], 'ais31_outcomes_passed')} | {get_metric(p2_res['wifi_idle'], 'ais31_outcomes_passed')} | {get_metric(p2_res['wifi_scan'], 'ais31_outcomes_passed')} | {get_metric(p2_res['urandom'], 'ais31_outcomes_passed')} |"
    )

    # GM/T
    gmt_res = {c: get_suite_result(manifests[c], "gmt-sts") for c in manifests}
    print(
        f"| **GM/T 0005-2021** Sub-tests Passed | {get_gmt_stats(gmt_res['rf_disabled'])} | {get_gmt_stats(gmt_res['wifi_idle'])} | {get_gmt_stats(gmt_res['wifi_scan'])} | {get_gmt_stats(gmt_res['urandom'])} |"
    )

    # Practrand
    pract_res = {c: get_suite_result(manifests[c], "practrand") for c in manifests}
    print(
        f"| **Practrand** Status | {pract_res['rf_disabled']['status'] if pract_res['rf_disabled'] else 'N/A'} | {pract_res['wifi_idle']['status'] if pract_res['wifi_idle'] else 'N/A'} | {pract_res['wifi_scan']['status'] if pract_res['wifi_scan'] else 'N/A'} | {pract_res['urandom']['status'] if pract_res['urandom'] else 'N/A'} |"
    )

    # TestU01
    rabbit_res = {
        c: get_suite_result(manifests[c], "testu01-rabbit") for c in manifests
    }
    print(
        f"| **TestU01 Rabbit** Suspect P-values | {get_metric(rabbit_res['rf_disabled'], 'suspect_p_values')} | {get_metric(rabbit_res['wifi_idle'], 'suspect_p_values')} | {get_metric(rabbit_res['wifi_scan'], 'suspect_p_values')} | {get_metric(rabbit_res['urandom'], 'suspect_p_values')} |"
    )


if __name__ == "__main__":
    main()
