#!/bin/bash
# Build, flash, and capture one RF-TRNG benchmark condition.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDITION="rf_disabled"
PORT="/dev/ttyUSB0"
BAUD="921600"
RAW_BYTES="268435456"
RAW_START_DELAY_MS="15000"
PROGRESS_INTERVAL="30"
STARTUP_TIMEOUT="90"
OUTPUT_DIR="$PROJECT_ROOT/data"
BUILD_ARGS=()
CAPTURE_ARGS=()

usage() {
    cat <<EOF
Usage: $0 [options]

Required for wifi_* conditions:
    --wifi-ssid <ssid>
    --wifi-pass <pass>

Options:
    --condition <name>   rf_disabled, wifi_idle, wifi_scan, wifi_traffic (default: rf_disabled)
    --port <device>      Serial port (default: /dev/ttyUSB0)
    --baud <baud>        Serial baud rate (default: 921600)
    --raw-bytes <bytes>  Bytes to emit and capture (default: 268435456, 256 MiB)
    --raw-delay-ms <ms>  Delay before raw marker (default: 15000)
    --progress-interval <seconds>
                         Seconds between capture progress lines (default: 30)
    --startup-timeout <seconds>
                         Seconds to wait for raw marker after boot (default: 90)
    --capture-reset      Reset ESP32 after opening serial port for capture
    --output-dir <dir>   Capture output directory (default: data/)
    --udp-ip <ip>        UDP flood target for wifi_traffic
    --udp-port <port>    UDP flood target port for wifi_traffic
    --no-clean           Reuse existing build directory
    -h|--help            Show this help

Example:
    $0 --condition wifi_idle --wifi-ssid MyWiFi --wifi-pass secret --port /dev/ttyUSB0
EOF
}

DO_CLEAN=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --condition)
            CONDITION="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --baud)
            BAUD="$2"
            shift 2
            ;;
        --raw-bytes)
            RAW_BYTES="$2"
            shift 2
            ;;
        --raw-delay-ms)
            RAW_START_DELAY_MS="$2"
            shift 2
            ;;
        --progress-interval)
            PROGRESS_INTERVAL="$2"
            shift 2
            ;;
        --startup-timeout)
            STARTUP_TIMEOUT="$2"
            shift 2
            ;;
        --capture-reset)
            CAPTURE_ARGS+=(--reset)
            shift
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --wifi-ssid|--wifi-pass|--udp-ip|--udp-port)
            BUILD_ARGS+=("$1" "$2")
            shift 2
            ;;
        --no-clean)
            DO_CLEAN=false
            shift
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

case "$CONDITION" in
    rf_disabled|wifi_idle|wifi_scan|wifi_traffic) ;;
    *)
        echo "Invalid condition: $CONDITION"
        exit 1
        ;;
esac

if ! [[ "$RAW_BYTES" =~ ^[0-9]+$ ]] || [[ "$RAW_BYTES" -le 0 ]]; then
    echo "Invalid --raw-bytes value: $RAW_BYTES"
    exit 1
fi

if ! [[ "$RAW_START_DELAY_MS" =~ ^[0-9]+$ ]]; then
    echo "Invalid --raw-delay-ms value: $RAW_START_DELAY_MS"
    exit 1
fi

if ! [[ "$PROGRESS_INTERVAL" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "Invalid --progress-interval value: $PROGRESS_INTERVAL"
    exit 1
fi

if ! [[ "$STARTUP_TIMEOUT" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "Invalid --startup-timeout value: $STARTUP_TIMEOUT"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
OUTPUT_FILE="$OUTPUT_DIR/${CONDITION}_${RAW_BYTES}.bin"

BUILD_CMD=("$PROJECT_ROOT/scripts/build.sh" --condition "$CONDITION" --raw-bytes "$RAW_BYTES" --raw-delay-ms "$RAW_START_DELAY_MS" --flash)
if [[ "$DO_CLEAN" == true ]]; then
    BUILD_CMD+=(--clean)
fi

"${BUILD_CMD[@]}" "${BUILD_ARGS[@]}"
python3 -u "$PROJECT_ROOT/scripts/capture_binary.py" \
    --port "$PORT" \
    --baud "$BAUD" \
    --bytes "$RAW_BYTES" \
    --output "$OUTPUT_FILE" \
    --startup-timeout "$STARTUP_TIMEOUT" \
    --progress-interval "$PROGRESS_INTERVAL" \
    "${CAPTURE_ARGS[@]}"

echo "Captured: $OUTPUT_FILE"