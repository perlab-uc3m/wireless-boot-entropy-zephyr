#ifndef TEB_CRYPTO_H
#define TEB_CRYPTO_H

#include <stddef.h>
#include <stdint.h>

#include "teb_protocol.h"

int teb_sha256(const uint8_t *in, size_t in_len, uint8_t out[TEB_HASH_SIZE]);
int teb_hkdf_sha256(const uint8_t *ikm, size_t ikm_len, const uint8_t *salt, size_t salt_len,
		    const uint8_t *info, size_t info_len, uint8_t *out, size_t out_len);
int teb_verify_ed25519(const uint8_t pub[TEB_ED25519_PUB_SIZE], const uint8_t *msg, size_t msg_len,
		       const uint8_t sig[TEB_ED25519_SIG_SIZE]);
int teb_verify_mldsa44(const uint8_t pub[TEB_MLDSA44_PUBLIC_KEY_SIZE], const uint8_t *msg,
		       size_t msg_len, const uint8_t sig[TEB_MLDSA44_SIG_SIZE]);
int teb_mlkem512_decapsulate(const uint8_t priv[TEB_MLKEM512_PRIVATE_KEY_SIZE],
			     const uint8_t ct[TEB_MLKEM512_CIPHERTEXT_SIZE],
			     uint8_t ss[TEB_MLKEM512_SHARED_SECRET_SIZE]);
void teb_print_hex(const char *tag, const uint8_t *buf, size_t len);

#endif /* TEB_CRYPTO_H */
