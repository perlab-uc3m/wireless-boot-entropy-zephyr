#!/bin/bash
# Build helper for RF-actuated boot entropy.

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOARD_TARGET="esp32_devkitc_wroom/esp32/procpu"

WIFI_SSID=""
WIFI_PASS=""
AEB_UDP_PORT="7777"
AEB_GATEWAY_IP=""
AEB_GATEWAY_PORT="7778"
AEB_CLIENT_INITIATED="1"
AEB_CLIENT_TRIALS="16"
AEB_BURST_COUNT="64"
AEB_INTERVAL_US="1000"
AEB_MAX_SAMPLE_BYTES="8192"
AEB_MAX_BURSTS="256"
AEB_RAW_CHUNK_BYTES="512"
AEB_TRIAL_GAP_MS="250"
AEB_DUMP_RAW_HEX="0"
AEB_DUMP_SEED="0"

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

run_west() {
    if [[ -n "${WEST_PYTHON:-}" ]]; then
        "$WEST_PYTHON" -m west "$@"
    else
        west "$@"
    fi
}

usage() {
    cat <<EOF
Usage: $0 [options]

Required:
  --wifi-ssid <ssid>       Wi-Fi SSID
  --wifi-pass <pass>       Wi-Fi password

Optional:
  --gateway-ip <ip>        Collector/gateway IPv4 address for client HELLO mode
  --gateway-port <port>    Collector/gateway UDP port (default: 7778)
  --listen-mode            Use legacy host-initiated listener mode
  --udp-port <port>        ESP32 UDP listen port in listen mode (default: 7777)
  --trials <n>             Client-initiated trials per boot (default: 16)
  --bursts <n>             Burst packets requested per trial (default: 64)
  --interval-us <n>        Burst spacing requested per trial (default: 1000)
  --max-sample-bytes <n>   Max local response bytes per trial (default: 8192)
  --max-bursts <n>         Max burst packets per trial (default: 256)
  --raw-chunk-bytes <n>    Raw UDP upload payload bytes per chunk (default: 512)
  --trial-gap-ms <n>       Delay between client trials (default: 250)
  --dump-raw               Print raw response as hex chunks (lab only)
  --dump-seed              Print derived seed (lab only)
  --clean                  Remove build directory first
  --init                   Initialize/update west workspace
  --flash                  Flash after build
  --monitor                Open serial monitor after flash
  -h|--help                Show this help

Example:
  $0 --wifi-ssid MyWiFi --wifi-pass secret --gateway-ip 192.168.1.50 --clean --flash
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --wifi-ssid)
            WIFI_SSID="$2"
            shift 2
            ;;
        --wifi-pass)
            WIFI_PASS="$2"
            shift 2
            ;;
        --udp-port)
            AEB_UDP_PORT="$2"
            shift 2
            ;;
        --gateway-ip)
            AEB_GATEWAY_IP="$2"
            shift 2
            ;;
        --gateway-port)
            AEB_GATEWAY_PORT="$2"
            shift 2
            ;;
        --listen-mode)
            AEB_CLIENT_INITIATED="0"
            shift
            ;;
        --trials)
            AEB_CLIENT_TRIALS="$2"
            shift 2
            ;;
        --bursts)
            AEB_BURST_COUNT="$2"
            shift 2
            ;;
        --interval-us)
            AEB_INTERVAL_US="$2"
            shift 2
            ;;
        --max-sample-bytes)
            AEB_MAX_SAMPLE_BYTES="$2"
            shift 2
            ;;
        --max-bursts)
            AEB_MAX_BURSTS="$2"
            shift 2
            ;;
        --raw-chunk-bytes)
            AEB_RAW_CHUNK_BYTES="$2"
            shift 2
            ;;
        --trial-gap-ms)
            AEB_TRIAL_GAP_MS="$2"
            shift 2
            ;;
        --dump-raw)
            AEB_DUMP_RAW_HEX="1"
            shift
            ;;
        --dump-seed)
            AEB_DUMP_SEED="1"
            shift
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

for value_name in AEB_UDP_PORT AEB_GATEWAY_PORT AEB_CLIENT_INITIATED \
    AEB_CLIENT_TRIALS AEB_BURST_COUNT AEB_INTERVAL_US \
    AEB_MAX_SAMPLE_BYTES AEB_MAX_BURSTS AEB_RAW_CHUNK_BYTES AEB_TRIAL_GAP_MS; do
    value="${!value_name}"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        echo "Invalid $value_name=$value"
        exit 1
    fi
done

for positive_name in AEB_UDP_PORT AEB_GATEWAY_PORT AEB_CLIENT_TRIALS \
    AEB_MAX_SAMPLE_BYTES AEB_MAX_BURSTS AEB_RAW_CHUNK_BYTES; do
    value="${!positive_name}"
    if [[ "$value" -le 0 ]]; then
        echo "Invalid $positive_name=$value"
        exit 1
    fi
