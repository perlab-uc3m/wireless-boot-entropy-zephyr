cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(entropy_renewal)

get_filename_component(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)

set(QEAAS_CLIENT_ROOT_VALUE "$ENV{QEAAS_CLIENT_ROOT}")
if(DEFINED QEAAS_CLIENT_ROOT)
    set(QEAAS_CLIENT_ROOT_VALUE "${QEAAS_CLIENT_ROOT}")
endif()
if(NOT QEAAS_CLIENT_ROOT_VALUE)
    set(QEAAS_CLIENT_ROOT_VALUE "${REPO_ROOT}/../qeaas_esp32_client")
endif()
get_filename_component(QEAAS_CLIENT_ROOT_VALUE "${QEAAS_CLIENT_ROOT_VALUE}" ABSOLUTE)
set(QEAAS_WOLFSSL_ROOT "${QEAAS_CLIENT_ROOT_VALUE}/wolfssl")

if(NOT EXISTS "${QEAAS_WOLFSSL_ROOT}/include/config-wolfssl-libcoap.h")
    message(FATAL_ERROR
        "Could not find QEaaS wolfSSL client headers at ${QEAAS_WOLFSSL_ROOT}. "
        "Set QEAAS_CLIENT_ROOT to a QEaaS ESP32 client checkout.")
endif()

set(DTLS_SERVER_IP_VALUE "$ENV{DTLS_IP}")
set(DTLS_SERVER_PORT_VALUE "$ENV{DTLS_PORT}")
set(DTLS_ENTROPY_BYTES_VALUE "$ENV{DTLS_ENTROPY_BYTES}")
set(WIFI_SSID_VALUE "$ENV{WIFI_SSID}")
set(WIFI_PASS_VALUE "$ENV{WIFI_PASS}")
set(WOLFSSL_GROUPS_VALUE "$ENV{WOLFSSL_GROUPS}")
set(WOLFSSL_SIG_VALUE "$ENV{WOLFSSL_SIG}")
set(RENEWAL_ITERATIONS_VALUE "$ENV{RENEWAL_ITERATIONS}")
set(RENEWAL_WARMUP_VALUE "$ENV{RENEWAL_WARMUP}")
set(RENEWAL_INTER_HANDSHAKE_MS_VALUE "$ENV{RENEWAL_INTER_HANDSHAKE_MS}")
set(RENEWAL_NETWORK_INJECT_EVERY_VALUE "$ENV{RENEWAL_NETWORK_INJECT_EVERY}")

if("${DTLS_SERVER_IP_VALUE}" STREQUAL "")
    set(DTLS_SERVER_IP_VALUE "$ENV{COAP_IP}")
endif()
if("${DTLS_SERVER_PORT_VALUE}" STREQUAL "")
    set(DTLS_SERVER_PORT_VALUE "$ENV{COAP_PORT}")
endif()
if("${WOLFSSL_GROUPS_VALUE}" STREQUAL "")
    set(WOLFSSL_GROUPS_VALUE "$ENV{COAP_WOLFSSL_GROUPS}")
endif()
if("${WOLFSSL_SIG_VALUE}" STREQUAL "")
    set(WOLFSSL_SIG_VALUE "$ENV{COAP_WOLFSSL_SIG}")
endif()
if("${WOLFSSL_GROUPS_VALUE}" STREQUAL "")
    set(WOLFSSL_GROUPS_VALUE "P-256")
endif()
if("${WOLFSSL_SIG_VALUE}" STREQUAL "")
    set(WOLFSSL_SIG_VALUE "ECDSA_P256")
endif()
if("${DTLS_SERVER_IP_VALUE}" STREQUAL "")
    set(DTLS_SERVER_IP_VALUE "192.168.1.136")
endif()
if("${DTLS_SERVER_PORT_VALUE}" STREQUAL "")
    set(DTLS_SERVER_PORT_VALUE "5684")
endif()
if("${DTLS_ENTROPY_BYTES_VALUE}" STREQUAL "")
    set(DTLS_ENTROPY_BYTES_VALUE "32")
endif()
if("${WIFI_SSID_VALUE}" STREQUAL "")
    set(WIFI_SSID_VALUE "WIFI_SSID_NOT_SET")
endif()
if("${WIFI_PASS_VALUE}" STREQUAL "")
    set(WIFI_PASS_VALUE "WIFI_PASS_NOT_SET")
endif()
if("${RENEWAL_ITERATIONS_VALUE}" STREQUAL "")
    set(RENEWAL_ITERATIONS_VALUE "50")
endif()
if("${RENEWAL_WARMUP_VALUE}" STREQUAL "")
    set(RENEWAL_WARMUP_VALUE "3")
endif()
if("${RENEWAL_INTER_HANDSHAKE_MS_VALUE}" STREQUAL "")
    set(RENEWAL_INTER_HANDSHAKE_MS_VALUE "1000")
endif()
if("${RENEWAL_NETWORK_INJECT_EVERY_VALUE}" STREQUAL "")
    set(RENEWAL_NETWORK_INJECT_EVERY_VALUE "1")
