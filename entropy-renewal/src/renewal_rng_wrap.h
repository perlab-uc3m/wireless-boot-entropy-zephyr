#ifndef RENEWAL_RNG_WRAP_H
#define RENEWAL_RNG_WRAP_H

#include <stdint.h>

struct renewal_rng_stats {
	uint64_t block_calls;
	uint64_t block_bytes;
	uint64_t block_cycles;
	uint64_t byte_calls;
	uint64_t byte_bytes;
	uint64_t byte_cycles;
	uint64_t errors;
};

void renewal_rng_reset(void);
void renewal_rng_snapshot(struct renewal_rng_stats *out);

#endif /* RENEWAL_RNG_WRAP_H */
