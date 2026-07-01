#include "teb_protocol.h"

#include <errno.h>
#include <string.h>

void teb_store_u16_be(uint8_t *out, uint16_t v)
{
	out[0] = (uint8_t)(v >> 8);
	out[1] = (uint8_t)v;
}

void teb_store_u32_be(uint8_t *out, uint32_t v)
{
	out[0] = (uint8_t)(v >> 24);
	out[1] = (uint8_t)(v >> 16);
	out[2] = (uint8_t)(v >> 8);
	out[3] = (uint8_t)v;
}

void teb_store_u64_be(uint8_t *out, uint64_t v)
{
	for (int i = 7; i >= 0; i--) {
		out[7 - i] = (uint8_t)(v >> (i * 8));
	}
}

uint16_t teb_load_u16_be(const uint8_t *in)
{
	return ((uint16_t)in[0] << 8) | in[1];
}

uint32_t teb_load_u32_be(const uint8_t *in)
{
	return ((uint32_t)in[0] << 24) | ((uint32_t)in[1] << 16) | ((uint32_t)in[2] << 8) | in[3];
}

uint64_t teb_load_u64_be(const uint8_t *in)
{
	uint64_t v = 0;

	for (int i = 0; i < 8; i++) {
		v = (v << 8) | in[i];
	}

	return v;
}

void teb_build_hello(uint8_t out[TEB_HELLO_LEN], const struct teb_hello_fields *fields)
{
	memset(out, 0, TEB_HELLO_LEN);
	memcpy(out + 0, TEB_HELLO_MAGIC, 4);
	out[4] = TEB_VERSION;
	out[5] = TEB_SELECTED_PROFILE;
	teb_store_u16_be(out + 6, 0);
	teb_store_u64_be(out + 8, fields->device_id);
	teb_store_u32_be(out + 16, fields->boot_counter);
	teb_store_u32_be(out + 20, fields->uptime_ms);
	memcpy(out + 24, fields->sram_commitment, TEB_HASH_SIZE);
	memcpy(out + 56, fields->timing_commitment, TEB_HASH_SIZE);
}

int teb_parse_capsule(const uint8_t *buf, size_t len, struct teb_capsule_fields *fields)
{
	if (buf == NULL || fields == NULL) {
		return -EINVAL;
	}

	if (len != TEB_CAPSULE_LEN) {
		return -EMSGSIZE;
	}

	if (memcmp(buf + 0, TEB_CAPSULE_MAGIC, 4) != 0) {
		return -EBADMSG;
	}

	if (buf[4] != TEB_VERSION) {
		return -EPROTONOSUPPORT;
	}

	fields->profile = buf[5];
	if (fields->profile != TEB_SELECTED_PROFILE) {
		return -EPROTONOSUPPORT;
	}

	if (teb_load_u16_be(buf + 6) != TEB_CAPSULE_SIGNED_LEN) {
		return -EBADMSG;
	}

	fields->device_id = teb_load_u64_be(buf + 8);
	fields->boot_counter = teb_load_u32_be(buf + 16);
	fields->gateway_time_ms = teb_load_u64_be(buf + 20);
	fields->sequence = teb_load_u32_be(buf + 28);
#if TEB_SELECTED_PROFILE == TEB_PROFILE_DEV_ED25519_PUF_BEACON
	memcpy(fields->beacon, buf + 32, TEB_BEACON_SIZE);
	memcpy(fields->hello_hash, buf + 64, TEB_HASH_SIZE);
	fields->kem_ciphertext = NULL;
#else
	memset(fields->beacon, 0, sizeof(fields->beacon));
	fields->kem_ciphertext = buf + TEB_CAPSULE_HEADER_LEN;
	memcpy(fields->hello_hash, buf + TEB_CAPSULE_HEADER_LEN + TEB_MLKEM512_CIPHERTEXT_SIZE,
	       TEB_HASH_SIZE);
#endif
	fields->signature = buf + TEB_CAPSULE_SIGNED_LEN;

	return 0;
}
