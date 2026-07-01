/*
 * esp32-rf-rng-state/src/trng_bench.h
 *
 * Copyright (C) 2026 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
 *
 * TRNG measurement routines for the RF-TRNG benchmark.
 */

#ifndef TRNG_BENCH_H
#define TRNG_BENCH_H

#include <zephyr/device.h>
#include <stdint.h>
#include <stdbool.h>

/* -- Benchmark parameters --------------------------------------- */

/* Number of entropy_get_entropy() calls per latency measurement round */
#define TRNG_BENCH_ITERATIONS 500

/* Number of warmup calls before measurement (primes caches/state) */
#define TRNG_BENCH_WARMUP 10

/* Block size for each entropy read (bytes) */
#define TRNG_BENCH_BLOCK_SIZE 32

/* Total bytes of raw RNG output to collect for offline analysis.
 * 256 MiB is enough for the strongest practical randlab screening suites
 * in a per-condition run: PractRand, TestU01, GM/T, AIS31, SP 800-90B,
 * Borel, and ENT. NIST STS and Dieharder remain long optional runs.
 */
#ifndef TRNG_RAW_DUMP_BYTES
#define TRNG_RAW_DUMP_BYTES 268435456ULL
#endif

/* Delay before the raw marker so the host can open the serial port after reset. */
#ifndef TRNG_RAW_START_DELAY_MS
#define TRNG_RAW_START_DELAY_MS 15000
#endif

/* Number of throughput measurement windows */
#define TRNG_THROUGHPUT_WINDOWS 10

/* Duration of each throughput window in milliseconds */
#define TRNG_THROUGHPUT_WINDOW_MS 1000

/* -- Structured output macros ----------------------------------- */
/* Compatible with the QEaaS benchmark parser */

#define BENCH_HEADER() printk("\n[BENCH_START]\n")
#define BENCH_FOOTER() printk("[BENCH_END]\n\n")
#define BENCH_RESULT(test, param, iter, us)                                                        \
	printk("[BENCH] %s,%d,%d,%llu\n", (test), (param), (iter), (unsigned long long)(us))
#define BENCH_META(key, val) printk("[BENCH_META] %s,%s\n", (key), (val))

/* -- API -------------------------------------------------------- */

/**
 * Run per-call latency measurements.
 *
 * Calls entropy_get_entropy(dev, buf, TRNG_BENCH_BLOCK_SIZE) repeatedly
 * and records the cycle-accurate latency of each call.
 *
 * Emits: [BENCH] trng_latency,<block_size>,<iter>,<us>
 */
void trng_bench_latency(const struct device *entropy_dev);

/**
 * Run throughput measurements.
 *
 * Reads entropy in a tight loop for TRNG_THROUGHPUT_WINDOW_MS ms per
 * window and reports bytes/sec.
 *
 * Emits: [BENCH] trng_throughput,<block_size>,<window>,<bytes_per_sec>
 */
void trng_bench_throughput(const struct device *entropy_dev);

/**
 * Collect raw RNG output and dump as binary.
 *
 * Reads TRNG_RAW_DUMP_BYTES from the entropy driver and emits them
 * as raw bytes between [BENCH_RAW_START] / [BENCH_RAW_END] tags
 * for offline statistical analysis.
 */
void trng_bench_raw_dump(const struct device *entropy_dev);

/**
 * Run all benchmarks in sequence: latency, throughput, raw dump.
 */
void trng_bench_run_all(const struct device *entropy_dev, const char *condition);

#endif /* TRNG_BENCH_H */
