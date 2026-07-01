/*
 * esp32-rf-rng-state/src/udp_flood.c
 *
 * Copyright (C) 2026 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
 *
 * UDP traffic generator for the wifi_traffic condition.
 *
 * Runs in a background thread and sends/receives UDP packets at maximum
 * rate to keep the ESP32 RF subsystem, interrupt controller, and DMA
 * continuously busy during TRNG measurements.
 */

#include <stdio.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/net/socket.h>

#include "udp_flood.h"
#include "trng_bench.h" /* for BENCH_META */

#ifndef UDP_TARGET_IP
#define UDP_TARGET_IP "192.168.1.136"
#endif

#ifndef UDP_TARGET_PORT
#define UDP_TARGET_PORT 9999
#endif

/* Background thread resources */
#define UDP_FLOOD_STACK_SIZE 4096
#define UDP_FLOOD_PRIORITY   10 /* lower priority than main */

static K_THREAD_STACK_DEFINE(udp_flood_stack, UDP_FLOOD_STACK_SIZE);
static struct k_thread udp_flood_thread;
static volatile bool udp_flood_running = false;

/* Statistics */
static volatile uint32_t udp_tx_packets = 0;
static volatile uint32_t udp_rx_packets = 0;
static volatile uint32_t udp_tx_errors = 0;
static volatile uint64_t udp_tx_bytes = 0;
static volatile uint64_t udp_rx_bytes = 0;

static void udp_flood_thread_fn(void *p1, void *p2, void *p3)
{
	ARG_UNUSED(p1);
	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	int sock;
	struct sockaddr_in dst;
	uint8_t tx_buf[UDP_FLOOD_PAYLOAD_SIZE];
	uint8_t rx_buf[UDP_FLOOD_PAYLOAD_SIZE];
	struct zsock_pollfd fds[1];

	/* Fill TX buffer with recognisable pattern */
	for (int i = 0; i < UDP_FLOOD_PAYLOAD_SIZE; i++) {
		tx_buf[i] = (uint8_t)(i & 0xFF);
	}

	/* Create UDP socket */
	sock = zsock_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
	if (sock < 0) {
		printk("[UDP_FLOOD] Socket creation failed: %d\n", sock);
		udp_flood_running = false;
		return;
	}

	/* Set up destination */
	memset(&dst, 0, sizeof(dst));
	dst.sin_family = AF_INET;
	dst.sin_port = htons(UDP_TARGET_PORT);
	zsock_inet_pton(AF_INET, UDP_TARGET_IP, &dst.sin_addr);

	/* Non-blocking receive */
	fds[0].fd = sock;
	fds[0].events = ZSOCK_POLLIN;

	printk("[UDP_FLOOD] Started -> %s:%d (%d B/pkt)\n", UDP_TARGET_IP, UDP_TARGET_PORT,
	       UDP_FLOOD_PAYLOAD_SIZE);

	while (udp_flood_running) {
		/* Send */
		int sent = zsock_sendto(sock, tx_buf, UDP_FLOOD_PAYLOAD_SIZE, 0,
					(struct sockaddr *)&dst, sizeof(dst));
		if (sent > 0) {
			udp_tx_packets++;
			udp_tx_bytes += sent;
		} else {
			udp_tx_errors++;
		}

		/* Non-blocking receive (check for echo) */
		int poll_ret = zsock_poll(fds, 1, 0);
		if (poll_ret > 0 && (fds[0].revents & ZSOCK_POLLIN)) {
			int rcvd = zsock_recv(sock, rx_buf, sizeof(rx_buf), ZSOCK_MSG_DONTWAIT);
			if (rcvd > 0) {
				udp_rx_packets++;
				udp_rx_bytes += rcvd;
			}
		}

#if UDP_FLOOD_DELAY_US > 0
		k_busy_wait(UDP_FLOOD_DELAY_US);
#else
		/* Yield to let other threads (main, Wi-Fi) run */
		k_yield();
#endif
	}

	zsock_close(sock);
	printk("[UDP_FLOOD] Stopped (TX: %u pkts, RX: %u pkts)\n", udp_tx_packets, udp_rx_packets);
}

int udp_flood_start(void)
{
	if (udp_flood_running) {
		return 0; /* already running */
	}

	udp_flood_running = true;
	udp_tx_packets = 0;
	udp_rx_packets = 0;
	udp_tx_errors = 0;
	udp_tx_bytes = 0;
	udp_rx_bytes = 0;

	k_thread_create(&udp_flood_thread, udp_flood_stack, UDP_FLOOD_STACK_SIZE,
			udp_flood_thread_fn, NULL, NULL, NULL, UDP_FLOOD_PRIORITY, 0, K_NO_WAIT);

	k_thread_name_set(&udp_flood_thread, "udp_flood");

	return 0;
}

void udp_flood_stop(void)
{
	if (!udp_flood_running) {
		return;
	}

	udp_flood_running = false;
	k_thread_join(&udp_flood_thread, K_SECONDS(5));
}

void udp_flood_report_stats(void)
{
	char buf[32];

	snprintf(buf, sizeof(buf), "%u", udp_tx_packets);
	BENCH_META("udp_tx_packets", buf);

	snprintf(buf, sizeof(buf), "%u", udp_rx_packets);
	BENCH_META("udp_rx_packets", buf);

	snprintf(buf, sizeof(buf), "%u", udp_tx_errors);
	BENCH_META("udp_tx_errors", buf);

	snprintf(buf, sizeof(buf), "%llu", (unsigned long long)udp_tx_bytes);
	BENCH_META("udp_tx_bytes", buf);

	snprintf(buf, sizeof(buf), "%llu", (unsigned long long)udp_rx_bytes);
	BENCH_META("udp_rx_bytes", buf);
}
