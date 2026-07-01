#!/bin/bash
# Launch a benchmark condition detached from the VS Code terminal.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$PROJECT_ROOT/run"
CONDITION="rf_disabled"
RAW_BYTES="268435456"
ORIGINAL_ARGS=("$@")

while [[ $# -gt 0 ]]; do
    case "$1" in
        --condition)
            CONDITION="$2"
            shift 2
            ;;
        --raw-bytes)
            RAW_BYTES="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

mkdir -p "$RUN_DIR"
LOG_FILE="$RUN_DIR/${CONDITION}_${RAW_BYTES}.log"
PID_FILE="$RUN_DIR/${CONDITION}_${RAW_BYTES}.pid"

cd "$PROJECT_ROOT"
nohup "$PROJECT_ROOT/scripts/run_condition.sh" "${ORIGINAL_ARGS[@]}" > "$LOG_FILE" 2>&1 < /dev/null &
PID="$!"
printf '%s\n' "$PID" > "$PID_FILE"

echo "Started $CONDITION capture"
echo "  PID:  $PID"
echo "  Log:  $LOG_FILE"
echo "  Data: $PROJECT_ROOT/data/${CONDITION}_${RAW_BYTES}.bin"
echo "Check status with: $PROJECT_ROOT/scripts/check_condition.sh --condition $CONDITION --raw-bytes $RAW_BYTES"