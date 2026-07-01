/*
 * Embedded entropy-renewal benchmark for ESP32 + Zephyr + wolfSSL.
 *
 * Each iteration creates one fresh DTLS 1.3 session and records:
 * - wolfSSL RNG-output bytes requested through wc_RNG_GenerateBlock/Byte
 * - credited BLAKE2s pool balance before and after the DTLS exchange
 * - local hardware refill bytes, accepted network bytes, and pool-debit bytes
 * - timing and RNG-wrapper cycle counters
 *
 * The application protocol is deliberately minimal: after the DTLS handshake,
 * the client writes "GET <N>\n" and expects N raw entropy bytes in reply.
 */

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/drivers/entropy.h>
#include <zephyr/drivers/entropy_blake2s_renewal.h>
#include <zephyr/kernel.h>
#include <zephyr/net/socket.h>
#include <zephyr/sys/mem_stats.h>
#include <zephyr/sys/sys_heap.h>
#include <zephyr/sys/util.h>

#include <wolfssl/ssl.h>
#include <wolfssl/version.h>
#include <wolfssl/wolfcrypt/settings.h>

#include "renewal_rng_wrap.h"
#include "wifi.h"

#ifndef SKIP_PEER_VERIFY
#include "ca_certs.h"
#endif

#ifdef CONFIG_SYS_HEAP_RUNTIME_STATS
extern struct k_heap _system_heap;
#endif

#ifndef DTLS_SERVER_IP
#define DTLS_SERVER_IP "192.168.1.136"
#endif

#ifndef DTLS_SERVER_PORT
#define DTLS_SERVER_PORT 5684
#endif

#ifndef DTLS_ENTROPY_BYTES
#define DTLS_ENTROPY_BYTES 32
#endif

#ifndef WOLFSSL_GROUP_NAME
#define WOLFSSL_GROUP_NAME "P-256"
#endif

#ifndef RENEWAL_ITERATIONS
#define RENEWAL_ITERATIONS 50
#endif

#ifndef RENEWAL_WARMUP
#define RENEWAL_WARMUP 3
#endif

#ifndef RENEWAL_INTER_HANDSHAKE_MS
#define RENEWAL_INTER_HANDSHAKE_MS 1000
#endif

#ifndef RENEWAL_NETWORK_INJECT_EVERY
#define RENEWAL_NETWORK_INJECT_EVERY 1
#endif

#define RESPONSE_BUFFER_SIZE     768
#define ENTROPY_PAYLOAD_MAX      256
#define DTLS_REQUEST_BUFFER_SIZE 32
#define DTLS_SOCKET_TIMEOUT_S    30
#define DTLS_MTU                 1400

#define RENEWAL_META(key, value) printf("[RENEWAL_META] %s,%s\n", (key), (value))

static uint8_t response_data[RESPONSE_BUFFER_SIZE];
static size_t response_data_len;

static inline uint32_t get_cycles(void)
{
	return k_cycle_get_32();
}

static inline uint32_t cycles_to_us(uint32_t start, uint32_t end)
{
	return k_cyc_to_us_floor32(end - start);
}

static void report_heap_stats(const char *label)
{
#ifdef CONFIG_SYS_HEAP_RUNTIME_STATS
	struct sys_memory_stats stats;
	char key[48];
	char value[32];

	if (sys_heap_runtime_stats_get(&_system_heap.heap, &stats) == 0) {
		snprintf(key, sizeof(key), "heap_free_%s", label);
		snprintf(value, sizeof(value), "%zu", stats.free_bytes);
		RENEWAL_META(key, value);

		snprintf(key, sizeof(key), "heap_used_%s", label);
		snprintf(value, sizeof(value), "%zu", stats.allocated_bytes);
		RENEWAL_META(key, value);

		snprintf(key, sizeof(key), "heap_peak_%s", label);
		snprintf(value, sizeof(value), "%zu", stats.max_allocated_bytes);
		RENEWAL_META(key, value);
	}
#else
	ARG_UNUSED(label);
#endif
}

