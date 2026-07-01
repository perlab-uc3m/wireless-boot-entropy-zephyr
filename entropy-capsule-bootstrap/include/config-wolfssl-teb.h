#ifndef WOLFSSL_SETTINGS_H
#define WOLFSSL_SETTINGS_H

#ifdef __cplusplus
extern "C" {
#endif

#ifndef WOLFSSL_USER_SETTINGS
#define WOLFSSL_USER_SETTINGS
#endif

#ifndef WOLFSSL_ZEPHYR
#define WOLFSSL_ZEPHYR
#endif

#define NO_STDIO_FILESYSTEM
#define NO_WRITEV
#define NO_DEV_RANDOM
#define NO_MAIN_DRIVER
#define NO_BIO
#define WOLFCRYPT_ONLY
#define WOLFSSL_NO_STDIO
#define WOLFSSL_NO_STDIO_PRINTF
#define NO_PWDBASED
#define NO_ERROR_QUEUE
#define SINGLE_THREADED

#define WOLFSSL_SHA256
#define WOLFSSL_SHA512
#define WOLFSSL_SHA3
#define WOLFSSL_SHAKE128
#define WOLFSSL_SHAKE256
#define HAVE_HKDF
#undef NO_HMAC

#define HAVE_ED25519
#define HAVE_ED25519_VERIFY
#define HAVE_ED25519_KEY_IMPORT

#define WOLFSSL_HAVE_MLKEM
#define WOLFSSL_WC_MLKEM
#define WOLFSSL_WC_ML_KEM_512
#define WOLFSSL_NO_ML_KEM_768
#define WOLFSSL_NO_ML_KEM_1024
#define WOLFSSL_MLKEM_NO_MAKE_KEY
#define WOLFSSL_MLKEM_NO_ENCAPSULATE
#define WOLFSSL_MLKEM_SMALL

#define HAVE_DILITHIUM
#define WOLFSSL_WC_DILITHIUM
#define WOLFSSL_NO_ML_DSA_65
#define WOLFSSL_NO_ML_DSA_87
#define WOLFSSL_DILITHIUM_VERIFY_ONLY
#define WOLFSSL_DILITHIUM_VERIFY_SMALL_MEM
#define WOLFSSL_DILITHIUM_SMALL

#define WOLFSSL_SMALL_STACK
#define NO_ERROR_STRINGS

#define NO_RSA
#define NO_DH
#define NO_DSA
#define NO_RC4
#define NO_MD4
#define NO_MD5
#define NO_OLD_TLS
#define NO_CERTS
#define NO_AES

#define XMALLOC_OVERRIDE
#define XMALLOC(s, h, t) ((void)(h), (void)(t), k_malloc((size_t)(s)))
#define XFREE(p, h, t)                                                                             \
	do {                                                                                       \
		void *xp = (p);                                                                    \
		(void)(h), (void)(t);                                                              \
		if (xp) {                                                                          \
			k_free(xp);                                                                \
		}                                                                                  \
	} while (0)
#define XREALLOC(p, n, h, t) ((void)(h), (void)(t), k_realloc((p), (size_t)(n)))

#ifdef __cplusplus
}
#endif

#endif /* WOLFSSL_SETTINGS_H */
