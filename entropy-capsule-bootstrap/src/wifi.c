#include "wifi.h"

#include <errno.h>
#include <stdio.h>

#include <zephyr/net/net_event.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/net_ip.h>
#include <zephyr/net/socket.h>
#include <zephyr/net/wifi_mgmt.h>
#include <zephyr/net/wifi_utils.h>

#ifndef WIFI_SSID
#define WIFI_SSID "WIFI_SSID_NOT_SET"
#endif

#ifndef WIFI_PASS
#define WIFI_PASS "WIFI_PASS_NOT_SET"
#endif

#define WIFI_CONNECTION_TIMEOUT_MS 20000
#define WIFI_CONNECTION_ATTEMPTS   5
#define WIFI_RETRY_DELAY_MS        2000

struct wifi_connect_req_params wifi_params = {.ssid = WIFI_SSID,
					      .ssid_length = sizeof(WIFI_SSID) - 1,
					      .psk = WIFI_PASS,
					      .psk_length = sizeof(WIFI_PASS) - 1,
					      .band = WIFI_FREQ_BAND_2_4_GHZ,
					      .channel = WIFI_CHANNEL_ANY,
					      .security = WIFI_SECURITY_TYPE_PSK,
					      .mfp = WIFI_MFP_OPTIONAL,
					      .timeout = SYS_FOREVER_MS};

#define WIFI_EVENTS (NET_EVENT_WIFI_CONNECT_RESULT | NET_EVENT_WIFI_DISCONNECT_RESULT)

static bool wifi_connected;
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

static void wifi_event_handler(struct net_mgmt_event_callback *cb, uint32_t mgmt_event,
			       struct net_if *iface)
{
	const struct wifi_status *status = (const struct wifi_status *)cb->info;

	ARG_UNUSED(iface);

	switch (mgmt_event) {
	case NET_EVENT_WIFI_CONNECT_RESULT:
		if (status->status) {
			if (wifi_event_logging) {
				printk("Wi-Fi connection failed (%d)\n", status->status);
			}
			wifi_connected = false;
		} else {
			if (wifi_event_logging) {
				printk("Wi-Fi connected\n");
			}
			wifi_connected = true;
		}
		break;
	case NET_EVENT_WIFI_DISCONNECT_RESULT:
		wifi_connected = false;
		if (wifi_event_logging) {
			printk("Wi-Fi disconnected\n");
		}
		break;
	default:
		break;
	}
}

int wifi_init(struct device *unused)
{
	ARG_UNUSED(unused);

	wifi_connected = false;
	net_mgmt_init_event_callback(&wifi_event_cb, wifi_event_handler, WIFI_EVENTS);
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
			printk("Wi-Fi connection timeout\n");
			return -ETIMEDOUT;
		}
	}

	return 0;
}

int wait_for_ipv4_address(void)
{
	struct net_if *iface = wifi_sta_iface();
	char addr_buf[NET_IPV4_ADDR_LEN];
	char gw_buf[NET_IPV4_ADDR_LEN];

	if (!iface) {
		return -ENODEV;
	}

	for (int i = 0; i < 100; i++) {
		struct in_addr *addr = net_if_ipv4_get_global_addr(iface, NET_ADDR_PREFERRED);

		if (addr != NULL && addr->s_addr != 0) {
			struct in_addr gw = net_if_ipv4_get_gw(iface);

			net_addr_ntop(AF_INET, addr, addr_buf, sizeof(addr_buf));
			net_addr_ntop(AF_INET, &gw, gw_buf, sizeof(gw_buf));
			printk("IPv4 ready: addr=%s gw=%s\n", addr_buf, gw_buf);
			return 0;
		}

		k_sleep(K_MSEC(100));
	}

	printk("IPv4 address timeout\n");
	return -ETIMEDOUT;
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

		wifi_connected = false;
		printk("Connecting to Wi-Fi (attempt %d/%d, ssid=%s)\n", attempt,
		       WIFI_CONNECTION_ATTEMPTS, WIFI_SSID);

		ret = net_mgmt(NET_REQUEST_WIFI_CONNECT, iface, &wifi_params, sizeof(wifi_params));
		if (ret < 0) {
			last_ret = ret;
		} else {
			ret = wait_for_wifi_connection();
			if (ret == 0) {
				return 0;
			}
			last_ret = ret;
		}

		wifi_disconnect();
		k_sleep(K_MSEC(WIFI_RETRY_DELAY_MS));
	}

	return last_ret;
}

void wifi_disconnect(void)
{
	struct net_if *iface = wifi_sta_iface();

	if (iface != NULL) {
		(void)net_mgmt(NET_REQUEST_WIFI_DISCONNECT, iface, NULL, 0);
	}
}

bool wifi_is_connected(void)
{
	return wifi_connected;
}

void wifi_set_event_logging(bool enabled)
{
	wifi_event_logging = enabled;
}
