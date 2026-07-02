/*
 * esp32-rf-rng-state/src/trng_bench.c
 *
 * Copyright (C) 2026 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
 *
 * TRNG measurement routines for the RF-TRNG benchmark.
 *
 * Measures the ESP32 hardware TRNG (WDEV_RND_REG) throughput, per-call
 * latency, and collects raw output for offline statistical analysis.
 *
 * IMPORTANT: This benchmark reads from the stock Zephyr entropy_esp32
 * driver, NOT the BLAKE2s entropy pool.  The goal is to observe the raw
 * hardware RNG behaviour under different RF subsystem states.
 */

#include <stdio.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/entropy.h>
#include <zephyr/drivers/uart.h>

#include "trng_bench.h"

#if !defined(BENCH_CONDITION_RF_DISABLED)
#include "wifi.h"
#endif

/* -- High-resolution timing ------------------------------------ */
/*
 * ESP32 @ 240 MHz -> ~4.17 ns per cycle.
 * k_cycle_get_32() wraps every ~17.9s; unsigned subtraction handles it.
 */
static inline uint32_t get_cycles(void)
{
	return k_cycle_get_32();
}

static inline uint32_t cycles_to_us(uint32_t start, uint32_t end)
{
	return k_cyc_to_us_floor32(end - start);
}

static void uart_write_str(const struct device *uart_dev, const char *text)
{
	while (*text != '\0') {
		uart_poll_out(uart_dev, *text++);
	}
}

/* -- Latency measurement --------------------------------------- */

void trng_bench_latency(const struct device *entropy_dev)
{
	uint8_t buf[TRNG_BENCH_BLOCK_SIZE];
	char meta_buf[32];

	printk("\n--- TRNG Per-Call Latency (%d iterations, %d bytes/call) ---\n",
	       TRNG_BENCH_ITERATIONS, TRNG_BENCH_BLOCK_SIZE);

	/* Warmup: prime any internal state / caches */
	for (int w = 0; w < TRNG_BENCH_WARMUP; w++) {
		entropy_get_entropy(entropy_dev, buf, TRNG_BENCH_BLOCK_SIZE);
	}

	/* Measured iterations */
	for (int i = 0; i < TRNG_BENCH_ITERATIONS; i++) {
		memset(buf, 0, TRNG_BENCH_BLOCK_SIZE);

		uint32_t c0 = get_cycles();
		int ret = entropy_get_entropy(entropy_dev, buf, TRNG_BENCH_BLOCK_SIZE);
		uint32_t c1 = get_cycles();

		if (ret == 0) {
			BENCH_RESULT("trng_latency", TRNG_BENCH_BLOCK_SIZE, i,
				     cycles_to_us(c0, c1));
		} else {
			printk("[BENCH_ERR] trng_latency,%d,%d,ret=%d\n", TRNG_BENCH_BLOCK_SIZE, i,
			       ret);
		}
	}

	snprintf(meta_buf, sizeof(meta_buf), "%d", TRNG_BENCH_ITERATIONS);
	BENCH_META("latency_iterations", meta_buf);
}

/* -- Throughput measurement ------------------------------------ */

void trng_bench_throughput(const struct device *entropy_dev)
{
	uint8_t buf[TRNG_BENCH_BLOCK_SIZE];

	printk("\n--- TRNG Throughput (%d windows x %d ms, %d bytes/read) ---\n",
	       TRNG_THROUGHPUT_WINDOWS, TRNG_THROUGHPUT_WINDOW_MS, TRNG_BENCH_BLOCK_SIZE);

	/* Warmup */
	for (int w = 0; w < TRNG_BENCH_WARMUP; w++) {
		entropy_get_entropy(entropy_dev, buf, TRNG_BENCH_BLOCK_SIZE);
	}

	for (int win = 0; win < TRNG_THROUGHPUT_WINDOWS; win++) {
		uint64_t bytes_read = 0;
		uint32_t c_start = get_cycles();
		int64_t deadline = k_uptime_get() + TRNG_THROUGHPUT_WINDOW_MS;

		while (k_uptime_get() < deadline) {
			int ret = entropy_get_entropy(entropy_dev, buf, TRNG_BENCH_BLOCK_SIZE);
			if (ret == 0) {
				bytes_read += TRNG_BENCH_BLOCK_SIZE;
			}
		}

		uint32_t c_end = get_cycles();
		uint32_t elapsed_us = cycles_to_us(c_start, c_end);

		/* bytes_per_sec = bytes_read * 1000000 / elapsed_us */
		uint64_t bytes_per_sec = 0;
		if (elapsed_us > 0) {
			bytes_per_sec = (bytes_read * 1000000ULL) / elapsed_us;
		}

		BENCH_RESULT("trng_throughput", TRNG_BENCH_BLOCK_SIZE, win,
			     (uint64_t)bytes_per_sec);

		printk("  [window %d] %llu bytes in %u us -> %llu B/s\n", win,
		       (unsigned long long)bytes_read, elapsed_us,
		       (unsigned long long)bytes_per_sec);
	}
}