static int setup_destination_address(struct sockaddr_in *dst, const char *host, uint16_t port)
{
	memset(dst, 0, sizeof(*dst));
	dst->sin_family = AF_INET;
	dst->sin_port = htons(port);

	if (inet_pton(AF_INET, host, &dst->sin_addr) <= 0) {
		printf("[RENEWAL_ERR] inet_pton,%s\n", host);
		return -EINVAL;
	}

	return 0;
}

static int create_connected_udp_socket(const struct sockaddr_in *dst)
{
	struct timeval timeout = {
		.tv_sec = DTLS_SOCKET_TIMEOUT_S,
		.tv_usec = 0,
	};
	int fd;

	fd = zsock_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
	if (fd < 0) {
		printf("[RENEWAL_ERR] socket,%d\n", errno);
		return -errno;
	}

	(void)zsock_setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	(void)zsock_setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

	if (zsock_connect(fd, (const struct sockaddr *)dst, sizeof(*dst)) < 0) {
		int err = errno;

		printf("[RENEWAL_ERR] udp_connect,%d\n", err);
		(void)zsock_close(fd);
		return -err;
	}

	return fd;
}

static WOLFSSL_CTX *create_dtls_ctx(void)
{
	WOLFSSL_CTX *ctx;

	ctx = wolfSSL_CTX_new(wolfDTLSv1_3_client_method());
	if (ctx == NULL) {
		printf("[RENEWAL_ERR] wolfssl_ctx_new\n");
		return NULL;
	}

	if (wolfSSL_CTX_set_cipher_list(ctx, "TLS13-AES128-GCM-SHA256") != WOLFSSL_SUCCESS) {
		printf("[RENEWAL_ERR] wolfssl_cipher_list\n");
		wolfSSL_CTX_free(ctx);
		return NULL;
	}

	if (wolfSSL_CTX_set1_groups_list(ctx, WOLFSSL_GROUP_NAME) != WOLFSSL_SUCCESS) {
		printf("[RENEWAL_ERR] wolfssl_group,%s\n", WOLFSSL_GROUP_NAME);
		wolfSSL_CTX_free(ctx);
		return NULL;
	}

#ifdef SKIP_PEER_VERIFY
	wolfSSL_CTX_set_verify(ctx, SSL_VERIFY_NONE, NULL);
#else
	wolfSSL_CTX_set_verify(ctx, SSL_VERIFY_PEER, NULL);
#if defined(WOLFSSL_SIG_MLDSA44)
	if (wolfSSL_CTX_load_verify_buffer(ctx, (const unsigned char *)ca_cert_mldsa44_pem,
					   sizeof(ca_cert_mldsa44_pem),
					   SSL_FILETYPE_PEM) != WOLFSSL_SUCCESS) {
		printf("[RENEWAL_ERR] load_ca_mldsa44\n");
		wolfSSL_CTX_free(ctx);
		return NULL;
	}
#else
	if (wolfSSL_CTX_load_verify_buffer(ctx, (const unsigned char *)ca_cert_ecc_pem,
					   sizeof(ca_cert_ecc_pem),
					   SSL_FILETYPE_PEM) != WOLFSSL_SUCCESS) {
		printf("[RENEWAL_ERR] load_ca_ecc\n");
		wolfSSL_CTX_free(ctx);
		return NULL;
	}
#endif
#endif

	return ctx;
}

static int configure_dtls_session(WOLFSSL *ssl, int fd)
{
	if (wolfSSL_set_fd(ssl, fd) != WOLFSSL_SUCCESS) {
		return -EIO;
	}

	if (wolfSSL_dtls_set_mtu(ssl, DTLS_MTU) != WOLFSSL_SUCCESS) {
		return -EIO;
	}

#if defined(WOLFSSL_ALGO_MLKEM512) || defined(WOLFSSL_ALGO_MLKEM768) ||                            \
	defined(WOLFSSL_ALGO_MLKEM1024)
	if (wolfSSL_NoKeyShares(ssl) != WOLFSSL_SUCCESS) {
		return -EIO;
	}
#endif

	return 0;
}

