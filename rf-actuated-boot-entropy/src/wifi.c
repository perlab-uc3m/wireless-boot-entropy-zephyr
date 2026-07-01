/*
 * Wi-Fi management for RF-actuated boot entropy.
 */

#include <errno.h>
#include <stdio.h>
#include <zephyr/kernel.h>
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

static struct wifi_connect_req_params wifi_params = {.ssid = WIFI_SSID,
						     .ssid_length = sizeof(WIFI_SSID) - 1,
						     .psk = WIFI_PASS,
						     .psk_length = sizeof(WIFI_PASS) - 1,
						     .band = WIFI_FREQ_BAND_2_4_GHZ,
						     .channel = WIFI_CHANNEL_ANY,
						     .security = WIFI_SECURITY_TYPE_PSK,
						     .mfp = WIFI_MFP_OPTIONAL,
						     .timeout = SYS_FOREVER_MS};

static union {
	struct {
		uint8_t connecting: 1;
		uint8_t disconnecting: 1;
		uint8_t _unused: 6;
	};
	uint8_t all;
} context;

static bool connected;
static bool event_logging = true;
static struct net_mgmt_event_callback wifi_event_cb;

static struct net_if *wifi_sta_iface(void)
{
	struct net_if *iface = net_if_get_wifi_sta();

	if (!iface) {
		printk("[AEB_WIFI] Failed to get Wi-Fi STA interface\n");
	}

	return iface;
}

static void handle_connect_result(struct net_mgmt_event_callback *cb)
{
	const struct wifi_status *status = (const struct wifi_status *)cb->info;

	if (status->status) {
		if (event_logging) {
			printk("[AEB_WIFI] Connection failed (%d)\n", status->status);
		}
	} else {
		connected = true;
		if (event_logging) {
			printk("[AEB_WIFI] Connected\n");
		}
	}

	context.connecting = false;
}

static void handle_disconnect_result(struct net_mgmt_event_callback *cb)
{
	const struct wifi_status *status = (const struct wifi_status *)cb->info;

	connected = false;

	if (event_logging) {
		printk("[AEB_WIFI] Disconnected (%d)\n", status->status);
	}

	context.disconnecting = false;
}

static void wifi_mgmt_event_handler(struct net_mgmt_event_callback *cb, uint32_t mgmt_event,
				    struct net_if *iface)
{
	ARG_UNUSED(iface);

	switch (mgmt_event) {
	case NET_EVENT_WIFI_CONNECT_RESULT:
		handle_connect_result(cb);
		break;
	case NET_EVENT_WIFI_DISCONNECT_RESULT:
		handle_disconnect_result(cb);
		break;
	default:
		break;
	}
}

int wifi_init(struct device *unused)
{
	ARG_UNUSED(unused);

	context.all = 0;
	connected = false;

	net_mgmt_init_event_callback(&wifi_event_cb, wifi_mgmt_event_handler,
				     NET_EVENT_WIFI_CONNECT_RESULT |
					     NET_EVENT_WIFI_DISCONNECT_RESULT);
	net_mgmt_add_event_callback(&wifi_event_cb);

	printk("[AEB_WIFI] Event callback initialized\n");
	return 0;
}

static int wait_for_connection(void)
{
	int waited_ms = 0;

	while (!connected && waited_ms < WIFI_CONNECTION_TIMEOUT_MS) {
		k_sleep(K_MSEC(100));
		waited_ms += 100;
	}

	if (!connected) {
		printk("[AEB_WIFI] Connection timeout after %d ms\n", waited_ms);
		return -ETIMEDOUT;
	}

	return 0;
}

static bool wifi_has_ipv4(void)
{
	struct net_if *iface = wifi_sta_iface();

	if (!iface) {
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
		printk("[AEB_WIFI] IPv4 timeout after %d ms\n", waited_ms);
		return -ETIMEDOUT;
	}

	wifi_print_ipv4();
	return 0;
}

int connect_to_wifi(void)
{
	struct net_if *iface = wifi_sta_iface();
	int last_ret = -ETIMEDOUT;

	if (!iface) {
		return -ENODEV;
	}

	for (int attempt = 1; attempt <= WIFI_CONNECTION_ATTEMPTS; attempt++) {
		int ret;

		connected = false;
		context.connecting = true;

		printk("[AEB_WIFI] Connecting attempt %d/%d to ssid=%s\n", attempt,
		       WIFI_CONNECTION_ATTEMPTS, WIFI_SSID);

		ret = net_mgmt(NET_REQUEST_WIFI_CONNECT, iface, &wifi_params, sizeof(wifi_params));
		if (ret < 0) {
			printk("[AEB_WIFI] Connect request failed: %d\n", ret);
			context.connecting = false;
			last_ret = ret;
		} else {
			ret = wait_for_connection();
			if (ret == 0) {
				ret = wifi_wait_for_ipv4(WIFI_DHCP_TIMEOUT_MS);
				if (ret == 0) {
					return 0;
				}
				last_ret = ret;
			}
		}

		wifi_disconnect();
		k_sleep(K_MSEC(WIFI_RETRY_DELAY_MS));
	}

	return last_ret;
}

void wifi_disconnect(void)
{
	struct net_if *iface = wifi_sta_iface();

	if (!iface) {
		return;
	}

	context.disconnecting = true;
	if (net_mgmt(NET_REQUEST_WIFI_DISCONNECT, iface, NULL, 0) != 0) {
		context.disconnecting = false;
	}
}

bool wifi_is_connected(void)
{
	return connected;
}

void wifi_set_event_logging(bool enabled)
{
	event_logging = enabled;
}

void wifi_print_ipv4(void)
{
	struct net_if *iface = wifi_sta_iface();

	if (!iface) {
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
		printk("[AEB_WIFI] IPv4 %s\n", buf);
	}
}
