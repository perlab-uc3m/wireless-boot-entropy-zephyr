import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


def main():
    results_dir = RESULTS_DIR
    conditions = ["rf_disabled", "wifi_idle", "wifi_scan", "urandom"]
    labels = [
        "RF Disabled\n(Baseline)",
        "Wi-Fi Idle\n(Connected)",
        "Wi-Fi Scan\n(Scanning)",
        "Linux\n/dev/urandom",
    ]

    # Extract data
    data = {c: {} for c in conditions}
    for cond in conditions:
        manifest_path = results_dir / f"{cond}_256m" / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)
        for result in manifest["results"]:
            suite = result["suite"]
            if suite == "ent":
                for m in result.get("metrics", []):
                    data[cond][m["name"]] = m["value"]
            elif suite == "entropy-iid":
                for m in result.get("metrics", []):
                    if m["name"] == "min(H_original, 8 X H_bitstring)":
                        data[cond]["iid_entropy"] = m["value"]
            elif suite == "entropy-non-iid":
                for m in result.get("metrics", []):
                    if m["name"] == "min(H_original, 8 X H_bitstring)":
                        data[cond]["non_iid_entropy"] = m["value"]
            elif suite == "borel":
                for m in result.get("metrics", []):
                    if m["name"] == "borel_normality_metric":
                        data[cond]["borel"] = m["value"]
            elif suite == "testu01-rabbit":
                for m in result.get("metrics", []):
                    if m["name"] == "suspect_p_values":
                        data[cond]["rabbit"] = m["value"]

    # Modern style setup
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]
    plt.rcParams["axes.edgecolor"] = "#bdc3c7"
    plt.rcParams["axes.linewidth"] = 0.8

    fig, axs = plt.subplots(2, 3, figsize=(15, 10))
    fig.suptitle(
        "ESP32 TRNG vs Linux /dev/urandom Statistical Comparison",
        fontsize=16,
        fontweight="bold",
        color="#2c3e50",
        y=0.98,
    )

    # Colors
    c_blue = "#2980b9"
    c_teal = "#16a085"
    c_slate = "#34495e"
    c_purple = "#8e44ad"
    c_orange = "#d35400"
    c_red = "#e74c3c"
    c_light_gray = "#ecf0f1"

    x = np.arange(len(conditions))
    width = 0.35

    # 1. NIST SP800-90B Entropy
    iid_vals = [data[c]["iid_entropy"] for c in conditions]
    non_iid_vals = [data[c]["non_iid_entropy"] for c in conditions]
    axs[0, 0].bar(x - width / 2, iid_vals, width, label="IID Estimate", color=c_slate)
    axs[0, 0].bar(
        x + width / 2, non_iid_vals, width, label="Non-IID Estimate", color=c_teal
    )
    axs[0, 0].set_title(
        "NIST SP 800-90B Min-Entropy (bits/sample)",
        fontsize=11,
        fontweight="semibold",
        color="#34495e",
    )
    axs[0, 0].set_ylabel("Entropy Value", fontsize=9)
    axs[0, 0].set_xticks(x)
    axs[0, 0].set_xticklabels(labels, fontsize=8)
    axs[0, 0].set_ylim(0, 8.5)
    axs[0, 0].axhline(
        y=8.0, color="r", linestyle="--", linewidth=0.8, label="Ideal Max (8.0)"
    )
    axs[0, 0].legend(
        loc="lower right", frameon=True, facecolor=c_light_gray, fontsize=8
    )
    axs[0, 0].grid(axis="y", linestyle=":", alpha=0.6)
    for i in range(len(conditions)):
        axs[0, 0].text(
            i - width / 2,
            iid_vals[i] + 0.15,
            f"{iid_vals[i]:.2f}",
            ha="center",
            fontsize=8,
            fontweight="semibold",
        )
        axs[0, 0].text(
            i + width / 2,
            non_iid_vals[i] + 0.15,
            f"{non_iid_vals[i]:.2f}",
            ha="center",
            fontsize=8,
            fontweight="semibold",
        )

    # 2. Chi-Square Exceed %
    chi_vals = [data[c]["chi_square_exceed_percent"] for c in conditions]
    axs[0, 1].bar(x, chi_vals, width * 1.5, color=c_blue)
    axs[0, 1].set_title(
        "Fourmilab ENT Chi-Square Exceed % (Ideal: ~50%)",
        fontsize=11,
        fontweight="semibold",
        color="#34495e",
    )
    axs[0, 1].set_ylabel("Exceed Probability (%)", fontsize=9)
    axs[0, 1].set_xticks(x)
    axs[0, 1].set_xticklabels(labels, fontsize=8)
    axs[0, 1].set_ylim(0, 100)
    axs[0, 1].axhspan(
        10, 90, color="#2ecc71", alpha=0.1, label="Nominal Range (10% - 90%)"
    )
    axs[0, 1].axhline(
        y=50, color="#e67e22", linestyle="--", linewidth=1.0, label="Ideal Target (50%)"
    )
    axs[0, 1].axhline(
        y=95, color="r", linestyle=":", linewidth=0.8, label="Suspect Threshold (95%)"
    )
    axs[0, 1].legend(loc="lower left", frameon=True, facecolor=c_light_gray, fontsize=8)
    axs[0, 1].grid(axis="y", linestyle=":", alpha=0.6)
    for i, val in enumerate(chi_vals):
        axs[0, 1].text(
            i, val + 2, f"{val:.1f}%", ha="center", fontsize=8, fontweight="semibold"
        )

    # 3. Borel Normality Metric
    borel_vals = [data[c]["borel"] for c in conditions]
    axs[0, 2].bar(x, borel_vals, width * 1.5, color=c_purple)
    axs[0, 2].set_title(
        "Borel Normality (Passed if <= 1.0)",
        fontsize=11,
        fontweight="semibold",
        color="#34495e",
    )
    axs[0, 2].set_ylabel("Borel Metric Value", fontsize=9)
    axs[0, 2].set_xticks(x)
    axs[0, 2].set_xticklabels(labels, fontsize=8)
    axs[0, 2].set_ylim(0, 1.2)
    axs[0, 2].axhline(
        y=1.0,
        color="r",
        linestyle="--",
        linewidth=1.0,
        label="Normality Threshold (1.0)",
    )
    axs[0, 2].legend(
        loc="upper right", frameon=True, facecolor=c_light_gray, fontsize=8
    )
    axs[0, 2].grid(axis="y", linestyle=":", alpha=0.6)
    for i, val in enumerate(borel_vals):
        axs[0, 2].text(
            i, val + 0.03, f"{val:.3f}", ha="center", fontsize=8, fontweight="semibold"
        )

    # 4. ENT Serial Correlation
    corr_vals = [data[c]["serial_correlation"] for c in conditions]
    axs[1, 0].bar(x, corr_vals, width * 1.5, color=c_orange)
    axs[1, 0].set_title(
        "Fourmilab ENT Serial Correlation (Ideal: 0.0)",
        fontsize=11,
        fontweight="semibold",
        color="#34495e",
    )
    axs[1, 0].set_ylabel("Correlation Coefficient", fontsize=9)
    axs[1, 0].set_xticks(x)
    axs[1, 0].set_xticklabels(labels, fontsize=8)
    y_max = max(abs(v) for v in corr_vals) * 1.5
    axs[1, 0].set_ylim(-y_max, y_max)
    axs[1, 0].axhline(y=0.0, color="#34495e", linestyle="-", linewidth=0.8)
    axs[1, 0].grid(axis="y", linestyle=":", alpha=0.6)
    for i, val in enumerate(corr_vals):
        va = "bottom" if val >= 0 else "top"
        offset = y_max * 0.05 if val >= 0 else -y_max * 0.05
        axs[1, 0].text(
            i,
            val + offset,
            f"{val:.6f}",
            ha="center",
            va=va,
            fontsize=8,
            fontweight="semibold",
        )

    # 5. TestU01 Rabbit Suspect P-values
    rabbit_vals = [data[c]["rabbit"] for c in conditions]
    axs[1, 1].bar(x, rabbit_vals, width * 1.5, color=c_red)
    axs[1, 1].set_title(
        "TestU01 Rabbit Suspect P-values (Ideal: 0)",
        fontsize=11,
        fontweight="semibold",
        color="#34495e",
    )
    axs[1, 1].set_ylabel("Suspect P-values Count", fontsize=9)
    axs[1, 1].set_xticks(x)
    axs[1, 1].set_xticklabels(labels, fontsize=8)
    axs[1, 1].set_ylim(0, 7)
    axs[1, 1].axhline(
        y=0.0, color="g", linestyle="--", linewidth=1.0, label="Ideal (0)"
    )
    axs[1, 1].legend(
        loc="upper right", frameon=True, facecolor=c_light_gray, fontsize=8
    )
    axs[1, 1].grid(axis="y", linestyle=":", alpha=0.6)
    for i, val in enumerate(rabbit_vals):
        axs[1, 1].text(
            i, val + 0.15, str(val), ha="center", fontsize=8, fontweight="semibold"
        )

    # 6. Description Subplot (Hide axis)
    axs[1, 2].axis("off")

    plt.tight_layout()
    output_path = results_dir / "comparison_plot.png"
    plt.savefig(output_path, dpi=300)
    print(f"Plot successfully saved to: {output_path}")


if __name__ == "__main__":
    main()
