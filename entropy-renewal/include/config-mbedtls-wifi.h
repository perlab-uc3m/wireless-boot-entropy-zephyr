/*
 * Minimal mbedTLS configuration for the ESP32 Wi-Fi driver. The benchmark
 * application itself uses wolfSSL directly for DTLS.
 */

#ifndef CONFIG_MBEDTLS_WIFI_H
#define CONFIG_MBEDTLS_WIFI_H

#ifndef MBEDTLS_AES_C
#define MBEDTLS_AES_C
#endif

#if defined(MBEDTLS_CCM_C)
#ifndef MBEDTLS_CCM_GCM_CAN_AES
#define MBEDTLS_CCM_GCM_CAN_AES
#endif
#endif

#ifndef MBEDTLS_MD_CAN_SHA256
#define MBEDTLS_MD_CAN_SHA256
#endif

#endif /* CONFIG_MBEDTLS_WIFI_H */
