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
#   ./scripts/build.sh --condition udp_burst --wifi-ssid SSID --wifi-pass PASS --flash --monitor

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_ZEPHYR_BASE="${ZEPHYR_BASE:-$PROJECT_ROOT/../../zephyr}"
BOARD_TARGET="${BOARD_TARGET:-}"
if [[ -z "$BOARD_TARGET" ]]; then
    if [[ -d "$DEFAULT_ZEPHYR_BASE/boards/espressif/esp32_devkitc" ]]; then
        BOARD_TARGET="esp32_devkitc/esp32/procpu"
    else
        BOARD_TARGET="esp32_devkitc_wroom/esp32/procpu"
    fi
fi
CONDITION="rf_disabled"
BUILD_DIR="${BUILD_DIR:-$PROJECT_ROOT/build}"
USER_CACHE_DIR="${USER_CACHE_DIR:-/tmp/zephyr-rf-rng-cache}"

WIFI_SSID="${WIFI_SSID:-}"
WIFI_PASS="${WIFI_PASS:-}"
UDP_BURST_PORT="9999"
UDP_BURST_PAYLOAD_SIZE="64"
UDP_BURST_EXPECT_BYTE="0x42"
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
FLASH_PORT=""

usage() {
    cat <<EOF
Usage: $0 [options]
Required:
    --condition <name>   Benchmark condition:
                         rf_disabled  - no Wi-Fi (pseudo-random baseline)
                         wifi_idle    - Wi-Fi associated, no traffic
                         wifi_scan    - Wi-Fi associated + periodic scans
                         udp_burst    - Wi-Fi associated + deterministic UDP bursts
                         wifi_traffic - legacy alias for udp_burst
Optional:
    --wifi-ssid <ssid>   Wi-Fi SSID (required for wifi_* conditions)
    --wifi-pass <pass>   Wi-Fi password
    --board <target>     Zephyr board target (default: auto-detected ESP32 DevKitC)
    --build-dir <dir>    Build directory (default: build/)
    --udp-port <port>    UDP burst listen port on ESP32 (default: 9999)
    --udp-payload-bytes <bytes>
                         Expected deterministic UDP payload size (default: 64)
    --udp-byte <value>   Expected repeated payload byte, decimal or hex (default: 0x42)
    --udp-ip <ip>        Ignored legacy option; host capture discovers ESP32 IP
    --raw-bytes <bytes>  Raw binary bytes emitted by firmware (default: 268435456, 256 MiB)
    --raw-delay-ms <ms>  Delay before raw marker (default: 15000)
    --clean              Remove build directory first
    --init               Initialize west workspace
    --flash              Flash after build
    --flash-port <dev>   ESP32 serial device for flashing
    --monitor            Open serial monitor after flash
    -h|--help            Show this help
Example:
    $0 --condition udp_burst --wifi-ssid MyWiFi --wifi-pass secret --clean --flash
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
        --board)
            BOARD_TARGET="$2"
            shift 2
            ;;
        --build-dir)
            BUILD_DIR="$2"
            shift 2
            ;;
        --udp-ip)
            echo "Note: --udp-ip is ignored by the firmware build; capture sends to the ESP32 DHCP address."
            shift 2
            ;;
        --udp-port)
            UDP_BURST_PORT="$2"
            shift 2
            ;;
        --udp-payload-bytes)
            UDP_BURST_PAYLOAD_SIZE="$2"
            shift 2
            ;;
        --udp-byte)
            UDP_BURST_EXPECT_BYTE="$2"
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
        --flash-port)
            FLASH_PORT="$2"
            shift 2
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
    rf_disabled|wifi_idle|wifi_scan|udp_burst|wifi_traffic)
        ;;
    *)
        echo "Invalid condition: $CONDITION"
        echo "Valid: rf_disabled, wifi_idle, wifi_scan, udp_burst"
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
echo "  Board:      $BOARD_TARGET"
echo "  Build dir:  $BUILD_DIR"
echo "  Raw bytes:  $RAW_BYTES"
echo "  Raw delay:  ${RAW_START_DELAY_MS} ms"
if [[ "$CONDITION" != "rf_disabled" ]]; then
    echo "  WiFi SSID:  $WIFI_SSID"
