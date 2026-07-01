#!/bin/bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_ROOT/.." && pwd)"
WORKSPACE_ROOT="${TEB_WEST_WORKSPACE:-$REPO_ROOT/.west-capsule-bootstrap}"
BUILD_DIR="${TEB_BUILD_DIR:-$WORKSPACE_ROOT/build-capsule-bootstrap}"
BOARD_TARGET="esp32_devkitc_wroom/esp32/procpu"

WIFI_SSID=""
WIFI_PASS=""
TEB_SERVER_IP="192.168.1.136"
TEB_SERVER_PORT="6767"
TEB_DEVICE_ID="0x4553503332544542"
TEB_PUF_BYTES="4096"
TEB_PUF_DUMP_HEX="0"
TEB_DISABLE_LOCAL_REFILL="1"
TEB_PROFILE="pq"

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
    --wifi-ssid <ssid>
    --wifi-pass <pass>
Optional:
    --server-ip <ip>       Capsule server IP (default: 192.168.1.136)
    --server-port <port>   Capsule server UDP port (default: 6767)
    --device-id <hex>      Non-secret device id (default: 0x4553503332544542)
    --puf-bytes <bytes>    SRAM startup bytes sampled (default: 4096)
    --puf-dump             Print raw SRAM PUF bytes over serial
    --profile <name>       ed25519 or pq (default: pq)
    --allow-local-refill   Keep local BLAKE2s hardware refill enabled
    --clean                Remove build directory first
    --init                 Initialize west workspace
    --flash                Flash after build
    --monitor              Open serial monitor after flash
    -h|--help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --wifi-ssid)
            WIFI_SSID="$2"; shift 2 ;;
        --wifi-pass)
            WIFI_PASS="$2"; shift 2 ;;
        --server-ip)
            TEB_SERVER_IP="$2"; shift 2 ;;
        --server-port)
            TEB_SERVER_PORT="$2"; shift 2 ;;
        --device-id)
            TEB_DEVICE_ID="$2"; shift 2 ;;
        --puf-bytes)
            TEB_PUF_BYTES="$2"; shift 2 ;;
        --puf-dump)
            TEB_PUF_DUMP_HEX="1"; shift ;;
        --profile)
            TEB_PROFILE="$2"; shift 2 ;;
        --allow-local-refill)
            TEB_DISABLE_LOCAL_REFILL="0"; shift ;;
        --clean)
            DO_CLEAN=true; shift ;;
        --init)
            DO_INIT=true; shift ;;
        --flash)
            DO_FLASH=true; shift ;;
        --monitor)
            DO_MONITOR=true; shift ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

if [[ -z "$WIFI_SSID" || -z "$WIFI_PASS" ]]; then
    echo "Wi-Fi credentials are required."
    exit 1
fi

case "$TEB_PROFILE" in
    ed25519|dev-ed25519)
        TEB_PROFILE_VALUE=1 ;;
    pq|mlkem512-mldsa44)
        TEB_PROFILE_VALUE=2 ;;
    *)
        echo "Unknown profile: $TEB_PROFILE (expected ed25519 or pq)"
        exit 1 ;;
esac

echo "============================================="
echo "Asymmetric Entropy Capsule Bootstrap Build"
echo "  Server:       $TEB_SERVER_IP:$TEB_SERVER_PORT"
echo "  Device ID:    $TEB_DEVICE_ID"
echo "  PUF bytes:    $TEB_PUF_BYTES"
echo "  PUF dump:     $TEB_PUF_DUMP_HEX"
echo "  Profile:      $TEB_PROFILE ($TEB_PROFILE_VALUE)"
echo "  WiFi SSID:    $WIFI_SSID"
echo "============================================="

cd "$PROJECT_ROOT"

if [ "$DO_CLEAN" = true ]; then
    rm -rf "$BUILD_DIR"
fi

init_workspace() {
    mkdir -p "$WORKSPACE_ROOT"
    west init -l "$PROJECT_ROOT" "$WORKSPACE_ROOT"
    (cd "$WORKSPACE_ROOT" && west update)
    (cd "$WORKSPACE_ROOT" && west blobs fetch hal_espressif 2>/dev/null || true)
}

if [ "$DO_INIT" = true ]; then
    init_workspace
    exit 0
fi

if [ ! -f "$WORKSPACE_ROOT/.west/config" ]; then
    init_workspace
fi

(cd "$WORKSPACE_ROOT" && west zephyr-export 2>/dev/null || true)
(cd "$WORKSPACE_ROOT" && west blobs fetch hal_espressif)

export WIFI_SSID WIFI_PASS
export TEB_SERVER_IP TEB_SERVER_PORT TEB_DEVICE_ID
export TEB_PUF_BYTES TEB_PUF_DUMP_HEX TEB_DISABLE_LOCAL_REFILL
export TEB_PROFILE="$TEB_PROFILE_VALUE"

(cd "$WORKSPACE_ROOT" && CCACHE_DISABLE="${CCACHE_DISABLE:-1}" \
    ZEPHYR_BASE="$WORKSPACE_ROOT/zephyr" \
    west build -d "$BUILD_DIR" -p auto -b "$BOARD_TARGET" "$PROJECT_ROOT") || {
        echo "Build failed"
        exit 1
    }

if [ "$DO_FLASH" = true ]; then
    (cd "$WORKSPACE_ROOT" && west flash -d "$BUILD_DIR" --skip-rebuild) || exit 1
fi
if [ "$DO_MONITOR" = true ]; then
    (cd "$WORKSPACE_ROOT" && west espressif monitor)
fi