static int dtls_request_entropy(WOLFSSL *ssl)
{
	char request[DTLS_REQUEST_BUFFER_SIZE];
	int request_len;
	int ret;

	response_data_len = 0;
	memset(response_data, 0, sizeof(response_data));

	ret = wolfSSL_connect(ssl);
	if (ret != WOLFSSL_SUCCESS) {
		printf("[RENEWAL_ERR] wolfssl_connect,%d\n", wolfSSL_get_error(ssl, ret));
		return -ECONNABORTED;
	}

	request_len = snprintf(request, sizeof(request), "GET %d\n", DTLS_ENTROPY_BYTES);
	if (request_len <= 0 || request_len >= (int)sizeof(request)) {
		return -EINVAL;
	}

	ret = wolfSSL_write(ssl, request, request_len);
	if (ret != request_len) {
		printf("[RENEWAL_ERR] wolfssl_write,%d\n", wolfSSL_get_error(ssl, ret));
		return -EIO;
	}

	while (response_data_len < DTLS_ENTROPY_BYTES &&
	       response_data_len < sizeof(response_data)) {
		ret = wolfSSL_read(ssl, response_data + response_data_len,
				   sizeof(response_data) - response_data_len);
		if (ret <= 0) {
			printf("[RENEWAL_ERR] wolfssl_read,%d\n", wolfSSL_get_error(ssl, ret));
			return response_data_len > 0 ? 0 : -ENODATA;
		}
		response_data_len += (size_t)ret;
	}

	return 0;
}

static int maybe_mix_network_entropy(const struct device *entropy_dev, int iter,
				     size_t *accepted_bytes)
{
#ifdef RENEWAL_ENABLE_NETWORK_INJECTION
	size_t payload_len;
	int ret;
#endif

	*accepted_bytes = 0;

#ifndef RENEWAL_ENABLE_NETWORK_INJECTION
	ARG_UNUSED(entropy_dev);
	ARG_UNUSED(iter);
	return 0;
#else
	if (RENEWAL_NETWORK_INJECT_EVERY <= 0 || (iter % RENEWAL_NETWORK_INJECT_EVERY) != 0) {
		return 0;
	}

	payload_len = MIN(response_data_len, (size_t)DTLS_ENTROPY_BYTES);
	payload_len = MIN(payload_len, (size_t)ENTROPY_PAYLOAD_MAX);
	if (payload_len == 0) {
		printf("[RENEWAL_WARN] empty_entropy_payload,%d\n", iter);
		return -ENODATA;
	}

	ret = entropy_add_entropy(entropy_dev, response_data, payload_len, payload_len * 8);
	if (ret == 0) {
		*accepted_bytes = payload_len;
	}
	return ret;
#endif
}

static void emit_algorithm_metadata(void)
{
#if defined(WOLFSSL_ALGO_MLKEM1024)
	RENEWAL_META("key_exchange", "ML-KEM-1024");
#elif defined(WOLFSSL_ALGO_MLKEM768)
	RENEWAL_META("key_exchange", "ML-KEM-768");
#elif defined(WOLFSSL_ALGO_MLKEM512)
	RENEWAL_META("key_exchange", "ML-KEM-512");
#elif defined(WOLFSSL_ALGO_X25519)
	RENEWAL_META("key_exchange", "X25519");
#elif defined(WOLFSSL_ALGO_ECDHE_P256)
	RENEWAL_META("key_exchange", "ECDHE-P256");
#else
	RENEWAL_META("key_exchange", "unknown");
#endif

#if defined(WOLFSSL_SIG_MLDSA44)
	RENEWAL_META("signature", "ML-DSA-44");
#else
	RENEWAL_META("signature", "ECDSA-P256");
#endif
}

