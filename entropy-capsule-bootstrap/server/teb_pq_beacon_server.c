#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <openssl/sha.h>
#include <oqs/oqs.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include "teb_pq_server_keys.h"

#define TEB_HELLO_MAGIC                 "TEBH"
#define TEB_CAPSULE_MAGIC               "TEBC"
#define TEB_VERSION                     1
#define TEB_PROFILE_PQ_MLKEM512_MLDSA44 2
#define TEB_HELLO_LEN                   88
#define TEB_HASH_SIZE                   32
#define TEB_MLKEM512_CIPHERTEXT_SIZE    768
#define TEB_MLKEM512_SHARED_SECRET_SIZE 32
#define TEB_MLDSA44_SIG_SIZE            2420
#define TEB_CAPSULE_HEADER_LEN          32
#define TEB_PQ_CAPSULE_SIGNED_LEN                                                                  \
	(TEB_CAPSULE_HEADER_LEN + TEB_MLKEM512_CIPHERTEXT_SIZE + TEB_HASH_SIZE)
#define TEB_PQ_CAPSULE_LEN (TEB_PQ_CAPSULE_SIGNED_LEN + TEB_MLDSA44_SIG_SIZE)

struct teb_hello {
	uint64_t device_id;
	uint32_t counter;
	uint32_t uptime_ms;
	uint8_t hello_hash[TEB_HASH_SIZE];
};

static void store_u16_be(uint8_t *out, uint16_t v)
{
	out[0] = (uint8_t)(v >> 8);
	out[1] = (uint8_t)v;
}

static void store_u32_be(uint8_t *out, uint32_t v)
{
	out[0] = (uint8_t)(v >> 24);
	out[1] = (uint8_t)(v >> 16);
	out[2] = (uint8_t)(v >> 8);
	out[3] = (uint8_t)v;
}

static void store_u64_be(uint8_t *out, uint64_t v)
{
	for (int i = 7; i >= 0; i--) {
		out[7 - i] = (uint8_t)(v >> (i * 8));
	}
}

static uint16_t load_u16_be(const uint8_t *in)
{
	return ((uint16_t)in[0] << 8) | in[1];
}

static uint32_t load_u32_be(const uint8_t *in)
{
	return ((uint32_t)in[0] << 24) | ((uint32_t)in[1] << 16) | ((uint32_t)in[2] << 8) | in[3];
}

static uint64_t load_u64_be(const uint8_t *in)
{
	uint64_t v = 0;

	for (int i = 0; i < 8; i++) {
		v = (v << 8) | in[i];
	}

	return v;
}

static uint64_t unix_time_ms(void)
{
	struct timespec ts;

	if (clock_gettime(CLOCK_REALTIME, &ts) != 0) {
		return 0;
	}

	return (uint64_t)ts.tv_sec * 1000ULL + (uint64_t)ts.tv_nsec / 1000000ULL;
}

static uint64_t monotonic_time_us(void)
{
	struct timespec ts;

	if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
		return 0;
	}

	return (uint64_t)ts.tv_sec * 1000000ULL + (uint64_t)ts.tv_nsec / 1000ULL;
}

static int parse_hello(const uint8_t *buf, size_t len, struct teb_hello *hello)
{
	if (len != TEB_HELLO_LEN) {
		return -EMSGSIZE;
	}
	if (memcmp(buf, TEB_HELLO_MAGIC, 4) != 0) {
		return -EBADMSG;
	}
	if (buf[4] != TEB_VERSION || buf[5] != TEB_PROFILE_PQ_MLKEM512_MLDSA44 ||
	    load_u16_be(buf + 6) != 0) {
		return -EPROTONOSUPPORT;
	}

	hello->device_id = load_u64_be(buf + 8);
	hello->counter = load_u32_be(buf + 16);
	hello->uptime_ms = load_u32_be(buf + 20);
	SHA256(buf, len, hello->hello_hash);
	return 0;
}

static int build_capsule(const struct teb_hello *hello, uint32_t sequence,
			 uint8_t capsule[TEB_PQ_CAPSULE_LEN])
{
	uint8_t *signed_msg = capsule;
	uint8_t *kem_ct = signed_msg + TEB_CAPSULE_HEADER_LEN;
	uint8_t *hello_hash = kem_ct + TEB_MLKEM512_CIPHERTEXT_SIZE;
	uint8_t *sig = signed_msg + TEB_PQ_CAPSULE_SIGNED_LEN;
	uint8_t shared_secret[TEB_MLKEM512_SHARED_SECRET_SIZE];
	size_t sig_len = TEB_MLDSA44_SIG_SIZE;

	memset(capsule, 0, TEB_PQ_CAPSULE_LEN);
	memcpy(signed_msg, TEB_CAPSULE_MAGIC, 4);
	signed_msg[4] = TEB_VERSION;
	signed_msg[5] = TEB_PROFILE_PQ_MLKEM512_MLDSA44;
	store_u16_be(signed_msg + 6, TEB_PQ_CAPSULE_SIGNED_LEN);
	store_u64_be(signed_msg + 8, hello->device_id);
	store_u32_be(signed_msg + 16, hello->counter);
	store_u64_be(signed_msg + 20, unix_time_ms());
	store_u32_be(signed_msg + 28, sequence);

	if (OQS_KEM_ml_kem_512_encaps(kem_ct, shared_secret, teb_mlkem512_public_key) !=
	    OQS_SUCCESS) {
		return -EIO;
	}
	memcpy(hello_hash, hello->hello_hash, TEB_HASH_SIZE);

	if (OQS_SIG_ml_dsa_44_sign(sig, &sig_len, signed_msg, TEB_PQ_CAPSULE_SIGNED_LEN,
				   teb_mldsa44_secret_key) != OQS_SUCCESS) {
		return -EIO;
	}
	if (sig_len != TEB_MLDSA44_SIG_SIZE) {
		return -EMSGSIZE;
	}

	memset(shared_secret, 0, sizeof(shared_secret));
	return 0;
}

