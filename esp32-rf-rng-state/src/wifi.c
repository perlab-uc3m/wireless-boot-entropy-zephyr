/*
 * esp32-rf-rng-state/src/wifi.c
 *
 * Copyright (C) 2024-2026 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
 *
 * Wi-Fi management for the RF-TRNG benchmark.
 * Adapted from the QEaaS ESP32 client wolfSSL Wi-Fi helper.
 */

#include <errno.h>
#include <stdio.h>
#include <zephyr/net/net_event.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/socket.h>
#include <zephyr/net/wifi_mgmt.h>
#include <zephyr/net/wifi_utils.h>
#include "wifi.h"

#ifndef WIFI_SSID
#define WIFI_SSID "WIFI_SSID_NOT_SET"
#endif

#ifndef WIFI_PASS
#define WIFI_PASS "WIFI_PASS_NOT_SET"
#endif

#define WIFI_CONNECTION_TIMEOUT_MS 20000
#define WIFI_CONNECTION_ATTEMPTS   5
#define WIFI_RETRY_DELAY_MS        2000
#define WIFI_DHCP_TIMEOUT_MS       20000

struct wifi_connect_req_params wifi_params = {.ssid = WIFI_SSID,
					      .ssid_length = sizeof(WIFI_SSID) - 1,
					      .psk = WIFI_PASS,
					      .psk_length = sizeof(WIFI_PASS) - 1,
					      .band = WIFI_FREQ_BAND_2_4_GHZ,
					      .channel = WIFI_CHANNEL_ANY,
					      .security = WIFI_SECURITY_TYPE_PSK,
					      .mfp = WIFI_MFP_OPTIONAL,
					      .timeout = SYS_FOREVER_MS};

#define WIFI_SHELL_MGMT_EVENTS                                                                     \
	(NET_EVENT_WIFI_SCAN_RESULT | NET_EVENT_WIFI_SCAN_DONE | NET_EVENT_WIFI_CONNECT_RESULT |   \
	 NET_EVENT_WIFI_DISCONNECT_RESULT)

static union {
	struct {
		uint8_t connecting: 1;
		uint8_t disconnecting: 1;
		uint8_t _unused: 6;
	};
	uint8_t all;
} context;

static uint32_t scan_result;
static bool wifi_connected = false;
static bool wifi_event_logging = true;
static struct net_mgmt_event_callback wifi_event_cb;

static struct net_if *wifi_sta_iface(void)
{
	struct net_if *iface = net_if_get_wifi_sta();

	if (!iface) {
		printk("Failed to get Wi-Fi STA interface\n");
	}

	return iface;
}

static void handle_wifi_scan_result(struct net_mgmt_event_callback *cb)
{
	const struct wifi_scan_result *entry = (const struct wifi_scan_result *)cb->info;

	scan_result++;

	if (!wifi_event_logging) {
		return;
	}

	if (scan_result == 1) {
		printk("\n%-4s | %-32s %-5s | %-4s | %-4s | %-5s\n", "Num", "SSID", "(len)", "Chan",
		       "RSSI", "Sec");
	}

	printk("%-4d | %-32s %-5u | %-4u | %-4d | %-5s\n", scan_result, entry->ssid,
	       entry->ssid_length, entry->channel, entry->rssi,
	       (entry->security == WIFI_SECURITY_TYPE_PSK ? "WPA/WPA2" : "Open"));
}

static void handle_wifi_scan_done(struct net_mgmt_event_callback *cb)
{
	const struct wifi_status *status = (const struct wifi_status *)cb->info;

	if (status->status) {
		if (wifi_event_logging) {
			printk("\nWi-Fi scan request failed (%d)\n", status->status);
		}
	} else {
		if (wifi_event_logging) {
			printk("Wi-Fi scan done (%u results)\n", scan_result);
		}
	}
	scan_result = 0;
}

static void handle_wifi_connect_result(struct net_mgmt_event_callback *cb)
{
	const struct wifi_status *status = (const struct wifi_status *)cb->info;

	if (status->status) {
		if (wifi_event_logging) {
			printk("\nWi-Fi connection failed (%d)\n", status->status);
		}
	} else {
		if (wifi_event_logging) {
			printk("\nWi-Fi connected\n");
		}
		wifi_connected = true;
	}
	context.connecting = false;
}

static void handle_wifi_disconnect_result(struct net_mgmt_event_callback *cb)
{
	const struct wifi_status *status = (const struct wifi_status *)cb->info;

	wifi_connected = false;

	if (context.disconnecting) {
		if (wifi_event_logging) {
			printk("\nWi-Fi disconnection %s (%d)\n",
			       status->status ? "failed" : "done", status->status);
		}
		context.disconnecting = false;
	} else {
		if (wifi_event_logging) {
			printk("\nWi-Fi Disconnected\n");
		}
	}
}

void wifi_set_event_logging(bool enabled)
{
	wifi_event_logging = enabled;
}

bool wifi_event_logging_enabled(void)
{
	return wifi_event_logging;
}

bool wifi_is_connected(void)
{
	return wifi_connected;
}

static bool wifi_has_ipv4(void)
{
	struct net_if *iface = wifi_sta_iface();

	if (!iface || !iface->config.ip.ipv4) {
		return false;
	}

	for (int i = 0; i < NET_IF_MAX_IPV4_ADDR; i++) {
		struct net_if_addr *ifaddr = &iface->config.ip.ipv4->unicast[i].ipv4;

		if (!ifaddr->is_used || ifaddr->addr_type == NET_ADDR_ANY ||
		    ifaddr->address.family != AF_INET) {
			continue;
		}

		return true;
	}

	return false;
}

