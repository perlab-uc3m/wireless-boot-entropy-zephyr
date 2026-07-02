#
# esp32-rf-rng-state/CMakeLists.txt
#
# Copyright (C) 2026 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
#
# RF-TRNG Benchmark: measures ESP32 TRNG behaviour under different
# RF subsystem operating modes.
#

cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(esp32_rf_rng_state)

# -- Benchmark condition ------------------------------------------
# BENCH_CONDITION selects the RF operating mode at compile time.
# Valid values: RF_DISABLED, WIFI_IDLE, WIFI_SCAN, UDP_BURST, WIFI_TRAFFIC
set(BENCH_CONDITION_VALUE $ENV{BENCH_CONDITION})
if(DEFINED BENCH_CONDITION)
    set(BENCH_CONDITION_VALUE ${BENCH_CONDITION})
endif()

if(NOT BENCH_CONDITION_VALUE)
    set(BENCH_CONDITION_VALUE "RF_DISABLED")
endif()

set(VALID_BENCH_CONDITIONS RF_DISABLED WIFI_IDLE WIFI_SCAN UDP_BURST WIFI_TRAFFIC)
list(FIND VALID_BENCH_CONDITIONS "${BENCH_CONDITION_VALUE}" BENCH_CONDITION_INDEX)
if(BENCH_CONDITION_INDEX EQUAL -1)
    message(FATAL_ERROR
        "Invalid BENCH_CONDITION=${BENCH_CONDITION_VALUE}. "
        "Valid values: RF_DISABLED, WIFI_IDLE, WIFI_SCAN, UDP_BURST")
endif()

# -- Wi-Fi credentials (conditions 2-4) --------------------------
set(WIFI_SSID_VALUE $ENV{WIFI_SSID})
set(WIFI_PASS_VALUE $ENV{WIFI_PASS})
if(DEFINED WIFI_SSID)
    set(WIFI_SSID_VALUE ${WIFI_SSID})
endif()
if(DEFINED WIFI_PASS)
    set(WIFI_PASS_VALUE ${WIFI_PASS})
endif()
if(NOT WIFI_SSID_VALUE)
    set(WIFI_SSID_VALUE "WIFI_SSID_NOT_SET")
endif()
if(NOT WIFI_PASS_VALUE)
    set(WIFI_PASS_VALUE "WIFI_PASS_NOT_SET")
endif()

# -- UDP burst receiver (condition 4) ----------------------------
set(UDP_BURST_PORT_VALUE $ENV{UDP_BURST_PORT})
if(DEFINED UDP_BURST_PORT)
    set(UDP_BURST_PORT_VALUE ${UDP_BURST_PORT})
endif()
if(DEFINED UDP_TARGET_PORT)
    set(UDP_BURST_PORT_VALUE ${UDP_TARGET_PORT})
endif()
if(NOT UDP_BURST_PORT_VALUE)
    set(UDP_BURST_PORT_VALUE "9999")
endif()

set(UDP_BURST_PAYLOAD_SIZE_VALUE $ENV{UDP_BURST_PAYLOAD_SIZE})
if(DEFINED UDP_BURST_PAYLOAD_SIZE)
    set(UDP_BURST_PAYLOAD_SIZE_VALUE ${UDP_BURST_PAYLOAD_SIZE})
endif()
if(NOT UDP_BURST_PAYLOAD_SIZE_VALUE)
    set(UDP_BURST_PAYLOAD_SIZE_VALUE "64")
endif()

set(UDP_BURST_EXPECT_BYTE_VALUE $ENV{UDP_BURST_EXPECT_BYTE})
if(DEFINED UDP_BURST_EXPECT_BYTE)
    set(UDP_BURST_EXPECT_BYTE_VALUE ${UDP_BURST_EXPECT_BYTE})
endif()
if(NOT UDP_BURST_EXPECT_BYTE_VALUE)
    set(UDP_BURST_EXPECT_BYTE_VALUE "0x42")
endif()

# -- Raw dump size -----------------------------------------------
set(TRNG_RAW_DUMP_BYTES_VALUE $ENV{TRNG_RAW_DUMP_BYTES})
if(DEFINED TRNG_RAW_DUMP_BYTES)
    set(TRNG_RAW_DUMP_BYTES_VALUE ${TRNG_RAW_DUMP_BYTES})
endif()
if(NOT TRNG_RAW_DUMP_BYTES_VALUE)
    set(TRNG_RAW_DUMP_BYTES_VALUE "268435456")
endif()

set(TRNG_RAW_START_DELAY_MS_VALUE $ENV{TRNG_RAW_START_DELAY_MS})
if(DEFINED TRNG_RAW_START_DELAY_MS)
    set(TRNG_RAW_START_DELAY_MS_VALUE ${TRNG_RAW_START_DELAY_MS})
endif()
if(NOT TRNG_RAW_START_DELAY_MS_VALUE)
    set(TRNG_RAW_START_DELAY_MS_VALUE "15000")
endif()

# -- Include paths ------------------------------------------------
zephyr_include_directories(include)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)

# -- Sources ------------------------------------------------------
target_sources(app PRIVATE
    src/main.c
    src/trng_bench.c
)

# Conditionally add Wi-Fi and UDP sources
if(NOT BENCH_CONDITION_VALUE STREQUAL "RF_DISABLED")
    target_sources(app PRIVATE src/wifi.c)
endif()

if(BENCH_CONDITION_VALUE STREQUAL "WIFI_TRAFFIC" OR BENCH_CONDITION_VALUE STREQUAL "UDP_BURST")
    target_sources(app PRIVATE src/udp_flood.c)
endif()

# -- Compile definitions -----------------------------------------
target_compile_definitions(app PRIVATE
    BENCH_CONDITION_${BENCH_CONDITION_VALUE}=1
    WIFI_SSID="${WIFI_SSID_VALUE}"
    WIFI_PASS="${WIFI_PASS_VALUE}"
    UDP_BURST_PORT=${UDP_BURST_PORT_VALUE}
    UDP_BURST_PAYLOAD_SIZE=${UDP_BURST_PAYLOAD_SIZE_VALUE}
    UDP_BURST_EXPECT_BYTE=${UDP_BURST_EXPECT_BYTE_VALUE}
    TRNG_RAW_DUMP_BYTES=${TRNG_RAW_DUMP_BYTES_VALUE}ULL
    TRNG_RAW_START_DELAY_MS=${TRNG_RAW_START_DELAY_MS_VALUE}
)

message(STATUS "...............................................")
message(STATUS "RF-TRNG Benchmark")
message(STATUS "  Condition:    ${BENCH_CONDITION_VALUE}")
message(STATUS "  Raw bytes:    ${TRNG_RAW_DUMP_BYTES_VALUE}")
message(STATUS "  Raw delay ms: ${TRNG_RAW_START_DELAY_MS_VALUE}")
if(NOT BENCH_CONDITION_VALUE STREQUAL "RF_DISABLED")
    message(STATUS "  WiFi SSID:    ${WIFI_SSID_VALUE}")
endif()
if(BENCH_CONDITION_VALUE STREQUAL "WIFI_TRAFFIC" OR BENCH_CONDITION_VALUE STREQUAL "UDP_BURST")
    message(STATUS "  UDP burst:    port ${UDP_BURST_PORT_VALUE}, ${UDP_BURST_PAYLOAD_SIZE_VALUE} bytes, byte ${UDP_BURST_EXPECT_BYTE_VALUE}")
endif()
message(STATUS "...............................................") 
