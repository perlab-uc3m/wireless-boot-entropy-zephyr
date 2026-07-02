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
UDP_TARGET_IP=""
UDP_BURST_PORT="9999"
UDP_BURST_PAYLOAD_SIZE="64"
UDP_BURST_EXPECT_BYTE="0x42"
UDP_BURST_INTERVAL_US="1000"
BUILD_ARGS=()
CAPTURE_ARGS=()

usage() {
    cat <<EOF
Usage: $0 [options]

Required for wifi_* conditions:
    --wifi-ssid <ssid>
    --wifi-pass <pass>

Options:
    --condition <name>   rf_disabled, wifi_idle, wifi_scan, udp_burst (default: rf_disabled)
                         wifi_traffic remains as a legacy alias for udp_burst
    --port <device>      Serial port (default: /dev/ttyUSB0)
    --board <target>     Zephyr board target (default: auto-detected ESP32 DevKitC)
    --build-dir <dir>    Build directory passed to build.sh
    --baud <baud>        Serial baud rate (default: 921600)
    --raw-bytes <bytes>  Bytes to emit and capture (default: 268435456, 256 MiB)
    --raw-delay-ms <ms>  Delay before raw marker (default: 15000)
    --progress-interval <seconds>
                         Seconds between capture progress lines (default: 30)
    --startup-timeout <seconds>
                         Seconds to wait for raw marker after boot (default: 90)
    --capture-reset      Reset ESP32 after opening serial port for capture
    --output-dir <dir>   Capture output directory (default: data/)
    --udp-ip <ip>        Override ESP32 target IP for udp_burst capture
    --udp-port <port>    UDP burst listen port on ESP32 (default: 9999)
    --udp-payload-bytes <bytes>
                         Deterministic UDP payload size (default: 64)
    --udp-byte <value>   Repeated deterministic payload byte (default: 0x42)
    --udp-interval-us <us>
                         Host UDP burst interval during capture (default: 1000)
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
        --wifi-ssid|--wifi-pass|--board|--build-dir)
            BUILD_ARGS+=("$1" "$2")
            shift 2
            ;;
        --udp-ip)
            UDP_TARGET_IP="$2"
            shift 2
            ;;
        --udp-port)
            UDP_BURST_PORT="$2"
            BUILD_ARGS+=("$1" "$2")
            shift 2
            ;;
        --udp-payload-bytes)
            UDP_BURST_PAYLOAD_SIZE="$2"
            BUILD_ARGS+=("$1" "$2")
            shift 2
            ;;
        --udp-byte)
            UDP_BURST_EXPECT_BYTE="$2"
            BUILD_ARGS+=("$1" "$2")
            shift 2
            ;;
        --udp-interval-us)
            UDP_BURST_INTERVAL_US="$2"
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
    rf_disabled|wifi_idle|wifi_scan|udp_burst|wifi_traffic) ;;
    *)
        echo "Invalid condition: $CONDITION"
        exit 1
        ;;
esac

if [[ "$CONDITION" == "wifi_traffic" ]]; then
    echo "Note: condition wifi_traffic is a legacy alias for udp_burst."
    CONDITION="udp_burst"
fi

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

if ! [[ "$UDP_BURST_PORT" =~ ^[0-9]+$ ]] || [[ "$UDP_BURST_PORT" -le 0 ]] || [[ "$UDP_BURST_PORT" -gt 65535 ]]; then
    echo "Invalid --udp-port value: $UDP_BURST_PORT"
    exit 1
fi

if ! [[ "$UDP_BURST_PAYLOAD_SIZE" =~ ^[0-9]+$ ]] || [[ "$UDP_BURST_PAYLOAD_SIZE" -le 0 ]] || [[ "$UDP_BURST_PAYLOAD_SIZE" -gt 1400 ]]; then
    echo "Invalid --udp-payload-bytes value: $UDP_BURST_PAYLOAD_SIZE"
    exit 1
fi

if ! [[ "$UDP_BURST_EXPECT_BYTE" =~ ^(0x[0-9A-Fa-f]+|[0-9]+)$ ]]; then
    echo "Invalid --udp-byte value: $UDP_BURST_EXPECT_BYTE"
    exit 1
fi

if ! [[ "$UDP_BURST_INTERVAL_US" =~ ^[0-9]+$ ]] || [[ "$UDP_BURST_INTERVAL_US" -le 0 ]]; then
    echo "Invalid --udp-interval-us value: $UDP_BURST_INTERVAL_US"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
OUTPUT_FILE="$OUTPUT_DIR/${CONDITION}_${RAW_BYTES}.bin"

BUILD_CMD=("$PROJECT_ROOT/scripts/build.sh" --condition "$CONDITION" --raw-bytes "$RAW_BYTES" --raw-delay-ms "$RAW_START_DELAY_MS" --flash --flash-port "$PORT")
if [[ "$DO_CLEAN" == true ]]; then
    BUILD_CMD+=(--clean)
fi

"${BUILD_CMD[@]}" "${BUILD_ARGS[@]}"

if [[ "$CONDITION" == "udp_burst" ]]; then
    CAPTURE_ARGS+=(
        --udp-burst
        --udp-port "$UDP_BURST_PORT"
        --udp-payload-bytes "$UDP_BURST_PAYLOAD_SIZE"
        --udp-byte "$UDP_BURST_EXPECT_BYTE"
        --udp-interval-us "$UDP_BURST_INTERVAL_US"
    )
    if [[ -n "$UDP_TARGET_IP" ]]; then
        CAPTURE_ARGS+=(--udp-target-ip "$UDP_TARGET_IP")
    fi
fi

python3 -u "$PROJECT_ROOT/scripts/capture_binary.py" \
    --port "$PORT" \
    --baud "$BAUD" \
    --bytes "$RAW_BYTES" \
    --output "$OUTPUT_FILE" \
    --startup-timeout "$STARTUP_TIMEOUT" \
    --progress-interval "$PROGRESS_INTERVAL" \
    "${CAPTURE_ARGS[@]}"

echo "Captured: $OUTPUT_FILE"
