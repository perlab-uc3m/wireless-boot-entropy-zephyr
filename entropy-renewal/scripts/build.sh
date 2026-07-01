#!/usr/bin/env bash
# Build helper for the ESP32 embedded renewal benchmark.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_ROOT/.." && pwd)"
BOARD_TARGET="esp32_devkitc_wroom/esp32/procpu"

QEAAS_CLIENT_ROOT="$REPO_ROOT/../qeaas_esp32_client"
DTLS_IP="192.168.1.136"
DTLS_PORT="5684"
DTLS_ENTROPY_BYTES="32"
WIFI_SSID=""
WIFI_PASS=""
WOLFSSL_GROUP="ML_KEM_512"
SIG="ML_DSA_44"
ITERATIONS="50"
WARMUP="3"
INTER_MS="1000"
NETWORK_INJECT=false
DISABLE_LOCAL_REFILL=false
NO_VERIFY=false
DO_CLEAN=false
DO_INIT=false
DO_FLASH=false
DO_MONITOR=false
PATCH_ONLY=false
WEST_WORKSPACE_DIR="$REPO_ROOT/.west-entropy-renewal"
WEST_MANIFEST_DIR="$WEST_WORKSPACE_DIR/manifest"

ENV_FILE="$PROJECT_ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

usage() {
    cat <<EOF
Usage: $0 [options]

Required:
  --wifi-ssid <ssid>
  --wifi-pass <pass>

Common:
  --dtls-ip <ip>            DTLS entropy server IP (default: 192.168.1.136)
  --dtls-port <port>        DTLS entropy server UDP port (default: 5684)
  --dtls-bytes <n>          Raw entropy bytes requested per session (default: 32)
  --groups <name>           wolfSSL group, e.g. ML_KEM_512 or P-256
  --sig <name>              ECDSA_P256 or ML_DSA_44
  --iterations <n>          Measured fresh handshakes (default: 50)
  --warmup <n>              Unlogged warm-up handshakes (default: 3)
  --inter-ms <n>            Delay between fresh handshakes (default: 1000)
  --network-inject          Mix accepted DTLS entropy payload after each response
  --disable-local-refill    Disable local HW refills after bootstrap
  --no-verify               Disable DTLS certificate verification

Build:
  --qeaas-client-root <dir> Path to a QEaaS ESP32 client checkout
  --clean                   Remove build directory before build
  --init                    west init/update only
  --patch-only              Apply Zephyr instrumentation patch and exit
  --flash                   Flash after build
  --monitor                 Open ESP32 serial monitor after flash
  --west-workspace <dir>    Dependency workspace (default: repo .west-entropy-renewal)
  -h|--help                 Show this help

Examples:
  $0 --wifi-ssid SSID --wifi-pass PASS --clean --flash --monitor
  $0 --wifi-ssid SSID --wifi-pass PASS --groups P-256 --sig ECDSA_P256
  $0 --wifi-ssid SSID --wifi-pass PASS --network-inject --iterations 20
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --qeaas-client-root) QEAAS_CLIENT_ROOT="$2"; shift 2 ;;
        --dtls-ip|--coap-ip) DTLS_IP="$2"; shift 2 ;;
        --dtls-port|--coap-port) DTLS_PORT="$2"; shift 2 ;;
        --dtls-bytes|--qeaas-bytes) DTLS_ENTROPY_BYTES="$2"; shift 2 ;;
        --wifi-ssid) WIFI_SSID="$2"; shift 2 ;;
        --wifi-pass) WIFI_PASS="$2"; shift 2 ;;
        --groups) WOLFSSL_GROUP="$2"; shift 2 ;;
        --sig) SIG="$2"; shift 2 ;;
        --iterations) ITERATIONS="$2"; shift 2 ;;
        --warmup) WARMUP="$2"; shift 2 ;;
        --inter-ms) INTER_MS="$2"; shift 2 ;;
        --network-inject) NETWORK_INJECT=true; shift ;;
        --disable-local-refill) DISABLE_LOCAL_REFILL=true; shift ;;
        --no-verify) NO_VERIFY=true; shift ;;
        --clean) DO_CLEAN=true; shift ;;
        --init) DO_INIT=true; shift ;;
        --patch-only) PATCH_ONLY=true; shift ;;
        --flash) DO_FLASH=true; shift ;;
        --monitor) DO_MONITOR=true; shift ;;
        --west-workspace) WEST_WORKSPACE_DIR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

