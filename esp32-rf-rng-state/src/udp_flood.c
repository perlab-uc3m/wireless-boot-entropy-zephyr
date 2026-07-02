/*
 * Deterministic UDP-burst receiver for the wifi_traffic/udp_burst condition.
 */

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/net/socket.h>

#include "trng_bench.h"
#include "udp_flood.h"

#define UDP_BURST_STACK_SIZE 4096
#define UDP_BURST_PRIORITY   0

static K_THREAD_STACK_DEFINE(udp_burst_stack, UDP_BURST_STACK_SIZE);
static struct k_thread udp_burst_thread;
static volatile bool udp_burst_running;

static volatile uint32_t udp_rx_packets;
static volatile uint32_t udp_rx_bad_size;
static volatile uint32_t udp_rx_bad_payload;
static volatile uint32_t udp_rx_errors;
static volatile uint64_t udp_rx_bytes;
static volatile uint32_t udp_first_rx_ms;
static volatile uint32_t udp_last_rx_ms;

static bool payload_is_expected(const uint8_t *buf, size_t len)
{
	for (size_t i = 0; i < len; i++) {
		if (buf[i] != (uint8_t)UDP_BURST_EXPECT_BYTE) {
			return false;
		}
	}

	return true;
}

static void udp_burst_thread_fn(void *p1, void *p2, void *p3)
{
	ARG_UNUSED(p1);
	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	int sock;
	struct sockaddr_in bind_addr;
	uint8_t rx_buf[UDP_BURST_PAYLOAD_SIZE + 64];

	sock = zsock_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
	if (sock < 0) {
		printk("[UDP_BURST] Socket creation failed: %d\n", sock);
		udp_burst_running = false;
		return;
	}

	memset(&bind_addr, 0, sizeof(bind_addr));
	bind_addr.sin_family = AF_INET;
	bind_addr.sin_addr.s_addr = htonl(INADDR_ANY);
	bind_addr.sin_port = htons(UDP_BURST_PORT);

	if (zsock_bind(sock, (struct sockaddr *)&bind_addr, sizeof(bind_addr)) < 0) {
		printk("[UDP_BURST] Bind failed on port %d\n", UDP_BURST_PORT);
		zsock_close(sock);
		udp_burst_running = false;
		return;
	}

	printk("[UDP_BURST] Listening on UDP/%d, expected %d B packets, byte=0x%02x\n",
	       UDP_BURST_PORT, UDP_BURST_PAYLOAD_SIZE, UDP_BURST_EXPECT_BYTE);

	while (udp_burst_running) {
		int ret = zsock_recv(sock, rx_buf, sizeof(rx_buf), ZSOCK_MSG_DONTWAIT);

		if (ret > 0) {
			uint32_t now = (uint32_t)k_uptime_get_32();

			if (udp_rx_packets == 0) {
				udp_first_rx_ms = now;
			}
			udp_last_rx_ms = now;
			udp_rx_packets++;
			udp_rx_bytes += (uint32_t)ret;

			if (ret != UDP_BURST_PAYLOAD_SIZE) {
				udp_rx_bad_size++;
			} else if (!payload_is_expected(rx_buf, (size_t)ret)) {
				udp_rx_bad_payload++;
			}
			continue;
		}

		if (ret < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
			udp_rx_errors++;
		}

		k_sleep(K_MSEC(1));
	}

	zsock_close(sock);
	printk("[UDP_BURST] Stopped (RX: %u pkts, %llu bytes)\n", udp_rx_packets,
	       (unsigned long long)udp_rx_bytes);
}

int udp_flood_start(void)
{
	if (udp_burst_running) {
		return 0;
	}

	udp_rx_packets = 0;
	udp_rx_bad_size = 0;
	udp_rx_bad_payload = 0;
	udp_rx_errors = 0;
	udp_rx_bytes = 0;
	udp_first_rx_ms = 0;
	udp_last_rx_ms = 0;
	udp_burst_running = true;

	k_thread_create(&udp_burst_thread, udp_burst_stack, UDP_BURST_STACK_SIZE,
			udp_burst_thread_fn, NULL, NULL, NULL, UDP_BURST_PRIORITY, 0,
			K_NO_WAIT);
	k_thread_name_set(&udp_burst_thread, "udp_burst_rx");

	return 0;
}

void udp_flood_stop(void)
{
	if (!udp_burst_running) {
		return;
	}

	udp_burst_running = false;
	k_thread_join(&udp_burst_thread, K_SECONDS(5));
}

void udp_flood_report_stats(void)
{
	char buf[32];
	uint32_t span_ms = 0;

	if (udp_first_rx_ms != 0 && udp_last_rx_ms >= udp_first_rx_ms) {
		span_ms = udp_last_rx_ms - udp_first_rx_ms;
	}

	snprintf(buf, sizeof(buf), "%u", UDP_BURST_PORT);
	BENCH_META("udp_burst_port", buf);

	snprintf(buf, sizeof(buf), "%u", UDP_BURST_PAYLOAD_SIZE);
	BENCH_META("udp_burst_payload_bytes", buf);

	snprintf(buf, sizeof(buf), "0x%02x", UDP_BURST_EXPECT_BYTE);
	BENCH_META("udp_burst_expected_byte", buf);

	snprintf(buf, sizeof(buf), "%u", udp_rx_packets);
	BENCH_META("udp_rx_packets", buf);

	snprintf(buf, sizeof(buf), "%llu", (unsigned long long)udp_rx_bytes);
	BENCH_META("udp_rx_bytes", buf);

	snprintf(buf, sizeof(buf), "%u", udp_rx_bad_size);
	BENCH_META("udp_rx_bad_size", buf);

	snprintf(buf, sizeof(buf), "%u", udp_rx_bad_payload);
	BENCH_META("udp_rx_bad_payload", buf);

	snprintf(buf, sizeof(buf), "%u", udp_rx_errors);
	BENCH_META("udp_rx_errors", buf);

	snprintf(buf, sizeof(buf), "%u", span_ms);
	BENCH_META("udp_rx_span_ms", buf);
}