fi
if [[ "$CONDITION" == "udp_burst" ]]; then
    echo "  UDP burst:  port $UDP_BURST_PORT, $UDP_BURST_PAYLOAD_SIZE bytes, byte $UDP_BURST_EXPECT_BYTE"
fi
echo "============================================="

cd "$PROJECT_ROOT"

# Clean
if [ "$DO_CLEAN" = true ]; then
    rm -rf "$BUILD_DIR"
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

# Export environment
export BENCH_CONDITION="$BENCH_CONDITION"
export WIFI_SSID WIFI_PASS
export UDP_BURST_PORT UDP_BURST_PAYLOAD_SIZE UDP_BURST_EXPECT_BYTE
export TRNG_RAW_DUMP_BYTES="$RAW_BYTES"
export TRNG_RAW_START_DELAY_MS="$RAW_START_DELAY_MS"
export USER_CACHE_DIR
export CCACHE_DIR="${CCACHE_DIR:-/tmp/rf-rng-ccache}"
export CCACHE_TEMPDIR="${CCACHE_TEMPDIR:-/tmp/rf-rng-ccache-tmp}"

# Build command - add Wi-Fi overlay for non-RF_DISABLED conditions
EXTRA_ARGS=()
if [[ "$CONDITION" != "rf_disabled" ]]; then
    EXTRA_ARGS+=("-DOVERLAY_CONFIG=overlay/wifi.conf")
fi

BUILD_DEFS=(
    "-DUSER_CACHE_DIR=$USER_CACHE_DIR"
    "-DBENCH_CONDITION=$BENCH_CONDITION"
    "-DTRNG_RAW_DUMP_BYTES=$RAW_BYTES"
    "-DTRNG_RAW_START_DELAY_MS=$RAW_START_DELAY_MS"
    "-DUDP_BURST_PORT=$UDP_BURST_PORT"
    "-DUDP_BURST_PAYLOAD_SIZE=$UDP_BURST_PAYLOAD_SIZE"
    "-DUDP_BURST_EXPECT_BYTE=$UDP_BURST_EXPECT_BYTE"
)

if [[ "$CONDITION" != "rf_disabled" ]]; then
    BUILD_DEFS+=("-DWIFI_SSID=$WIFI_SSID" "-DWIFI_PASS=$WIFI_PASS")
fi

WEST_WORKSPACE=""
if [[ -n "${ZEPHYR_BASE:-}" && -f "$(dirname "$ZEPHYR_BASE")/.west/config" ]]; then
    WEST_WORKSPACE="$(dirname "$ZEPHYR_BASE")"
elif [[ -f "$PROJECT_ROOT/../.west/config" ]]; then
    WEST_WORKSPACE="$PROJECT_ROOT/.."
fi

if [[ -n "$WEST_WORKSPACE" ]]; then
    (
        cd "$WEST_WORKSPACE"
        west build -p auto -d "$BUILD_DIR" -b "$BOARD_TARGET" "$PROJECT_ROOT" \
            "${EXTRA_ARGS[@]}" "${BUILD_DEFS[@]}"
    ) || { echo "Build failed"; exit 1; }
else
    cmake -S "$PROJECT_ROOT" -B "$BUILD_DIR" -GNinja -DBOARD="$BOARD_TARGET" \
        "${EXTRA_ARGS[@]}" "${BUILD_DEFS[@]}" || { echo "Configure failed"; exit 1; }
    cmake --build "$BUILD_DIR" || { echo "Build failed"; exit 1; }
fi

if [ "$DO_FLASH" = true ]; then
    if [[ -n "$WEST_WORKSPACE" ]]; then
        FLASH_ARGS=()
        if [[ -n "$FLASH_PORT" ]]; then
            FLASH_ARGS+=(--esp-device "$FLASH_PORT")
        fi
        (cd "$WEST_WORKSPACE" && west flash -d "$BUILD_DIR" "${FLASH_ARGS[@]}") || exit 1
    else
        cmake --build "$BUILD_DIR" --target flash || exit 1
    fi
fi

if [ "$DO_MONITOR" = true ]; then
    if [[ -n "$WEST_WORKSPACE" ]]; then
        cd "$WEST_WORKSPACE"
        west espressif monitor
    else
        echo "--monitor requires a west workspace."
        exit 1
    fi
fi