if [[ "$DO_INIT" != true && "$PATCH_ONLY" != true && \
      ( -z "$WIFI_SSID" || -z "$WIFI_PASS" ) ]]; then
    echo "Missing Wi-Fi credentials. Use --wifi-ssid/--wifi-pass or $PROJECT_ROOT/.env"
    exit 1
fi

for n in "$DTLS_ENTROPY_BYTES" "$ITERATIONS" "$WARMUP" "$INTER_MS"; do
    if ! [[ "$n" =~ ^[0-9]+$ ]]; then
        echo "Expected numeric option, got: $n"
        exit 1
    fi
done

if [[ ! -f "$QEAAS_CLIENT_ROOT/wolfssl/include/config-wolfssl-libcoap.h" ]]; then
    echo "Cannot find QEaaS wolfSSL client under: $QEAAS_CLIENT_ROOT"
    echo "Use --qeaas-client-root <path-to-qeaas_esp32_client>"
    exit 1
fi

init_workspace() {
    WEST_WORKSPACE_DIR="$(realpath -m "$WEST_WORKSPACE_DIR")"
    WEST_MANIFEST_DIR="$WEST_WORKSPACE_DIR/manifest"

    mkdir -p "$WEST_MANIFEST_DIR"
    sed 's|path: \.$|path: manifest|' \
        "$PROJECT_ROOT/west.yml" > "$WEST_MANIFEST_DIR/west.yml"

    if [[ ! -f "$WEST_WORKSPACE_DIR/.west/config" ]]; then
        (cd "$WEST_WORKSPACE_DIR" && west init -l "$WEST_MANIFEST_DIR")
        if [[ ! -f "$WEST_WORKSPACE_DIR/.west/config" ]]; then
            echo "west init did not create expected workspace: $WEST_WORKSPACE_DIR"
            exit 1
        fi
        (cd "$WEST_WORKSPACE_DIR" && west config manifest.path manifest)
        (cd "$WEST_WORKSPACE_DIR" && west update)
    elif [[ "$DO_INIT" == true || ! -d "$WEST_WORKSPACE_DIR/zephyr" || \
            ! -d "$WEST_WORKSPACE_DIR/modules/crypto/wolfssl" ]]; then
        (cd "$WEST_WORKSPACE_DIR" && west update)
    else
        echo "dependency west workspace already present; skipping west update"
    fi

    cd "$WEST_WORKSPACE_DIR"
    west blobs fetch hal_espressif 2>/dev/null || true
    west zephyr-export || echo "warning: west zephyr-export failed; continuing with workspace-local Zephyr"
}

apply_zephyr_patch() {
    local zephyr_dir="$WEST_WORKSPACE_DIR/zephyr"
    local patch_file="$PROJECT_ROOT/patches/zephyr-028d194-blake2s-renewal-trace.patch"

    if [[ ! -d "$zephyr_dir" ]]; then
        echo "Zephyr tree not found at $zephyr_dir. Run with --init first."
        exit 1
    fi

    if git -C "$zephyr_dir" apply --check "$patch_file" >/dev/null 2>&1; then
        git -C "$zephyr_dir" apply "$patch_file"
        echo "Applied Zephyr renewal instrumentation patch."
    elif git -C "$zephyr_dir" apply --reverse --check "$patch_file" >/dev/null 2>&1; then
        echo "Zephyr renewal instrumentation patch already applied."
    else
        echo "Cannot apply Zephyr patch cleanly: $patch_file"
        echo "Check whether the Zephyr fork changed from the pinned commit."
        exit 1
    fi
}

if [[ "$DO_INIT" == true ]]; then
    init_workspace
    apply_zephyr_patch
    exit 0
fi

cd "$PROJECT_ROOT"
if [[ "$DO_CLEAN" == true ]]; then
    rm -rf build/
fi

init_workspace
apply_zephyr_patch

if [[ "$PATCH_ONLY" == true ]]; then
    exit 0
fi

