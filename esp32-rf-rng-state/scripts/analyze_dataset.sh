#!/bin/bash
# Run the RF-state randlab battery for a board/date dataset.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$PROJECT_ROOT/data"
RESULTS_DIR="$PROJECT_ROOT/results"
RANDLAB_BIN="$PROJECT_ROOT/../../randlab/.venv/bin/randlab"
TOOLS_ROOT="$PROJECT_ROOT/../../randlab/.randlab/tools"
LABEL="256m"
PROFILE="paper"
CONDITIONS=(rf_disabled wifi_idle wifi_scan udp_burst urandom)

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
    --raw-dir <dir>      Directory containing <condition>_<bytes>.bin files
    --results-dir <dir>  Directory for randlab result folders
    --label <label>      Result suffix, e.g. 256m (default: 256m)
    --profile <profile>  randlab profile (default: paper)
    --randlab <path>     randlab executable
    --tools-root <dir>   randlab tools root
    --condition <name>   Analyze one condition; may be repeated
    -h|--help            Show this help
EOF
}

CUSTOM_CONDITIONS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --raw-dir)
            RAW_DIR="$2"
            shift 2
            ;;
        --results-dir)
            RESULTS_DIR="$2"
            shift 2
            ;;
        --label)
            LABEL="$2"
            shift 2
            ;;
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --randlab)
            RANDLAB_BIN="$2"
            shift 2
            ;;
        --tools-root)
            TOOLS_ROOT="$2"
            shift 2
            ;;
        --condition)
            CUSTOM_CONDITIONS+=("$2")
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ "${#CUSTOM_CONDITIONS[@]}" -gt 0 ]]; then
    CONDITIONS=("${CUSTOM_CONDITIONS[@]}")
fi

if [[ ! -x "$RANDLAB_BIN" ]]; then
    echo "randlab executable not found or not executable: $RANDLAB_BIN"
    exit 1
fi

mkdir -p "$RESULTS_DIR"

for condition in "${CONDITIONS[@]}"; do
    mapfile -t matches < <(find "$RAW_DIR" -maxdepth 1 -type f -name "${condition}_*.bin" | sort)
    if [[ "${#matches[@]}" -eq 0 ]]; then
        echo "Missing raw stream for condition: $condition"
        echo "Expected a file like: $RAW_DIR/${condition}_268435456.bin"
        exit 1
    fi

    input_file="${matches[$((${#matches[@]} - 1))]}"
    out_dir="$RESULTS_DIR/${condition}_${LABEL}"

    echo "============================================="
    echo "Analyzing $condition"
    echo "  Input:  $input_file"
    echo "  Output: $out_dir"
    echo "============================================="

    "$RANDLAB_BIN" run \
        --tools-root "$TOOLS_ROOT" \
        --input "$input_file" \
        --format raw \
        --profile "$PROFILE" \
        --suite ent \
        --suite entropy-iid \
        --suite entropy-non-iid \
        --suite borel \
        --suite ais31-p1-t0 \
        --suite ais31-p1-t1-t5 \
        --suite ais31-p2 \
        --suite gmt-sts \
        --suite practrand \
        --suite testu01-rabbit \
        --out "$out_dir"
done

python3 "$PROJECT_ROOT/scripts/compare_results.py" --results-dir "$RESULTS_DIR" \
    > "$RESULTS_DIR/comparison.md"
python3 "$PROJECT_ROOT/scripts/plot_results.py" --results-dir "$RESULTS_DIR" \
    --output "$RESULTS_DIR/comparison_plot.png"

echo "Wrote:"
echo "  $RESULTS_DIR/comparison.md"
echo "  $RESULTS_DIR/comparison_plot.png"
