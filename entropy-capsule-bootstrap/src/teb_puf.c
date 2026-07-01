#include "teb_puf.h"

#include <limits.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <wolfssl/wolfcrypt/sha256.h>

#include "teb_crypto.h"

#ifndef TEB_PUF_BYTES
#define TEB_PUF_BYTES 4096
#endif

#ifndef TEB_PUF_TIMING_SAMPLES
#define TEB_PUF_TIMING_SAMPLES 512
#endif

__attribute__((section(".noinit.teb_sram_puf"), aligned(4),
	       used)) static volatile uint8_t sram_puf_area[TEB_PUF_BYTES];

static uint8_t popcount8(uint8_t v)
{
	uint8_t c = 0;

	while (v != 0) {
		c += v & 1U;
		v >>= 1;
	}

	return c;
}

static void hash_labelled(const char *label, const uint8_t *buf, size_t len,
			  uint8_t out[TEB_HASH_SIZE])
{
	wc_Sha256 sha;

	(void)wc_InitSha256(&sha);
	(void)wc_Sha256Update(&sha, (const uint8_t *)label, strlen(label));
	(void)wc_Sha256Update(&sha, buf, (word32)len);
	(void)wc_Sha256Final(&sha, out);
	wc_Sha256Free(&sha);
}

static void capture_sram(struct teb_puf_sample *out)
{
	wc_Sha256 secret_hash;
	uint8_t prev = 0;
	bool have_prev = false;

	out->sram_ones = 0;
	out->sram_transitions = 0;

	(void)wc_InitSha256(&secret_hash);
	(void)wc_Sha256Update(&secret_hash, (const uint8_t *)"TEB SRAM PUF secret v1",
			      strlen("TEB SRAM PUF secret v1"));

	for (size_t i = 0; i < TEB_PUF_BYTES; i++) {
		uint8_t b = sram_puf_area[i];

		out->sram_ones += popcount8(b);
		if (have_prev) {
			out->sram_transitions += popcount8((uint8_t)(prev ^ b));
		}
		prev = b;
		have_prev = true;
		(void)wc_Sha256Update(&secret_hash, &b, 1);
	}

	(void)wc_Sha256Final(&secret_hash, out->sram_secret);
	wc_Sha256Free(&secret_hash);

	hash_labelled("TEB SRAM PUF commitment v1", out->sram_secret, TEB_HASH_SIZE,
		      out->sram_commitment);

#if TEB_PUF_DUMP_HEX
	printf("[TEB_PUF_RAW] ");
	for (size_t i = 0; i < TEB_PUF_BYTES; i++) {
		printf("%02x", (uint8_t)sram_puf_area[i]);
	}
	printf("\n");
#endif

	for (size_t i = 0; i < TEB_PUF_BYTES; i++) {
		sram_puf_area[i] = (uint8_t)(0xa5U ^ (uint8_t)i);
	}
}

static void capture_timing(struct teb_puf_sample *out)
{
	wc_Sha256 timing_hash;
	volatile uint32_t sink = 0;

	out->timing_min_delta = UINT_MAX;
	out->timing_max_delta = 0;
	out->timing_sum_delta = 0;

	(void)wc_InitSha256(&timing_hash);
	(void)wc_Sha256Update(&timing_hash, (const uint8_t *)"TEB timing fingerprint v1",
			      strlen("TEB timing fingerprint v1"));

	for (uint32_t i = 0; i < TEB_PUF_TIMING_SAMPLES; i++) {
		uint32_t start = k_cycle_get_32();

		for (uint32_t j = 0; j < 64; j++) {
			sink ^= (i + 1U) * (j + 17U);
		}

		uint32_t delta = k_cycle_get_32() - start;
		uint8_t enc[4];

		if (delta < out->timing_min_delta) {
			out->timing_min_delta = delta;
		}
		if (delta > out->timing_max_delta) {
			out->timing_max_delta = delta;
		}
		out->timing_sum_delta += delta;

		teb_store_u32_be(enc, delta);
		(void)wc_Sha256Update(&timing_hash, enc, sizeof(enc));
	}

	(void)wc_Sha256Update(&timing_hash, (const uint8_t *)&sink, sizeof(sink));
	(void)wc_Sha256Final(&timing_hash, out->timing_secret);
	wc_Sha256Free(&timing_hash);

	hash_labelled("TEB timing commitment v1", out->timing_secret, TEB_HASH_SIZE,
		      out->timing_commitment);
}

void teb_puf_capture(struct teb_puf_sample *out)
{
	memset(out, 0, sizeof(*out));
	capture_sram(out);
	capture_timing(out);
}
