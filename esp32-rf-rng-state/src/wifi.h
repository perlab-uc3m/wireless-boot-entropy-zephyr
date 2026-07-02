/*
 * esp32-rf-rng-state/src/wifi.h
 *
 * Copyright (C) 2024-2026 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
 *
 * Wi-Fi management header for the RF-TRNG benchmark.
 * Adapted from the QEaaS ESP32 client wolfSSL Wi-Fi helper.
 */

#ifndef WIFI_H
#define WIFI_H

#include <zephyr/kernel.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/wifi_mgmt.h>

extern struct wifi_connect_req_params wifi_params;

void wifi_mgmt_event_handler(struct net_mgmt_event_callback *cb, uint32_t mgmt_event,
			     struct net_if *iface);
int wifi_init(struct device *unused);
int shell_cmd_scan(void);
int wait_for_wifi_connection(void);
int wifi_wait_for_ipv4(int timeout_ms);
int connect_to_wifi(void);
int wifi_reconnect(void);
void wifi_disconnect(void);
bool wifi_is_connected(void);
void wifi_set_event_logging(bool enabled);
bool wifi_event_logging_enabled(void);
void wifi_print_ipv4(void);

/**
 * Trigger a Wi-Fi scan (non-blocking).
 * Used by the wifi_scan condition to generate RF activity
 * without application-layer data transfer.
 */
int wifi_trigger_scan(void);

#endif /* WIFI_H */