/* -- Raw byte dump --------------------------------------------- */

void trng_bench_raw_dump(const struct device *entropy_dev)
{
	/*
	 * Collect TRNG_RAW_DUMP_BYTES in chunks and emit as raw binary.
	 * We use a moderately sized chunk (256 bytes) to keep memory footprint
	 * low on the ESP32 while avoiding tiny transfers.
	 */
	const size_t chunk_size = 256;
	uint8_t chunk[256];
	uint64_t total = TRNG_RAW_DUMP_BYTES;
	char meta_buf[32];
	const struct device *uart_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

	printk("\n--- TRNG Raw Byte Dump (%llu bytes) ---\n", (unsigned long long)total);
	snprintf(meta_buf, sizeof(meta_buf), "%llu", (unsigned long long)total);
	BENCH_META("raw_dump_bytes", meta_buf);
	snprintf(meta_buf, sizeof(meta_buf), "%d", TRNG_RAW_START_DELAY_MS);
	BENCH_META("raw_start_delay_ms", meta_buf);

	printk("[BENCH_RAW_ARMED] delay_ms,%d,trigger,G\n", TRNG_RAW_START_DELAY_MS);
	k_msleep(TRNG_RAW_START_DELAY_MS);

	while (true) {
		unsigned char trigger;

		if (uart_poll_in(uart_dev, &trigger) == 0 && trigger == 'G') {
			break;
		}

		k_msleep(10);
	}

	uart_write_str(uart_dev, "[BENCH_RAW_START]\n");
	k_msleep(100); /* Wait for the start marker to clear the console TX ring buffer */

	while (total > 0) {
		size_t to_read = (total < chunk_size) ? (size_t)total : chunk_size;
		int ret = entropy_get_entropy(entropy_dev, chunk, to_read);

		if (ret != 0) {
			printk("\n[BENCH_ERR] raw_dump,ret=%d\n", ret);
			break;
		}

		/* Stream the binary chunk byte-by-byte directly to the UART FIFO */
		for (size_t i = 0; i < to_read; i++) {
			uart_poll_out(uart_dev, chunk[i]);
		}

		total -= to_read;
		k_yield();
	}

	k_msleep(100); /* Wait for binary transmission to finish before the end marker */
	uart_write_str(uart_dev, "\n[BENCH_RAW_END]\n");
}

/* -- Run all benchmarks ---------------------------------------- */

void trng_bench_run_all(const struct device *entropy_dev, const char *condition)
{
	char meta_buf[32];

	printk("\n========================================\n");
	printk("RF-TRNG Benchmark - Condition: %s\n", condition);
	printk("========================================\n");

	BENCH_HEADER();

	/* Metadata */
	BENCH_META("board", "esp32_devkitc_wroom");
	BENCH_META("condition", condition);
	BENCH_META("entropy_driver", entropy_dev->name);
	snprintf(meta_buf, sizeof(meta_buf), "%d", TRNG_BENCH_BLOCK_SIZE);
	BENCH_META("sample_size", meta_buf);
	snprintf(meta_buf, sizeof(meta_buf), "%d", TRNG_BENCH_ITERATIONS);
	BENCH_META("iterations", meta_buf);
	snprintf(meta_buf, sizeof(meta_buf), "%d", TRNG_THROUGHPUT_WINDOWS);
	BENCH_META("throughput_windows", meta_buf);
	snprintf(meta_buf, sizeof(meta_buf), "%d", TRNG_THROUGHPUT_WINDOW_MS);
	BENCH_META("throughput_window_ms", meta_buf);
	snprintf(meta_buf, sizeof(meta_buf), "%d", CONFIG_HEAP_MEM_POOL_SIZE);
	BENCH_META("heap_pool_size", meta_buf);

	/* Phase 1: Latency */
	printk("\n======== Phase 1: Per-Call Latency ========\n");
	trng_bench_latency(entropy_dev);

	/* Phase 2: Throughput */
	printk("\n======== Phase 2: Sustained Throughput ========\n");
	trng_bench_throughput(entropy_dev);

	/* Phase 3: Raw dump */
	printk("\n======== Phase 3: Raw Byte Collection ========\n");

#if !defined(BENCH_CONDITION_RF_DISABLED)
	if (wifi_reconnect() != 0) {
		printk("[BENCH_ERR] wifi_reconnect_before_raw\n");
	}
	BENCH_META("wifi_connected_before_raw", wifi_is_connected() ? "true" : "false");
	wifi_set_event_logging(false);
#endif

	trng_bench_raw_dump(entropy_dev);

#if !defined(BENCH_CONDITION_RF_DISABLED)
	wifi_set_event_logging(true);
#endif

	BENCH_FOOTER();

	printk("\n========================================\n");
	printk("RF-TRNG Benchmark Complete (%s)\n", condition);
	printk("========================================\n");
}