static int run_one_iteration(const struct device *entropy_dev, const struct sockaddr_in *dst,
			     int iter, bool measured)
{
	struct entropy_blake2s_renewal_stats pre;
	struct entropy_blake2s_renewal_stats post;
	struct renewal_rng_stats rng;
	WOLFSSL_CTX *ctx = NULL;
	WOLFSSL *ssl = NULL;
	uint32_t start;
	uint32_t end;
	size_t accepted_net_bytes = 0;
	int fd = -1;
	int ret = -EIO;

	ctx = create_dtls_ctx();
	if (ctx == NULL) {
		return -ENOMEM;
	}

	fd = create_connected_udp_socket(dst);
	if (fd < 0) {
		ret = fd;
		goto out;
	}

	ssl = wolfSSL_new(ctx);
	if (ssl == NULL) {
		ret = -ENOMEM;
		goto out;
	}

	ret = configure_dtls_session(ssl, fd);
	if (ret != 0) {
		printf("[RENEWAL_ERR] wolfssl_config,%d\n", ret);
		goto out;
	}

	memset(&pre, 0, sizeof(pre));
	memset(&post, 0, sizeof(post));
	memset(&rng, 0, sizeof(rng));

	renewal_rng_reset();
	(void)entropy_blake2s_renewal_snapshot(&pre);
	start = get_cycles();

	ret = dtls_request_entropy(ssl);
	if (ret == 0) {
		ret = maybe_mix_network_entropy(entropy_dev, iter, &accepted_net_bytes);
	}

	end = get_cycles();
	(void)entropy_blake2s_renewal_snapshot(&post);
	renewal_rng_snapshot(&rng);

	if (measured) {
		printf("[RENEWAL_ITER] %d,%d,%u,%zu,%zu,"
		       "%llu,%llu,%llu,%llu,"
		       "%llu,%llu,%llu,%llu,%llu,%llu,%u,%u,"
		       "%llu,%llu,%llu,%llu,%llu\n",
		       iter, ret, cycles_to_us(start, end), response_data_len, accepted_net_bytes,
		       (unsigned long long)rng.block_calls, (unsigned long long)rng.block_bytes,
		       (unsigned long long)rng.byte_calls, (unsigned long long)rng.byte_bytes,
		       (unsigned long long)(post.total_hw_bytes - pre.total_hw_bytes),
		       (unsigned long long)(post.total_external_bytes - pre.total_external_bytes),
		       (unsigned long long)(post.total_pool_debit_bytes -
					    pre.total_pool_debit_bytes),
		       (unsigned long long)(post.total_thread_get_bytes -
					    pre.total_thread_get_bytes),
		       (unsigned long long)(post.total_isr_get_bytes - pre.total_isr_get_bytes),
		       (unsigned long long)(post.total_fast_refill_bytes -
					    pre.total_fast_refill_bytes),
		       pre.credited_pool_bits, post.credited_pool_bits,
		       (unsigned long long)rng.block_cycles, (unsigned long long)rng.byte_cycles,
		       (unsigned long long)rng.errors, (unsigned long long)pre.timestamp_us,
		       (unsigned long long)post.timestamp_us);
	}

out:
	if (ssl != NULL) {
		wolfSSL_shutdown(ssl);
		wolfSSL_free(ssl);
	}
	if (fd >= 0) {
		(void)zsock_close(fd);
	}
	if (ctx != NULL) {
		wolfSSL_CTX_free(ctx);
	}
	return ret;
}

static int run_benchmark(const struct device *entropy_dev)
{
	struct sockaddr_in dst;
	int ret;

	ret = wolfSSL_Init();
	if (ret != WOLFSSL_SUCCESS) {
		printf("[RENEWAL_ERR] wolfssl_init,%d\n", ret);
		return -EIO;
	}

	ret = setup_destination_address(&dst, DTLS_SERVER_IP, DTLS_SERVER_PORT);
	if (ret != 0) {
		goto out_cleanup;
	}

	wifi_init(NULL);
	ret = connect_to_wifi();
	if (ret < 0 || wait_for_wifi_connection() < 0) {
		printf("[RENEWAL_ERR] wifi_connect,%d\n", ret);
		ret = -ENOTCONN;
		goto out_cleanup;
	}
	k_sleep(K_MSEC(1000));

	for (int i = 0; i < RENEWAL_WARMUP; i++) {
		(void)run_one_iteration(entropy_dev, &dst, i, false);
		k_sleep(K_MSEC(RENEWAL_INTER_HANDSHAKE_MS));
	}

	printf("[RENEWAL_HEADER] iter,ret,elapsed_us,response_bytes,"
	       "accepted_net_bytes,rng_block_calls,rng_block_bytes,"
	       "rng_byte_calls,rng_byte_bytes,local_hw_bytes,external_bytes,"
	       "pool_debit_bytes,thread_get_bytes,isr_get_bytes,"
	       "fast_refill_bytes,pool_credit_bits_pre,pool_credit_bits_post,"
	       "rng_block_cycles,rng_byte_cycles,rng_errors,"
	       "pool_timestamp_us_pre,pool_timestamp_us_post\n");

	for (int i = 0; i < RENEWAL_ITERATIONS; i++) {
		ret = run_one_iteration(entropy_dev, &dst, i, true);
		if (ret != 0) {
			printf("[RENEWAL_WARN] iteration_failed,%d,%d\n", i, ret);
		}
		if (!wifi_is_connected() && wifi_reconnect() != 0) {
			printf("[RENEWAL_ERR] wifi_reconnect_failed,%d\n", i);
			break;
		}
		k_sleep(K_MSEC(RENEWAL_INTER_HANDSHAKE_MS));
	}

	ret = 0;

out_cleanup:
	wifi_disconnect();
	wolfSSL_Cleanup();
	return ret;
}