endif()

zephyr_include_directories(
    include
    ${QEAAS_WOLFSSL_ROOT}/include
)

target_sources(app PRIVATE
    src/main.c
    src/renewal_rng_wrap.c
    ${QEAAS_WOLFSSL_ROOT}/src/wifi.c
)

target_include_directories(app PRIVATE
    include
    ${QEAAS_WOLFSSL_ROOT}/include
)

# Linker wrapping keeps wolfSSL v5.8.2-stable unmodified while counting the
# RNG-output bytes returned by the wolfCrypt API.
target_link_options(app PRIVATE
    -Wl,--wrap=wc_RNG_GenerateBlock
    -Wl,--wrap=wc_RNG_GenerateByte
)

target_compile_definitions(app PRIVATE
    DTLS_SERVER_IP="${DTLS_SERVER_IP_VALUE}"
    DTLS_SERVER_PORT=${DTLS_SERVER_PORT_VALUE}
    DTLS_ENTROPY_BYTES=${DTLS_ENTROPY_BYTES_VALUE}
    WOLFSSL_GROUP_NAME="${WOLFSSL_GROUPS_VALUE}"
    WIFI_SSID="${WIFI_SSID_VALUE}"
    WIFI_PASS="${WIFI_PASS_VALUE}"
    RENEWAL_ITERATIONS=${RENEWAL_ITERATIONS_VALUE}
    RENEWAL_WARMUP=${RENEWAL_WARMUP_VALUE}
    RENEWAL_INTER_HANDSHAKE_MS=${RENEWAL_INTER_HANDSHAKE_MS_VALUE}
    RENEWAL_NETWORK_INJECT_EVERY=${RENEWAL_NETWORK_INJECT_EVERY_VALUE}
)

if(DEFINED ENV{RENEWAL_ENABLE_NETWORK_INJECTION})
    target_compile_definitions(app PRIVATE RENEWAL_ENABLE_NETWORK_INJECTION=1)
endif()

if(DEFINED ENV{RENEWAL_DISABLE_LOCAL_REFILL_AFTER_BOOTSTRAP})
    target_compile_definitions(app PRIVATE RENEWAL_DISABLE_LOCAL_REFILL_AFTER_BOOTSTRAP=1)
endif()

if(DEFINED ENV{SKIP_PEER_VERIFY})
    target_compile_definitions(app PRIVATE SKIP_PEER_VERIFY=1)
    set(_VERIFY_PEER "OFF")
else()
    set(_VERIFY_PEER "ON")
endif()

if(WOLFSSL_GROUPS_VALUE)
    if(WOLFSSL_GROUPS_VALUE MATCHES "ML_KEM_1024")
        set(_WOLFSSL_ALGO "MLKEM1024")
    elseif(WOLFSSL_GROUPS_VALUE MATCHES "ML_KEM_768")
        set(_WOLFSSL_ALGO "MLKEM768")
    elseif(WOLFSSL_GROUPS_VALUE MATCHES "ML_KEM_512")
        set(_WOLFSSL_ALGO "MLKEM512")
    elseif(WOLFSSL_GROUPS_VALUE MATCHES "X25519")
        set(_WOLFSSL_ALGO "X25519")
    else()
        set(_WOLFSSL_ALGO "ECDHE_P256")
    endif()
else()
    set(_WOLFSSL_ALGO "ECDHE_P256")
endif()

zephyr_compile_definitions(WOLFSSL_ALGO_${_WOLFSSL_ALGO}=1)

if(WOLFSSL_SIG_VALUE MATCHES "ML_DSA_44")
    set(_WOLFSSL_SIG "MLDSA44")
else()
    set(_WOLFSSL_SIG "ECDSA_P256")
endif()
zephyr_compile_definitions(WOLFSSL_SIG_${_WOLFSSL_SIG}=1)

message(STATUS "...............................................")
message(STATUS "Embedded renewal benchmark")
message(STATUS "QEaaS client root:       ${QEAAS_CLIENT_ROOT_VALUE}")
message(STATUS "DTLS server:             ${DTLS_SERVER_IP_VALUE}:${DTLS_SERVER_PORT_VALUE}")
message(STATUS "DTLS entropy bytes:      ${DTLS_ENTROPY_BYTES_VALUE}")
message(STATUS "wolfSSL group string:    ${WOLFSSL_GROUPS_VALUE}")
message(STATUS "wolfSSL key exchange:    ${_WOLFSSL_ALGO}")
message(STATUS "wolfSSL signature:       ${_WOLFSSL_SIG}")
message(STATUS "Verify peer cert:        ${_VERIFY_PEER}")
message(STATUS "Iterations/warmup:       ${RENEWAL_ITERATIONS_VALUE}/${RENEWAL_WARMUP_VALUE}")
message(STATUS "Inter-handshake delay:   ${RENEWAL_INTER_HANDSHAKE_MS_VALUE} ms")
message(STATUS "Wi-Fi network:           ${WIFI_SSID_VALUE}")
message(STATUS "...............................................")
