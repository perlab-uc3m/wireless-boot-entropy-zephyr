/*
 * config-mbedtls-wifi.h
 * Minimal mbedTLS configuration for ESP32 WiFi driver (WPA2-Personal) ONLY.
 *
 * The application TLS/DTLS backend uses wolfSSL, NOT mbedTLS.
 * This config provides only the raw crypto primitives that the ESP-IDF
 * wpa_supplicant needs: AES, CCM, CMAC, SHA-256, HMAC, DES, bignum, NIST-KW.
 *
 * Copyright (C) 2006-2015, ARM Limited, All Rights Reserved
 * Copyright (c) 2017 Intel Corporation.
 * Copyright (c) 2018 Nordic Semiconductor ASA
 * Copyright (C) 2024-2025 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef CONFIG_MBEDTLS_WIFI_H
#define CONFIG_MBEDTLS_WIFI_H

/* ---- Crypto primitives used by wpa_supplicant ---- */

/* AES (ECB/CBC/CTR) - always needed for WPA2 CCMP */
#ifndef MBEDTLS_AES_C
#define MBEDTLS_AES_C
#endif

/* CCM mode - used by WPA2 CCMP */
#if defined(MBEDTLS_CCM_C)
#ifndef MBEDTLS_CCM_GCM_CAN_AES
#define MBEDTLS_CCM_GCM_CAN_AES
#endif
#endif

/* SHA-256 - needed for HMAC / key derivation in 4-way handshake */
#ifndef MBEDTLS_MD_CAN_SHA256
#define MBEDTLS_MD_CAN_SHA256
#endif

#endif /* CONFIG_MBEDTLS_WIFI_H */
