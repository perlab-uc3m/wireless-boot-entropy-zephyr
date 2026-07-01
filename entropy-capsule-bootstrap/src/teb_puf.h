#ifndef TEB_PUF_H
#define TEB_PUF_H

#include <stddef.h>
#include <stdint.h>

#include "teb_protocol.h"

struct teb_puf_sample {
	uint8_t sram_secret[TEB_HASH_SIZE];
	uint8_t sram_commitment[TEB_HASH_SIZE];
	uint8_t timing_secret[TEB_HASH_SIZE];
	uint8_t timing_commitment[TEB_HASH_SIZE];
	uint32_t sram_ones;
	uint32_t sram_transitions;
	uint32_t timing_min_delta;
	uint32_t timing_max_delta;
	uint64_t timing_sum_delta;
};

void teb_puf_capture(struct teb_puf_sample *out);

#endif /* TEB_PUF_H */
