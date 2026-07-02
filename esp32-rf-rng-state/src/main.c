/*
 * esp32-rf-rng-state/src/main.c
 *
 * Copyright (C) 2026 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
 *
 * RF-TRNG Benchmark: measures ESP32 hardware TRNG behaviour under
 * different RF subsystem operating modes.
 *
 * Experimental conditions (selected at compile time via BENCH_CONDITION_*):
 *
 *   1. RF_DISABLED   - No Wi-Fi driver.  The ESP32 TRNG falls back to
 *                      pseudo-random output because the RF/ADC noise
 *                      source is inactive.  This is a documented
 *                      baseline, not a valid entropy condition.
 *
 *   2. WIFI_IDLE     - Wi-Fi associated and DHCP obtained, but no
 *                      application-layer traffic.  The RF subsystem is
 *                      enabled; the WDEV register receives hardware
 *                      noise.  Best clean true-entropy baseline.
 *
 *   3. WIFI_SCAN     - Wi-Fi associated with periodic active scans.
 *                      This generates RF activity (probe requests,
 *                      channel hopping) without application data,
 *                      separating "RF activity" from "app workload."
 *
 *   4. UDP_BURST     - Wi-Fi associated while a gateway sends a
 *                      deterministic public UDP burst train to the DUT.
 *                      This mirrors the RF-actuated AEB traffic direction
 *                      while measuring only WDEV bytes.
 *
 * The benchmark does NOT use the custom BLAKE2s entropy pool fork.
 * It reads directly from the stock Zephyr entropy_esp32 driver
 * (WDEV_RND_REG) so the pool conditioning cannot mask or alter
 * the raw hardware RNG behaviour.
 *
 * References:
 *   - entropy_esp32.c:46-52  (WDEV_RND_REG + APB timing)
 *   - Kconfig.esp32:14-16    (pseudo-entropy warning)
 *   - espressif,esp32-trng.yaml:7-8 (Wi-Fi/BT noise coupling)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/entropy.h>

#include "trng_bench.h"

/* Conditionally include Wi-Fi and UDP headers */
#if !defined(BENCH_CONDITION_RF_DISABLED)
#include "wifi.h"
#endif

#if defined(BENCH_CONDITION_WIFI_TRAFFIC) || defined(BENCH_CONDITION_UDP_BURST)
#include "udp_flood.h"
#endif

/* -- Condition label ------------------------------------------- */

static const char *get_condition_label(void)
{
#if defined(BENCH_CONDITION_RF_DISABLED)
	return "rf_disabled";
#elif defined(BENCH_CONDITION_WIFI_IDLE)
	return "wifi_idle";
#elif defined(BENCH_CONDITION_WIFI_SCAN)
	return "wifi_scan";
#elif defined(BENCH_CONDITION_UDP_BURST)
	return "udp_burst";
#elif defined(BENCH_CONDITION_WIFI_TRAFFIC)
	return "udp_burst";
#else
	return "unknown";
#endif
}

/* -- Wi-Fi setup (conditions 2-4) ------------------------------ */

#if !defined(BENCH_CONDITION_RF_DISABLED)
static int setup_wifi(void)
{
	wifi_init(NULL);

	int ret = connect_to_wifi();
	if (ret < 0) {
		printk("[MAIN] Wi-Fi connection failed: %d\n", ret);
		return ret;
	}

	/* Allow DHCP and ARP to settle */
	printk("[MAIN] Wi-Fi connected, waiting for network stack...\n");
	k_sleep(K_MSEC(3000));

	return 0;
}
#endif

/* -- Scan loop for wifi_scan condition -------------------------- */

#if defined(BENCH_CONDITION_WIFI_SCAN)

#define SCAN_INTERVAL_MS 5000 /* Trigger a scan every 5 seconds */

static volatile bool scan_thread_running = false;

#define SCAN_THREAD_STACK_SIZE 2048
#define SCAN_THREAD_PRIORITY   12

static K_THREAD_STACK_DEFINE(scan_thread_stack, SCAN_THREAD_STACK_SIZE);
static struct k_thread scan_thread_data;

static void scan_thread_fn(void *p1, void *p2, void *p3)
{
	ARG_UNUSED(p1);
	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	int scan_count = 0;

	while (scan_thread_running) {
		wifi_trigger_scan();
		scan_count++;
		if (wifi_event_logging_enabled()) {
			printk("[SCAN] Scan #%d triggered\n", scan_count);
		}
		k_sleep(K_MSEC(SCAN_INTERVAL_MS));
	}

	if (wifi_event_logging_enabled()) {
		printk("[SCAN] Thread stopped after %d scans\n", scan_count);
	}
}

