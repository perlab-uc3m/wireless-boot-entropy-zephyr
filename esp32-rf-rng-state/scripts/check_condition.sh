#!/bin/bash
# Show low-output status for a benchmark condition.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDITION="rf_disabled"
RAW_BYTES="268435456"

usage() {
    cat <<EOF
Usage: $0 --condition <name> [--raw-bytes <bytes>]
EOF
}

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

DATA_FILE="$PROJECT_ROOT/data/${CONDITION}_${RAW_BYTES}.bin"
SHA_FILE="$DATA_FILE.sha256"
LOG_FILE="$PROJECT_ROOT/run/${CONDITION}_${RAW_BYTES}.log"
ALT_LOG_FILE="/tmp/rf-trng-${CONDITION//_/-}-full.log"
PID_FILE="$PROJECT_ROOT/run/${CONDITION}_${RAW_BYTES}.pid"

if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE")"
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
        echo "Process: running (pid $PID)"
    else
        echo "Process: not running (last pid $PID)"
    fi
else
    MATCHES="$(pgrep -af "run_condition.sh --condition $CONDITION|capture_binary.py .*${CONDITION}_${RAW_BYTES}.bin" || true)"
    if [[ -n "$MATCHES" ]]; then
        echo "Process: running"
        echo "$MATCHES"
    else
        echo "Process: no pid file and no matching process"
    fi
fi

if [[ -f "$DATA_FILE" ]]; then
    SIZE="$(stat -c '%s' "$DATA_FILE")"
    awk -v size="$SIZE" -v total="$RAW_BYTES" 'BEGIN { printf "Data: %d bytes (%.2f MiB / %.2f MiB, %.2f%%)\n", size, size/1048576, total/1048576, (size/total)*100 }'
else
    echo "Data: not created"
fi

if [[ -f "$SHA_FILE" ]]; then
    echo "Checksum: $(cat "$SHA_FILE")"
fi

if [[ ! -f "$LOG_FILE" && -f "$ALT_LOG_FILE" ]]; then
    LOG_FILE="$ALT_LOG_FILE"
fi

if [[ -f "$LOG_FILE" ]]; then
    echo "Log: $LOG_FILE ($(du -h "$LOG_FILE" | awk '{print $1}'))"
    grep -E '\[MAIN\]|wifi_connected_before_raw|BENCH_RAW_ARMED|Start marker|Collected:|Success|Error|Timeout|Total Bytes|Captured:' "$LOG_FILE" | tail -20 || true
else
    echo "Log: not found at $LOG_FILE"
fi