static int parse_args(int argc, char **argv, const char **bind_addr, int *port)
{
	*bind_addr = "0.0.0.0";
	*port = 6767;

	for (int i = 1; i < argc; i++) {
		if (strcmp(argv[i], "--bind") == 0 && i + 1 < argc) {
			*bind_addr = argv[++i];
		} else if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) {
			*port = atoi(argv[++i]);
		} else {
			fprintf(stderr, "usage: %s [--bind addr] [--port port]\n", argv[0]);
			return -EINVAL;
		}
	}

	return 0;
}

int main(int argc, char **argv)
{
	const char *bind_addr;
	int port;
	int fd;
	struct sockaddr_in addr;
	uint32_t sequence = 0;
	int ret;

	ret = parse_args(argc, argv, &bind_addr, &port);
	if (ret != 0) {
		return 2;
	}

	fd = socket(AF_INET, SOCK_DGRAM, 0);
	if (fd < 0) {
		perror("socket");
		return 1;
	}

	memset(&addr, 0, sizeof(addr));
	addr.sin_family = AF_INET;
	addr.sin_port = htons((uint16_t)port);
	if (inet_pton(AF_INET, bind_addr, &addr.sin_addr) != 1) {
		fprintf(stderr, "bad bind address: %s\n", bind_addr);
		close(fd);
		return 1;
	}

	if (bind(fd, (const struct sockaddr *)&addr, sizeof(addr)) != 0) {
		perror("bind");
		close(fd);
		return 1;
	}

	printf("[TEB_SERVER] bind,%s:%d\n", bind_addr, port);
	printf("[TEB_SERVER] profile,pq-mlkem512-mldsa44\n");
	printf("[TEB_SERVER] pq_kem,ML-KEM-512\n");
	printf("[TEB_SERVER] pq_sig,ML-DSA-44\n");
	fflush(stdout);

	while (true) {
		uint8_t hello_buf[TEB_HELLO_LEN];
		uint8_t capsule[TEB_PQ_CAPSULE_LEN];
		struct teb_hello hello;
		struct sockaddr_in peer;
		socklen_t peer_len = sizeof(peer);
		char peer_addr[INET_ADDRSTRLEN];
		uint64_t server_start_us = 0;
		uint64_t server_end_us = 0;
		ssize_t got;

		got = recvfrom(fd, hello_buf, sizeof(hello_buf), 0, (struct sockaddr *)&peer,
			       &peer_len);
		if (got < 0) {
			perror("recvfrom");
			continue;
		}

		ret = parse_hello(hello_buf, (size_t)got, &hello);
		if (ret == 0) {
			sequence++;
			server_start_us = monotonic_time_us();
			ret = build_capsule(&hello, sequence, capsule);
			server_end_us = monotonic_time_us();
		}

		inet_ntop(AF_INET, &peer.sin_addr, peer_addr, sizeof(peer_addr));
		if (ret == 0) {
			ssize_t sent = sendto(fd, capsule, sizeof(capsule), 0,
					      (const struct sockaddr *)&peer, peer_len);
			if (sent == (ssize_t)sizeof(capsule)) {
				printf("[TEB_SERVER] served,"
				       "peer=%s:%u,"
				       "device=0x%016llx,"
				       "counter=%u,"
				       "seq=%u,"
				       "server_us=%llu,"
				       "capsule=%zu\n",
				       peer_addr, ntohs(peer.sin_port),
				       (unsigned long long)hello.device_id, hello.counter, sequence,
				       (unsigned long long)(server_end_us - server_start_us),
				       sizeof(capsule));
			} else {
				printf("[TEB_SERVER] reject,peer=%s:%u,error=send:%s\n", peer_addr,
				       ntohs(peer.sin_port),
				       sent < 0 ? strerror(errno) : "short send");
			}
		} else {
			printf("[TEB_SERVER] reject,peer=%s:%u,error=%d\n", peer_addr,
			       ntohs(peer.sin_port), ret);
		}
		fflush(stdout);
	}
}