static void start_scan_thread(void)
{
	scan_thread_running = true;
	k_thread_create(&scan_thread_data, scan_thread_stack, SCAN_THREAD_STACK_SIZE,
			scan_thread_fn, NULL, NULL, NULL, SCAN_THREAD_PRIORITY, 0, K_NO_WAIT);
	k_thread_name_set(&scan_thread_data, "wifi_scan");
}

static void stop_scan_thread(void)
{
	scan_thread_running = false;
	k_thread_join(&scan_thread_data, K_SECONDS(10));
}

#endif /* BENCH_CONDITION_WIFI_SCAN */

/* -- Main ------------------------------------------------------ */

int main(void)
{
	const struct device *entropy_dev;
	const char *condition = get_condition_label();

	printk("\n========================================\n");
	printk("RF-TRNG Benchmark\n");
	printk("Condition: %s\n", condition);
	printk("========================================\n");

	/* Get the entropy device.
	 *
	 * On stock Zephyr without BLAKE2s overlay, zephyr,entropy points
	 * directly to trng0 (espressif,esp32-trng).  This gives us raw
	 * WDEV_RND_REG reads through entropy_esp32.c.
	 */
	entropy_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_entropy));
	if (!device_is_ready(entropy_dev)) {
		printk("FATAL: Entropy device not ready\n");
		return EXIT_FAILURE;
	}

	printk("Entropy device: %s\n", entropy_dev->name);

	/* Wait for serial monitor to attach after flash+reset */
	printk("Waiting for serial monitor...\n");
	k_msleep(5000);

	/* -- Condition-specific setup -------------------------------- */

#if defined(BENCH_CONDITION_RF_DISABLED)
	printk("\n[MAIN] Condition: RF_DISABLED\n");
	printk("[MAIN] WARNING: Without Wi-Fi/BT enabled, the ESP32 TRNG\n");
	printk("[MAIN] produces pseudo-random output (WDEV register not fed\n");
	printk("[MAIN] by RF noise).  This is a documented pseudo-random\n");
	printk("[MAIN] baseline, NOT a valid entropy source.\n\n");

#elif defined(BENCH_CONDITION_WIFI_IDLE)
	printk("\n[MAIN] Condition: WIFI_IDLE\n");
	if (setup_wifi() != 0) {
		printk("FATAL: Wi-Fi setup failed\n");
		return EXIT_FAILURE;
	}
	printk("[MAIN] Wi-Fi idle - RF subsystem enabled, no app traffic\n\n");

#elif defined(BENCH_CONDITION_WIFI_SCAN)
	printk("\n[MAIN] Condition: WIFI_SCAN\n");
	if (setup_wifi() != 0) {
		printk("FATAL: Wi-Fi setup failed\n");
		return EXIT_FAILURE;
	}
	printk("[MAIN] Starting periodic Wi-Fi scans...\n");
	start_scan_thread();
	/* Let the first scan complete before benchmarking */
	k_sleep(K_MSEC(SCAN_INTERVAL_MS + 2000));
	printk("[MAIN] Scan thread running - measuring TRNG concurrently\n\n");

#elif defined(BENCH_CONDITION_WIFI_TRAFFIC) || defined(BENCH_CONDITION_UDP_BURST)
	printk("\n[MAIN] Condition: UDP_BURST\n");
	if (setup_wifi() != 0) {
		printk("FATAL: Wi-Fi setup failed\n");
		return EXIT_FAILURE;
	}
	printk("[MAIN] Starting deterministic UDP burst receiver...\n");
	if (udp_flood_start() != 0) {
		printk("FATAL: UDP burst receiver start failed\n");
		return EXIT_FAILURE;
	}
	/* Give the host a moment to see the IPv4 line and start sending bursts. */
	k_sleep(K_MSEC(3000));
	printk("[MAIN] UDP burst receiver running - measuring TRNG concurrently\n\n");

#else
	printk("FATAL: No valid BENCH_CONDITION_* defined\n");
	return EXIT_FAILURE;
#endif

	/* -- Run TRNG benchmarks ------------------------------------ */

	trng_bench_run_all(entropy_dev, condition);

	/* -- Condition-specific teardown ---------------------------- */

#if defined(BENCH_CONDITION_WIFI_SCAN)
	stop_scan_thread();
#endif

#if defined(BENCH_CONDITION_WIFI_TRAFFIC) || defined(BENCH_CONDITION_UDP_BURST)
	udp_flood_stop();
	udp_flood_report_stats();
#endif

#if !defined(BENCH_CONDITION_RF_DISABLED)
	wifi_disconnect();
#endif

	printk("\n========================================\n");
	printk("RF-TRNG Benchmark Complete (%s)\n", condition);
	printk("========================================\n");

	return EXIT_SUCCESS;
}
