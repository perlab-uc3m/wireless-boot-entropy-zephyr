#!/bin/bash
# esp32-rf-rng-state/scripts/build.sh
#
# Copyright (C) 2026 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
#
# Build script for the RF-TRNG benchmark.
#
# Each condition requires a separate build because the Wi-Fi driver
# presence changes the RF subsystem state at boot.
#
# Usage:
#   ./scripts/build.sh --condition rf_disabled --clean --flash --monitor
#   ./scripts/build.sh --condition wifi_idle --wifi-ssid SSID --wifi-pass PASS --flash --monitor
#   ./scripts/build.sh --condition wifi_scan --wifi-ssid SSID --wifi-pass PASS --flash --monitor
#   ./scripts/build.sh --condition wifi_traffic --wifi-ssid SSID --wifi-pass PASS --flash --monitor

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOARD_TARGET="esp32_devkitc_wroom/esp32/procpu"
CONDITION="rf_disabled"

WIFI_SSID=""
WIFI_PASS=""
UDP_TARGET_IP="192.168.1.136"
UDP_TARGET_PORT="9999"
RAW_BYTES="268435456"
RAW_START_DELAY_MS="15000"

# Source .env if present
ENV_FILE="$PROJECT_ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

DO_CLEAN=false
DO_INIT=false
DO_FLASH=false
DO_MONITOR=false

usage() {
    cat <<EOF
Usage: $0 [options]
Required:
    --condition <name>   Benchmark condition:
                         rf_disabled  - no Wi-Fi (pseudo-random baseline)
                         wifi_idle    - Wi-Fi associated, no traffic
                         wifi_scan    - Wi-Fi associated + periodic scans
                         wifi_traffic - Wi-Fi associated + UDP flood
Optional:
    --wifi-ssid <ssid>   Wi-Fi SSID (required for wifi_* conditions)
    --wifi-pass <pass>   Wi-Fi password
    --udp-ip <ip>        UDP flood target IP (default: 192.168.1.136)
    --udp-port <port>    UDP flood target port (default: 9999)
    --raw-bytes <bytes>  Raw binary bytes emitted by firmware (default: 268435456, 256 MiB)
    --raw-delay-ms <ms>  Delay before raw marker (default: 15000)
    --clean              Remove build directory first
    --init               Initialize west workspace
    --flash              Flash after build
    --monitor            Open serial monitor after flash
    -h|--help            Show this help
Example:
    $0 --condition wifi_idle --wifi-ssid MyWiFi --wifi-pass secret --clean --flash --monitor
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --condition)
            CONDITION="$2"
            shift 2
            ;;
        --wifi-ssid)
            WIFI_SSID="$2"
            shift 2
            ;;
        --wifi-pass)
            WIFI_PASS="$2"
            shift 2
            ;;
        --udp-ip)
            UDP_TARGET_IP="$2"
            shift 2
            ;;
        --udp-port)
            UDP_TARGET_PORT="$2"
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
        --clean)
            DO_CLEAN=true
            shift
            ;;
        --init)
            DO_INIT=true
            shift
            ;;
        --flash)
            DO_FLASH=true
            shift
            ;;
        --monitor)
            DO_MONITOR=true
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

# Validate condition
case "$CONDITION" in
    rf_disabled|wifi_idle|wifi_scan|wifi_traffic)
        ;;
    *)
        echo "Invalid condition: $CONDITION"
        echo "Valid: rf_disabled, wifi_idle, wifi_scan, wifi_traffic"
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

# Validate Wi-Fi credentials for wifi_* conditions
if [[ "$CONDITION" != "rf_disabled" ]]; then
    if [[ -z "$WIFI_SSID" || -z "$WIFI_PASS" ]]; then
        echo "Wi-Fi credentials required for condition '$CONDITION'."
        echo "Pass --wifi-ssid/--wifi-pass or create .env"
        exit 1
    fi
fi

# Map condition -> CMake define
BENCH_CONDITION=$(echo "$CONDITION" | tr '[:lower:]' '[:upper:]')

echo "============================================="
echo "RF-TRNG Benchmark Build"
echo "  Condition:  $CONDITION ($BENCH_CONDITION)"
echo "  Raw bytes:  $RAW_BYTES"
echo "  Raw delay:  ${RAW_START_DELAY_MS} ms"
if [[ "$CONDITION" != "rf_disabled" ]]; then
    echo "  WiFi SSID:  $WIFI_SSID"
fi
if [[ "$CONDITION" == "wifi_traffic" ]]; then
    echo "  UDP target: $UDP_TARGET_IP:$UDP_TARGET_PORT"
fi
echo "============================================="

cd "$PROJECT_ROOT"

# Clean
if [ "$DO_CLEAN" = true ]; then
    rm -rf build/
    find . -name "CMakeCache.txt" -delete 2>/dev/null || true
    find . -name "CMakeFiles" -type d -exec rm -rf {} + 2>/dev/null || true
fi

# Init
init_workspace() {
    west init -l .
    west update
    west blobs fetch hal_espressif 2>/dev/null || true
}

if [ "$DO_INIT" = true ]; then init_workspace; exit 0; fi

# Initialize if needed
if [ ! -f ".west/config" ] && [ ! -f "../.west/config" ]; then
    init_workspace
else
    west update
fi

west zephyr-export

if [[ "$CONDITION" != "rf_disabled" ]]; then
    west blobs fetch hal_espressif
fi

# Export environment
export BENCH_CONDITION="$BENCH_CONDITION"
export WIFI_SSID WIFI_PASS
export UDP_TARGET_IP UDP_TARGET_PORT
export TRNG_RAW_DUMP_BYTES="$RAW_BYTES"
export TRNG_RAW_START_DELAY_MS="$RAW_START_DELAY_MS"

# Build command - add Wi-Fi overlay for non-RF_DISABLED conditions
EXTRA_ARGS=""
if [[ "$CONDITION" != "rf_disabled" ]]; then
    EXTRA_ARGS="-DOVERLAY_CONFIG=overlay/wifi.conf"
fi

west build -p auto -b "$BOARD_TARGET" . $EXTRA_ARGS \
    -DBENCH_CONDITION="$BENCH_CONDITION" \
    -DTRNG_RAW_DUMP_BYTES="$RAW_BYTES" \
    -DTRNG_RAW_START_DELAY_MS="$RAW_START_DELAY_MS" \
    || { echo "Build failed"; exit 1; }

if [ "$DO_FLASH" = true ]; then west flash || exit 1; fi
if [ "$DO_MONITOR" = true ]; then west espressif monitor; fi
