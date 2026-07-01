#include "renewal_rng_wrap.h"

#include <string.h>
#include <zephyr/kernel.h>
#include <wolfssl/wolfcrypt/random.h>

static struct k_spinlock rng_lock;
static struct renewal_rng_stats rng_stats;

void renewal_rng_reset(void)
{
	k_spinlock_key_t key = k_spin_lock(&rng_lock);

	memset(&rng_stats, 0, sizeof(rng_stats));
	k_spin_unlock(&rng_lock, key);
}

void renewal_rng_snapshot(struct renewal_rng_stats *out)
{
	k_spinlock_key_t key;

	if (out == NULL) {
		return;
	}

	key = k_spin_lock(&rng_lock);
	*out = rng_stats;
	k_spin_unlock(&rng_lock, key);
}

static void record_block_result(word32 sz, uint32_t cycles, int ret)
{
	k_spinlock_key_t key = k_spin_lock(&rng_lock);

	rng_stats.block_calls++;
	rng_stats.block_cycles += cycles;
	if (ret == 0) {
		rng_stats.block_bytes += sz;
	} else {
		rng_stats.errors++;
	}

	k_spin_unlock(&rng_lock, key);
}

static void record_byte_result(uint32_t cycles, int ret)
{
	k_spinlock_key_t key = k_spin_lock(&rng_lock);

	rng_stats.byte_calls++;
	rng_stats.byte_cycles += cycles;
	if (ret == 0) {
		rng_stats.byte_bytes++;
	} else {
		rng_stats.errors++;
	}

	k_spin_unlock(&rng_lock, key);
}

int __real_wc_RNG_GenerateBlock(WC_RNG *rng, byte *output, word32 sz);
int __wrap_wc_RNG_GenerateBlock(WC_RNG *rng, byte *output, word32 sz)
{
	uint32_t start = k_cycle_get_32();
	int ret = __real_wc_RNG_GenerateBlock(rng, output, sz);
	uint32_t end = k_cycle_get_32();

	record_block_result(sz, end - start, ret);
	return ret;
}

int __real_wc_RNG_GenerateByte(WC_RNG *rng, byte *b);
int __wrap_wc_RNG_GenerateByte(WC_RNG *rng, byte *b)
{
	uint32_t start = k_cycle_get_32();
	int ret = __real_wc_RNG_GenerateByte(rng, b);
	uint32_t end = k_cycle_get_32();

	record_byte_result(end - start, ret);
	return ret;
}