export CCACHE_DIR="${CCACHE_DIR:-$WEST_WORKSPACE_DIR/.ccache}"
export CCACHE_TEMPDIR="${CCACHE_TEMPDIR:-/tmp/ccache-tmp}"
mkdir -p "$CCACHE_DIR" "$CCACHE_TEMPDIR"

if [[ "$NO_VERIFY" != true ]]; then
    CA_CERT_DIR="$QEAAS_CLIENT_ROOT/qeaas-server/coap2http-proxy/certs"
    CA_HEADER="$QEAAS_CLIENT_ROOT/wolfssl/include/ca_certs.h"
    if [[ -f "$CA_CERT_DIR/ecc/ca-ecc-cert.pem" && \
          -f "$CA_CERT_DIR/mldsa44/mldsa44_root_cert.pem" ]]; then
        "$QEAAS_CLIENT_ROOT/scripts/generate_ca_header.sh"
    elif [[ ! -f "$CA_HEADER" ]] || \
         grep -q 'ca_cert_.*_pem\[\] = ""' "$CA_HEADER"; then
        echo "Missing verified-DTLS CA certs under: $CA_CERT_DIR"
        echo "Cannot build with peer verification until qeaas-server certs are present."
        echo "Use --no-verify only for transport/RNG-path bring-up builds."
        exit 1
    else
        echo "CA cert source directory missing; reusing existing non-empty CA header: $CA_HEADER"
    fi
fi

export QEAAS_CLIENT_ROOT
export DTLS_IP DTLS_PORT DTLS_ENTROPY_BYTES WIFI_SSID WIFI_PASS
export WOLFSSL_GROUPS="$WOLFSSL_GROUP"
export WOLFSSL_SIG="$SIG"
export RENEWAL_ITERATIONS="$ITERATIONS"
export RENEWAL_WARMUP="$WARMUP"
export RENEWAL_INTER_HANDSHAKE_MS="$INTER_MS"
export RENEWAL_NETWORK_INJECT_EVERY="1"

if [[ "$NETWORK_INJECT" == true ]]; then
    export RENEWAL_ENABLE_NETWORK_INJECTION=1
else
    unset RENEWAL_ENABLE_NETWORK_INJECTION || true
fi

if [[ "$DISABLE_LOCAL_REFILL" == true ]]; then
    export RENEWAL_DISABLE_LOCAL_REFILL_AFTER_BOOTSTRAP=1
else
    unset RENEWAL_DISABLE_LOCAL_REFILL_AFTER_BOOTSTRAP || true
fi

if [[ "$NO_VERIFY" == true ]]; then
    export SKIP_PEER_VERIFY=1
else
    unset SKIP_PEER_VERIFY || true
fi

echo "============================================="
echo "Embedded Renewal Benchmark"
echo "  West workspace:        $WEST_WORKSPACE_DIR"
echo "  Group/signature:       $WOLFSSL_GROUP / $SIG"
echo "  DTLS server:           $DTLS_IP:$DTLS_PORT"
echo "  Entropy bytes/session: $DTLS_ENTROPY_BYTES"
echo "  Iterations/warmup:     $ITERATIONS / $WARMUP"
echo "  Inter-handshake delay: ${INTER_MS} ms"
echo "  Network injection:     $NETWORK_INJECT"
echo "  Local refill after boot: $([[ "$DISABLE_LOCAL_REFILL" == true ]] && echo disabled || echo enabled)"
echo "  Verify peer cert:      $([[ "$NO_VERIFY" == true ]] && echo disabled || echo enabled)"
echo "============================================="

(cd "$WEST_WORKSPACE_DIR" && west list zephyr wolfssl -f '  {name}: {revision} ({path})')

(cd "$WEST_WORKSPACE_DIR" && west build -p auto -d "$PROJECT_ROOT/build" -b "$BOARD_TARGET" "$PROJECT_ROOT")

if [[ "$DO_FLASH" == true ]]; then
    (cd "$WEST_WORKSPACE_DIR" && west flash -d "$PROJECT_ROOT/build")
fi
if [[ "$DO_MONITOR" == true ]]; then
    (cd "$WEST_WORKSPACE_DIR" && west espressif monitor -d "$PROJECT_ROOT/build")
fi
