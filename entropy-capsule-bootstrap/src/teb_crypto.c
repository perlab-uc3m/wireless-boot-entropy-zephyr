#include "teb_crypto.h"

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>

#include <zephyr/kernel.h>

#include <wolfssl/wolfcrypt/dilithium.h>
#include <wolfssl/wolfcrypt/ed25519.h>
#include <wolfssl/wolfcrypt/hash.h>
#include <wolfssl/wolfcrypt/hmac.h>
#include <wolfssl/wolfcrypt/mlkem.h>
#include <wolfssl/wolfcrypt/wc_mlkem.h>

int teb_sha256(const uint8_t *in, size_t in_len, uint8_t out[TEB_HASH_SIZE])
{
	if (in_len > UINT32_MAX) {
		return -EMSGSIZE;
	}

	return wc_Sha256Hash(in, (word32)in_len, out);
}

int teb_hkdf_sha256(const uint8_t *ikm, size_t ikm_len, const uint8_t *salt, size_t salt_len,
		    const uint8_t *info, size_t info_len, uint8_t *out, size_t out_len)
{
	if (ikm_len > UINT32_MAX || salt_len > UINT32_MAX || info_len > UINT32_MAX ||
	    out_len > UINT32_MAX) {
		return -EMSGSIZE;
	}

	return wc_HKDF(WC_SHA256, ikm, (word32)ikm_len, salt, (word32)salt_len, info,
		       (word32)info_len, out, (word32)out_len);
}

int teb_verify_ed25519(const uint8_t pub[TEB_ED25519_PUB_SIZE], const uint8_t *msg, size_t msg_len,
		       const uint8_t sig[TEB_ED25519_SIG_SIZE])
{
	ed25519_key *key;
	int verify = 0;
	int ret;

	if (msg_len > UINT32_MAX) {
		return -EMSGSIZE;
	}

	key = k_malloc(sizeof(*key));
	if (key == NULL) {
		return -ENOMEM;
	}

	ret = wc_ed25519_init(key);
	if (ret != 0) {
		k_free(key);
		return ret;
	}

	ret = wc_ed25519_import_public(pub, TEB_ED25519_PUB_SIZE, key);
	if (ret == 0) {
		ret = wc_ed25519_verify_msg(sig, TEB_ED25519_SIG_SIZE, msg, (word32)msg_len,
					    &verify, key);
	}

	wc_ed25519_free(key);
	k_free(key);

	if (ret != 0) {
		return ret;
	}

	return verify == 1 ? 0 : -EACCES;
}

int teb_verify_mldsa44(const uint8_t pub[TEB_MLDSA44_PUBLIC_KEY_SIZE], const uint8_t *msg,
		       size_t msg_len, const uint8_t sig[TEB_MLDSA44_SIG_SIZE])
{
	dilithium_key *key;
	int verify = 0;
	int ret;
	bool inited = false;

	if (msg_len > UINT32_MAX) {
		return -EMSGSIZE;
	}

	key = k_malloc(sizeof(*key));
	if (key == NULL) {
		return -ENOMEM;
	}

	ret = wc_dilithium_init(key);
	if (ret == 0) {
		inited = true;
	}
	if (ret == 0) {
		ret = wc_dilithium_set_level(key, WC_ML_DSA_44);
	}
	if (ret == 0) {
		ret = wc_dilithium_import_public(pub, TEB_MLDSA44_PUBLIC_KEY_SIZE, key);
	}
	if (ret == 0) {
		ret = wc_dilithium_verify_ctx_msg(sig, TEB_MLDSA44_SIG_SIZE, NULL, 0, msg,
						  (word32)msg_len, &verify, key);
	}

	if (inited) {
		wc_dilithium_free(key);
	}
	k_free(key);

	if (ret != 0) {
		return ret;
	}

	return verify == 1 ? 0 : -EACCES;
}

int teb_mlkem512_decapsulate(const uint8_t priv[TEB_MLKEM512_PRIVATE_KEY_SIZE],
			     const uint8_t ct[TEB_MLKEM512_CIPHERTEXT_SIZE],
			     uint8_t ss[TEB_MLKEM512_SHARED_SECRET_SIZE])
{
	MlKemKey *key;
	int ret;
	bool inited = false;

	key = k_malloc(sizeof(*key));
	if (key == NULL) {
		return -ENOMEM;
	}

	ret = wc_MlKemKey_Init(key, WC_ML_KEM_512, NULL, INVALID_DEVID);
	if (ret == 0) {
		inited = true;
	}
	if (ret == 0) {
		ret = wc_MlKemKey_DecodePrivateKey(key, priv, TEB_MLKEM512_PRIVATE_KEY_SIZE);
	}
	if (ret == 0) {
		ret = wc_MlKemKey_Decapsulate(key, ss, ct, TEB_MLKEM512_CIPHERTEXT_SIZE);
	}

	if (inited) {
		wc_MlKemKey_Free(key);
	}
	k_free(key);

	return ret;
}

void teb_print_hex(const char *tag, const uint8_t *buf, size_t len)
{
	printf("%s", tag);
	for (size_t i = 0; i < len; i++) {
		printf("%02x", buf[i]);
	}
	printf("\n");
}