int wifi_wait_for_ipv4(int timeout_ms)
{
	int waited_ms = 0;

	while (!wifi_has_ipv4() && waited_ms < timeout_ms) {
		k_sleep(K_MSEC(250));
		waited_ms += 250;
	}

	if (!wifi_has_ipv4()) {
		printk("[RF_WIFI] IPv4 timeout after %d ms\n", waited_ms);
		return -ETIMEDOUT;
	}

	wifi_print_ipv4();
	return 0;
}

void wifi_mgmt_event_handler(struct net_mgmt_event_callback *cb, uint32_t mgmt_event,
			     struct net_if *iface)
{
	switch (mgmt_event) {
	case NET_EVENT_WIFI_SCAN_RESULT:
		handle_wifi_scan_result(cb);
		break;
	case NET_EVENT_WIFI_SCAN_DONE:
		handle_wifi_scan_done(cb);
		break;
	case NET_EVENT_WIFI_CONNECT_RESULT:
		handle_wifi_connect_result(cb);
		break;
	case NET_EVENT_WIFI_DISCONNECT_RESULT:
		handle_wifi_disconnect_result(cb);
		break;
	default:
		break;
	}
}

int wifi_init(struct device *unused)
{
	ARG_UNUSED(unused);

	context.all = 0;
	scan_result = 0;

	net_mgmt_init_event_callback(&wifi_event_cb, wifi_mgmt_event_handler,
				     WIFI_SHELL_MGMT_EVENTS);

	printk("Wi-Fi event callback initialized\n");
	net_mgmt_add_event_callback(&wifi_event_cb);

	return 0;
}

int wait_for_wifi_connection(void)
{
	int timeout_count = 0;
	int max_timeout_count = WIFI_CONNECTION_TIMEOUT_MS / 100;

	while (!wifi_connected) {
		k_sleep(K_MSEC(100));
		timeout_count++;

		if (timeout_count >= max_timeout_count) {
			printk("Wi-Fi connection timeout after %d ms\n",
			       WIFI_CONNECTION_TIMEOUT_MS);
			return -ETIMEDOUT;
		}
	}

	printk("Wi-Fi connected successfully\n");
	return 0;
}

int connect_to_wifi(void)
{
	int last_ret = -ETIMEDOUT;

	struct net_if *iface = wifi_sta_iface();

	if (!iface) {
		return -ENODEV;
	}

	for (int attempt = 1; attempt <= WIFI_CONNECTION_ATTEMPTS; attempt++) {
		int ret;

		wifi_connected = false;
		context.connecting = true;

		printk("Connecting to Wi-Fi (attempt %d/%d, ssid=%s)...\n", attempt,
		       WIFI_CONNECTION_ATTEMPTS, WIFI_SSID);

		ret = net_mgmt(NET_REQUEST_WIFI_CONNECT, iface, &wifi_params,
			       sizeof(struct wifi_connect_req_params));

		if (ret < 0) {
			printk("Failed to request Wi-Fi connection: %d\n", ret);
			context.connecting = false;
			last_ret = ret;
		} else {
			printk("Wi-Fi connection requested\n");
			ret = wait_for_wifi_connection();
			if (ret == 0) {
				ret = wifi_wait_for_ipv4(WIFI_DHCP_TIMEOUT_MS);
				if (ret == 0) {
					return 0;
				}
			}
			last_ret = ret;
		}

		wifi_disconnect();
		k_sleep(K_MSEC(WIFI_RETRY_DELAY_MS));
	}

	printk("Wi-Fi failed after %d attempts\n", WIFI_CONNECTION_ATTEMPTS);
	return last_ret;
}

int wifi_reconnect(void)
{
	if (wifi_connected) {
		return 0;
	}

	printk("[WIFI] Reconnecting...\n");
	wifi_disconnect();
	k_sleep(K_MSEC(WIFI_RETRY_DELAY_MS));

	return connect_to_wifi();
}

void wifi_disconnect(void)
{
	struct net_if *iface = wifi_sta_iface();

	if (!iface) {
		return;
	}

	context.disconnecting = true;

	if (net_mgmt(NET_REQUEST_WIFI_DISCONNECT, iface, NULL, 0)) {
		printk("Wi-Fi disconnect failed\n");
		context.disconnecting = false;
	} else {
		printk("Wi-Fi disconnect requested\n");
	}
}

int wifi_trigger_scan(void)
{
	struct net_if *iface = wifi_sta_iface();

	if (!iface) {
		return -ENODEV;
	}

	if (net_mgmt(NET_REQUEST_WIFI_SCAN, iface, NULL, 0)) {
		printk("Wi-Fi scan request failed\n");
		return -1;
	}
	return 0;
}

void wifi_print_ipv4(void)
{
	struct net_if *iface = wifi_sta_iface();

	if (!iface || !iface->config.ip.ipv4) {
		return;
	}

	for (int i = 0; i < NET_IF_MAX_IPV4_ADDR; i++) {
		struct net_if_addr *ifaddr = &iface->config.ip.ipv4->unicast[i].ipv4;
		char buf[NET_IPV4_ADDR_LEN];

		if (!ifaddr->is_used || ifaddr->addr_type == NET_ADDR_ANY ||
		    ifaddr->address.family != AF_INET) {
			continue;
		}

		net_addr_ntop(AF_INET, &ifaddr->address.in_addr, buf, sizeof(buf));
		printk("[RF_WIFI] IPv4 %s\n", buf);
	}
}