done

if [[ -z "$WIFI_SSID" || -z "$WIFI_PASS" ]]; then
    echo "Wi-Fi credentials required. Use --wifi-ssid/--wifi-pass or .env"
    exit 1
fi

if [[ "$AEB_CLIENT_INITIATED" == "1" && -z "$AEB_GATEWAY_IP" ]]; then
    echo "Client HELLO mode requires --gateway-ip"
    exit 1
fi

if [[ "$AEB_BURST_COUNT" -gt "$AEB_MAX_BURSTS" ]]; then
    echo "--bursts must be <= --max-bursts"
    exit 1
fi

echo "============================================="
echo "RF-Actuated Boot Entropy Build"
echo "  WiFi SSID:        $WIFI_SSID"
echo "  UDP port:         $AEB_UDP_PORT"
echo "  Gateway:          ${AEB_GATEWAY_IP:-n/a}:$AEB_GATEWAY_PORT"
echo "  Client mode:      $AEB_CLIENT_INITIATED"
echo "  Client trials:    $AEB_CLIENT_TRIALS"
echo "  Burst count:      $AEB_BURST_COUNT"
echo "  Interval us:      $AEB_INTERVAL_US"
echo "  Max sample bytes: $AEB_MAX_SAMPLE_BYTES"
echo "  Max bursts:       $AEB_MAX_BURSTS"
echo "  Raw chunk bytes:  $AEB_RAW_CHUNK_BYTES"
echo "  Trial gap ms:     $AEB_TRIAL_GAP_MS"
echo "  Dump raw hex:     $AEB_DUMP_RAW_HEX"
echo "  Dump seed:        $AEB_DUMP_SEED"
echo "============================================="

cd "$PROJECT_ROOT"

if [ "$DO_CLEAN" = true ]; then
    rm -rf build/
    find . -name "CMakeCache.txt" -delete 2>/dev/null || true
    find . -name "CMakeFiles" -type d -exec rm -rf {} + 2>/dev/null || true
fi

init_workspace() {
    run_west init -l .
    run_west update
    run_west blobs fetch hal_espressif 2>/dev/null || true
}

if [ "$DO_INIT" = true ]; then
    init_workspace
    exit 0
fi

if [ ! -f ".west/config" ] && [ ! -f "../.west/config" ]; then
    init_workspace
else
    echo "West workspace already present; skipping west update"
fi

run_west zephyr-export
run_west blobs fetch hal_espressif 2>/dev/null || true

export WIFI_SSID WIFI_PASS AEB_UDP_PORT AEB_GATEWAY_IP AEB_GATEWAY_PORT
export AEB_CLIENT_INITIATED AEB_CLIENT_TRIALS AEB_BURST_COUNT AEB_INTERVAL_US
export AEB_MAX_SAMPLE_BYTES AEB_MAX_BURSTS AEB_RAW_CHUNK_BYTES AEB_TRIAL_GAP_MS
export AEB_DUMP_RAW_HEX AEB_DUMP_SEED

run_west build -p auto -b "$BOARD_TARGET" . -DOVERLAY_CONFIG=overlay/wifi.conf \
    -DDTC_OVERLAY_FILE=app.overlay \
    -DWIFI_SSID="$WIFI_SSID" \
    -DWIFI_PASS="$WIFI_PASS" \
    -DAEB_UDP_PORT="$AEB_UDP_PORT" \
    -DAEB_GATEWAY_IP="$AEB_GATEWAY_IP" \
    -DAEB_GATEWAY_PORT="$AEB_GATEWAY_PORT" \
    -DAEB_CLIENT_INITIATED="$AEB_CLIENT_INITIATED" \
    -DAEB_CLIENT_TRIALS="$AEB_CLIENT_TRIALS" \
    -DAEB_BURST_COUNT="$AEB_BURST_COUNT" \
    -DAEB_INTERVAL_US="$AEB_INTERVAL_US" \
    -DAEB_MAX_SAMPLE_BYTES="$AEB_MAX_SAMPLE_BYTES" \
    -DAEB_MAX_BURSTS="$AEB_MAX_BURSTS" \
    -DAEB_RAW_CHUNK_BYTES="$AEB_RAW_CHUNK_BYTES" \
    -DAEB_TRIAL_GAP_MS="$AEB_TRIAL_GAP_MS" \
    -DAEB_DUMP_RAW_HEX="$AEB_DUMP_RAW_HEX" \
    -DAEB_DUMP_SEED="$AEB_DUMP_SEED"

if [ "$DO_FLASH" = true ]; then
    run_west flash
fi

if [ "$DO_MONITOR" = true ]; then
    run_west espressif monitor
fi
