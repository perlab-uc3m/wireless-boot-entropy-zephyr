#ifndef TEB_PROTOCOL_H
#define TEB_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define TEB_VERSION                        1
#define TEB_PROFILE_DEV_ED25519_PUF_BEACON 1
#define TEB_PROFILE_PQ_MLKEM512_MLDSA44    2

#ifndef TEB_SELECTED_PROFILE
#define TEB_SELECTED_PROFILE TEB_PROFILE_PQ_MLKEM512_MLDSA44
#endif

#define TEB_HELLO_MAGIC   "TEBH"
#define TEB_CAPSULE_MAGIC "TEBC"

#define TEB_HASH_SIZE                   32
#define TEB_BEACON_SIZE                 32
#define TEB_ED25519_PUB_SIZE            32
#define TEB_ED25519_SIG_SIZE            64
#define TEB_MLKEM512_PUBLIC_KEY_SIZE    800
#define TEB_MLKEM512_PRIVATE_KEY_SIZE   1632
#define TEB_MLKEM512_CIPHERTEXT_SIZE    768
#define TEB_MLKEM512_SHARED_SECRET_SIZE 32
#define TEB_MLDSA44_PUBLIC_KEY_SIZE     1312
#define TEB_MLDSA44_SIG_SIZE            2420
#define TEB_BOOT_SEED_SIZE              32

#define TEB_HELLO_LEN                  88
#define TEB_CAPSULE_HEADER_LEN         32
#define TEB_ED25519_CAPSULE_SIGNED_LEN 96
#define TEB_ED25519_CAPSULE_LEN        (TEB_ED25519_CAPSULE_SIGNED_LEN + TEB_ED25519_SIG_SIZE)
#define TEB_PQ_CAPSULE_SIGNED_LEN                                                                  \
	(TEB_CAPSULE_HEADER_LEN + TEB_MLKEM512_CIPHERTEXT_SIZE + TEB_HASH_SIZE)
#define TEB_PQ_CAPSULE_LEN (TEB_PQ_CAPSULE_SIGNED_LEN + TEB_MLDSA44_SIG_SIZE)

#if TEB_SELECTED_PROFILE == TEB_PROFILE_DEV_ED25519_PUF_BEACON
#define TEB_PROFILE_NAME       "dev-ed25519-puf-beacon"
#define TEB_CAPSULE_SIGNED_LEN TEB_ED25519_CAPSULE_SIGNED_LEN
#define TEB_CAPSULE_LEN        TEB_ED25519_CAPSULE_LEN
#elif TEB_SELECTED_PROFILE == TEB_PROFILE_PQ_MLKEM512_MLDSA44
#define TEB_PROFILE_NAME       "pq-mlkem512-mldsa44"
#define TEB_CAPSULE_SIGNED_LEN TEB_PQ_CAPSULE_SIGNED_LEN
#define TEB_CAPSULE_LEN        TEB_PQ_CAPSULE_LEN
#else
#error "Unsupported TEB_SELECTED_PROFILE"
#endif

struct teb_hello_fields {
	uint64_t device_id;
	uint32_t boot_counter;
	uint32_t uptime_ms;
	uint8_t sram_commitment[TEB_HASH_SIZE];
	uint8_t timing_commitment[TEB_HASH_SIZE];
};

struct teb_capsule_fields {
	uint8_t profile;
	uint64_t device_id;
	uint32_t boot_counter;
	uint64_t gateway_time_ms;
	uint32_t sequence;
	uint8_t beacon[TEB_BEACON_SIZE];
	uint8_t hello_hash[TEB_HASH_SIZE];
	const uint8_t *kem_ciphertext;
	const uint8_t *signature;
};

void teb_store_u16_be(uint8_t *out, uint16_t v);
void teb_store_u32_be(uint8_t *out, uint32_t v);
void teb_store_u64_be(uint8_t *out, uint64_t v);
uint16_t teb_load_u16_be(const uint8_t *in);
uint32_t teb_load_u32_be(const uint8_t *in);
uint64_t teb_load_u64_be(const uint8_t *in);

void teb_build_hello(uint8_t out[TEB_HELLO_LEN], const struct teb_hello_fields *fields);
int teb_parse_capsule(const uint8_t *buf, size_t len, struct teb_capsule_fields *fields);

#endif /* TEB_PROTOCOL_H */