int main(void)
{
	const struct device *entropy_dev;
	struct entropy_blake2s_renewal_stats boot_stats;
	char value[64];

	printf("\n[RENEWAL_START]\n");
	k_sleep(K_SECONDS(5));

	entropy_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_entropy));
	if (!device_is_ready(entropy_dev)) {
		printf("[RENEWAL_ERR] entropy_device_not_ready\n");
		return EXIT_FAILURE;
	}

	RENEWAL_META("board", "esp32_devkitc_wroom");
	RENEWAL_META("entropy_driver", entropy_dev->name);
	RENEWAL_META("schema_version", "3");
	RENEWAL_META("transport", "dtls13_udp");
	RENEWAL_META("application_protocol", "raw_entropy_request");
	snprintf(value, sizeof(value), "%s:%d", DTLS_SERVER_IP, DTLS_SERVER_PORT);
	RENEWAL_META("dtls_server", value);
	snprintf(value, sizeof(value), "%d", DTLS_ENTROPY_BYTES);
	RENEWAL_META("dtls_entropy_bytes", value);
	RENEWAL_META("wolfssl_group_name", WOLFSSL_GROUP_NAME);
	RENEWAL_META("wolfssl_version", LIBWOLFSSL_VERSION_STRING);
	RENEWAL_META("cert_verify",
#ifdef SKIP_PEER_VERIFY
		     "disabled"
#else
		     "enabled"
#endif
	);
	emit_algorithm_metadata();

	snprintf(value, sizeof(value), "%d", RENEWAL_ITERATIONS);
	RENEWAL_META("iterations", value);
	snprintf(value, sizeof(value), "%d", RENEWAL_WARMUP);
	RENEWAL_META("warmup", value);
	snprintf(value, sizeof(value), "%d", RENEWAL_INTER_HANDSHAKE_MS);
	RENEWAL_META("inter_handshake_ms", value);
#ifdef RENEWAL_ENABLE_NETWORK_INJECTION
	RENEWAL_META("network_injection", "enabled");
	RENEWAL_META("network_payload_parser", "raw_binary");
	RENEWAL_META("network_entropy_credit_bits_per_byte", "8");
#else
	RENEWAL_META("network_injection", "disabled");
#endif

#ifdef RENEWAL_DISABLE_LOCAL_REFILL_AFTER_BOOTSTRAP
	entropy_blake2s_renewal_set_hw_refill_enabled(false);
	RENEWAL_META("local_hw_refill_after_bootstrap", "disabled");
#else
	RENEWAL_META("local_hw_refill_after_bootstrap", "enabled");
#endif

	if (entropy_blake2s_renewal_snapshot(&boot_stats) == 0) {
		snprintf(value, sizeof(value), "%u", boot_stats.credited_pool_bits);
		RENEWAL_META("boot_pool_credit_bits", value);
	}

	report_heap_stats("boot");
	(void)run_benchmark(entropy_dev);
	report_heap_stats("final");

	printf("[RENEWAL_END]\n");
	return EXIT_SUCCESS;
}
