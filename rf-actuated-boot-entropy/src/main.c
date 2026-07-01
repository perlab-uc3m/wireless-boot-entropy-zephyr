/*
 * RF-Actuated Boot Entropy
 *
 * The ESP32 starts with a boot HELLO. The gateway/host replies with a
 * public stimulus. The device samples local WDEV RNG output and packet-arrival
 * timing during that stimulus, then derives a seed locally. The stimulus and
 * nonce are public and are never counted as entropy.
 */

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <mbedtls/hkdf.h>
#include <mbedtls/md.h>
#include <mbedtls/sha256.h>

#include <zephyr/device.h>
#include <zephyr/drivers/entropy.h>
#include <zephyr/kernel.h>
#include <zephyr/net/socket.h>
#include <zephyr/sys/byteorder.h>

#include "wifi.h"

#ifndef AEB_UDP_PORT
#define AEB_UDP_PORT 7777
#endif

#ifndef AEB_GATEWAY_IP
#define AEB_GATEWAY_IP "0.0.0.0"
#endif

#ifndef AEB_GATEWAY_PORT
#define AEB_GATEWAY_PORT 7778
#endif

#ifndef AEB_CLIENT_INITIATED
#define AEB_CLIENT_INITIATED 1
#endif

#ifndef AEB_CLIENT_TRIALS
#define AEB_CLIENT_TRIALS 16
#endif

#ifndef AEB_BURST_COUNT
#define AEB_BURST_COUNT 64
#endif

#ifndef AEB_INTERVAL_US
#define AEB_INTERVAL_US 1000
#endif

#ifndef AEB_MAX_SAMPLE_BYTES
#define AEB_MAX_SAMPLE_BYTES 8192
#endif

#ifndef AEB_MAX_BURSTS
#define AEB_MAX_BURSTS 256
#endif

#ifndef AEB_RAW_CHUNK_BYTES
#define AEB_RAW_CHUNK_BYTES 512
#endif

#ifndef AEB_TRIAL_GAP_MS
#define AEB_TRIAL_GAP_MS 250
#endif

#ifndef AEB_DUMP_RAW_HEX
#define AEB_DUMP_RAW_HEX 0
#endif

#ifndef AEB_DUMP_SEED
#define AEB_DUMP_SEED 0
#endif

#define AEB_MAGIC             0x31424541u /* "AEB1", little endian */
#define AEB_VERSION           1u
#define AEB_TYPE_START        1u
#define AEB_TYPE_BURST        2u
#define AEB_TYPE_HELLO        3u
#define AEB_TYPE_RAW_BEGIN    4u
#define AEB_TYPE_RAW_CHUNK    5u
#define AEB_TYPE_RAW_END      6u
#define AEB_TYPE_JITTER_CHUNK 7u
#define AEB_HEADER_LEN        40u
#define AEB_SEED_LEN          32u
#define AEB_HASH_LEN          32u
#define AEB_RECV_BUF_LEN      512u
#define AEB_BURST_TIMEOUT_MS  2000
#define AEB_START_TIMEOUT_MS  10000
#define AEB_IDLE_SPACING_US   1000

#if AEB_RAW_CHUNK_BYTES <= 0
#error "AEB_RAW_CHUNK_BYTES must be positive"
#endif

struct aeb_msg {
	uint16_t type;
	uint32_t trial_id;
	uint32_t seq_or_count;
	uint32_t interval_us;
	uint32_t sample_bytes;
	uint8_t nonce[16];
	uint8_t header[AEB_HEADER_LEN];
};

static uint8_t raw_buf[AEB_MAX_SAMPLE_BYTES];
static uint32_t jitter_us[AEB_MAX_BURSTS];
static uint8_t tx_buf[AEB_HEADER_LEN + AEB_RAW_CHUNK_BYTES];

static inline uint32_t cycles_now(void)
{
	return k_cycle_get_32();
}

static inline uint32_t cycles_to_us(uint32_t start, uint32_t end)
{
	return k_cyc_to_us_floor32(end - start);
}

static void bytes_to_hex(const uint8_t *in, size_t len, char *out, size_t out_len)
{
	static const char hex[] = "0123456789abcdef";
	size_t pos = 0;

	if (out_len == 0) {
		return;
	}

	for (size_t i = 0; i < len && pos + 2 < out_len; i++) {
		out[pos++] = hex[in[i] >> 4];
		out[pos++] = hex[in[i] & 0x0f];
	}
	out[pos] = '\0';
}

static void build_msg(uint8_t header[AEB_HEADER_LEN], uint16_t type, uint32_t trial_id,
		      uint32_t seq_or_count, uint32_t interval_us, uint32_t sample_bytes,
		      const uint8_t nonce[16])
{
	memset(header, 0, AEB_HEADER_LEN);
	sys_put_le32(AEB_MAGIC, &header[0]);
	sys_put_le16(AEB_VERSION, &header[4]);
	sys_put_le16(type, &header[6]);
	sys_put_le32(trial_id, &header[8]);
	sys_put_le32(seq_or_count, &header[12]);
	sys_put_le32(interval_us, &header[16]);
	sys_put_le32(sample_bytes, &header[20]);
	memcpy(&header[24], nonce, 16);
}

static void fill_public_nonce(uint32_t trial_id, uint8_t nonce[16])
{
	/*
	 * The nonce is a public transcript label. Do not use the entropy device
	 * here; we want the first measured source bytes to live inside the trial.
	 */
	sys_put_le32(trial_id, &nonce[0]);
	sys_put_le32(k_uptime_get_32(), &nonce[4]);
	memcpy(&nonce[8], "AEBBOOT1", 8);
}

static int sha256_one(const uint8_t *buf, size_t len, uint8_t out[AEB_HASH_LEN])
{
	mbedtls_sha256_context ctx;
	int ret;

	mbedtls_sha256_init(&ctx);
	ret = mbedtls_sha256_starts(&ctx, 0);
	if (ret == 0) {
		ret = mbedtls_sha256_update(&ctx, buf, len);
	}
	if (ret == 0) {
		ret = mbedtls_sha256_finish(&ctx, out);
	}
	mbedtls_sha256_free(&ctx);

	return ret;
}

static int sha256_response(const uint8_t *raw, size_t raw_len, const uint32_t *deltas,
			   size_t delta_count, uint8_t out[AEB_HASH_LEN])
{
	mbedtls_sha256_context ctx;
	uint8_t le[4];
	int ret;

	mbedtls_sha256_init(&ctx);
	ret = mbedtls_sha256_starts(&ctx, 0);
	if (ret == 0) {
		ret = mbedtls_sha256_update(&ctx, raw, raw_len);
	}
	for (size_t i = 0; ret == 0 && i < delta_count; i++) {
		sys_put_le32(deltas[i], le);
		ret = mbedtls_sha256_update(&ctx, le, sizeof(le));
	}
	if (ret == 0) {
		ret = mbedtls_sha256_finish(&ctx, out);
	}
	mbedtls_sha256_free(&ctx);

	return ret;
}

static int sha256_jitter(const uint32_t *deltas, size_t delta_count, uint8_t out[AEB_HASH_LEN])
{
	mbedtls_sha256_context ctx;
	uint8_t le[4];
	int ret;

	mbedtls_sha256_init(&ctx);
	ret = mbedtls_sha256_starts(&ctx, 0);
	for (size_t i = 0; ret == 0 && i < delta_count; i++) {
		sys_put_le32(deltas[i], le);
		ret = mbedtls_sha256_update(&ctx, le, sizeof(le));
	}
	if (ret == 0) {
		ret = mbedtls_sha256_finish(&ctx, out);
	}
	mbedtls_sha256_free(&ctx);

	return ret;
}

static int derive_seed(const uint8_t response_hash[AEB_HASH_LEN],
		       const uint8_t transcript_hash[AEB_HASH_LEN], uint8_t seed[AEB_SEED_LEN])
{
	static const uint8_t info[] = "rf-actuated-entropy-v1";
	const mbedtls_md_info_t *md = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);

	if (!md) {
		return -ENOTSUP;
	}

	return mbedtls_hkdf(md, transcript_hash, AEB_HASH_LEN, response_hash, AEB_HASH_LEN, info,
			    sizeof(info) - 1, seed, AEB_SEED_LEN);
}

static int parse_msg(const uint8_t *buf, size_t len, struct aeb_msg *msg)
{
	if (len < AEB_HEADER_LEN) {
		return -EMSGSIZE;
	}

	if (sys_get_le32(&buf[0]) != AEB_MAGIC) {
		return -EINVAL;
	}
	if (sys_get_le16(&buf[4]) != AEB_VERSION) {
		return -EPROTONOSUPPORT;
	}

	msg->type = sys_get_le16(&buf[6]);
	msg->trial_id = sys_get_le32(&buf[8]);
	msg->seq_or_count = sys_get_le32(&buf[12]);
	msg->interval_us = sys_get_le32(&buf[16]);
	msg->sample_bytes = sys_get_le32(&buf[20]);
	memcpy(msg->nonce, &buf[24], sizeof(msg->nonce));
	memcpy(msg->header, buf, AEB_HEADER_LEN);

	return 0;
}

static int entropy_read_exact(const struct device *entropy_dev, uint8_t *dst, size_t len)
{
	size_t off = 0;

	while (off < len) {
		size_t chunk = len - off;
		int ret;

		if (chunk > 64) {
			chunk = 64;
		}

		ret = entropy_get_entropy(entropy_dev, &dst[off], chunk);
		if (ret != 0) {
			return ret;
		}
		off += chunk;
	}

	return 0;
}

static uint64_t bit_count(const uint8_t *buf, size_t len)
{
	uint64_t ones = 0;

	for (size_t i = 0; i < len; i++) {
		ones += __builtin_popcount((unsigned int)buf[i]);
	}

	return ones;
}

static uint64_t bit_transitions(const uint8_t *buf, size_t len)
{
	uint64_t transitions = 0;
	int previous = -1;

	for (size_t i = 0; i < len; i++) {
		for (int bit = 7; bit >= 0; bit--) {
			int current = (buf[i] >> bit) & 1;

			if (previous >= 0 && current != previous) {
				transitions++;
			}
			previous = current;
		}
	}

	return transitions;
}

static void jitter_stats(const uint32_t *deltas, size_t count, uint32_t *min_us, uint32_t *mean_us,
			 uint32_t *max_us)
{
	uint64_t sum = 0;

	if (count == 0) {
		*min_us = 0;
		*mean_us = 0;
		*max_us = 0;
		return;
	}

	*min_us = deltas[0];
	*max_us = deltas[0];

	for (size_t i = 0; i < count; i++) {
		if (deltas[i] < *min_us) {
			*min_us = deltas[i];
		}
		if (deltas[i] > *max_us) {
			*max_us = deltas[i];
		}
		sum += deltas[i];
	}

	*mean_us = (uint32_t)(sum / count);
}

static void maybe_dump_raw_hex(uint32_t trial_id, const uint8_t *raw, size_t raw_len)
{
#if AEB_DUMP_RAW_HEX
	char line[129];

	printk("[AEB_RAW_HEX_BEGIN] trial=%u bytes=%u\n", trial_id, (unsigned int)raw_len);

	for (size_t off = 0; off < raw_len; off += 64) {
		size_t chunk = raw_len - off;

		if (chunk > 64) {
			chunk = 64;
		}
		bytes_to_hex(&raw[off], chunk, line, sizeof(line));
		printk("[AEB_RAW_HEX_CHUNK] trial=%u offset=%u hex=%s\n", trial_id,
		       (unsigned int)off, line);
	}

	printk("[AEB_RAW_HEX_END] trial=%u\n", trial_id);
#else
	ARG_UNUSED(trial_id);
	ARG_UNUSED(raw);
	ARG_UNUSED(raw_len);
#endif
}

static void maybe_dump_seed(uint32_t trial_id, const uint8_t seed[AEB_SEED_LEN])
{
#if AEB_DUMP_SEED
	char seed_hex[AEB_SEED_LEN * 2 + 1];

	bytes_to_hex(seed, AEB_SEED_LEN, seed_hex, sizeof(seed_hex));
	printk("[AEB_SEED] trial=%u seed=%s\n", trial_id, seed_hex);
#else
	ARG_UNUSED(trial_id);
	ARG_UNUSED(seed);
#endif
}

static int send_text(int fd, const char *line)
{
	int ret = zsock_send(fd, line, strlen(line), 0);

	if (ret < 0) {
		return -errno;
	}
	return 0;
}

static int send_binary_msg(int fd, uint16_t type, uint32_t trial_id, uint32_t seq_or_count,
			   uint32_t interval_us, uint32_t sample_bytes, const uint8_t nonce[16],
			   const uint8_t *payload, size_t payload_len)
{
	if (payload_len > AEB_RAW_CHUNK_BYTES) {
		return -EMSGSIZE;
	}

	build_msg(tx_buf, type, trial_id, seq_or_count, interval_us, sample_bytes, nonce);
	if (payload_len > 0) {
		memcpy(&tx_buf[AEB_HEADER_LEN], payload, payload_len);
	}

	int ret = zsock_send(fd, tx_buf, AEB_HEADER_LEN + payload_len, 0);

	if (ret < 0) {
		return -errno;
	}
	if ((size_t)ret != AEB_HEADER_LEN + payload_len) {
		return -EIO;
	}
	return 0;
}

static int send_raw_stream(int fd, const struct aeb_msg *start, const uint8_t *raw,
			   uint32_t raw_len, const uint32_t *deltas, uint32_t delta_count)
{
	uint32_t raw_chunks = (raw_len + AEB_RAW_CHUNK_BYTES - 1) / AEB_RAW_CHUNK_BYTES;
	uint32_t jitter_values_per_chunk = AEB_RAW_CHUNK_BYTES / sizeof(uint32_t);
	uint8_t jitter_payload[AEB_RAW_CHUNK_BYTES];
	int ret;

	ret = send_binary_msg(fd, AEB_TYPE_RAW_BEGIN, start->trial_id, raw_chunks,
			      start->interval_us, raw_len, start->nonce, NULL, 0);
	if (ret != 0) {
		return ret;
	}

	for (uint32_t i = 0; i < raw_chunks; i++) {
		uint32_t off = i * AEB_RAW_CHUNK_BYTES;
		uint32_t chunk = raw_len - off;

		if (chunk > AEB_RAW_CHUNK_BYTES) {
			chunk = AEB_RAW_CHUNK_BYTES;
		}

		ret = send_binary_msg(fd, AEB_TYPE_RAW_CHUNK, start->trial_id, i,
				      start->interval_us, chunk, start->nonce, &raw[off], chunk);
		if (ret != 0) {
			return ret;
		}
	}

	if (jitter_values_per_chunk == 0) {
		jitter_values_per_chunk = 1;
	}

	for (uint32_t off = 0, chunk_index = 0; off < delta_count;
	     off += jitter_values_per_chunk, chunk_index++) {
		uint32_t values = delta_count - off;
		size_t payload_len;

		if (values > jitter_values_per_chunk) {
			values = jitter_values_per_chunk;
		}

		for (uint32_t i = 0; i < values; i++) {
			sys_put_le32(deltas[off + i], &jitter_payload[i * sizeof(uint32_t)]);
		}
		payload_len = values * sizeof(uint32_t);

		ret = send_binary_msg(fd, AEB_TYPE_JITTER_CHUNK, start->trial_id, chunk_index,
				      start->interval_us, payload_len, start->nonce, jitter_payload,
				      payload_len);
		if (ret != 0) {
			return ret;
		}
	}

	return send_binary_msg(fd, AEB_TYPE_RAW_END, start->trial_id, raw_chunks,
			       start->interval_us, raw_len, start->nonce, NULL, 0);
}

static int run_trial(int fd, const struct device *entropy_dev, const struct aeb_msg *start,
		     bool send_udp_result, uint32_t *raw_len_out, uint32_t *jitter_count_out)
{
	uint32_t requested = start->sample_bytes;
	uint32_t expected = start->seq_or_count;
	uint32_t packets_seen = 0;
	uint32_t sample_used = 0;
	uint32_t per_packet;
	uint32_t sample_start;
	uint32_t sample_end;
	uint32_t prev_cycle;
	uint32_t min_jitter;
	uint32_t mean_jitter;
	uint32_t max_jitter;
	uint8_t recv_buf[AEB_RECV_BUF_LEN];
	uint8_t raw_hash[AEB_HASH_LEN];
	uint8_t jitter_hash[AEB_HASH_LEN];
	uint8_t response_hash[AEB_HASH_LEN];
	uint8_t transcript_hash[AEB_HASH_LEN];
	uint8_t seed[AEB_SEED_LEN];
	uint8_t seed_hash[AEB_HASH_LEN];
	char nonce_hex[sizeof(start->nonce) * 2 + 1];
	char raw_hex[AEB_HASH_LEN * 2 + 1];
	char jitter_hex[AEB_HASH_LEN * 2 + 1];
	char response_hex[AEB_HASH_LEN * 2 + 1];
	char seed_hash_hex[AEB_HASH_LEN * 2 + 1];
	char result_line[768];
	const char *condition = expected == 0 ? "idle" : "burst";
	int ret;

	if (requested == 0 || requested > AEB_MAX_SAMPLE_BYTES) {
		requested = AEB_MAX_SAMPLE_BYTES;
	}
	if (expected > AEB_MAX_BURSTS) {
		expected = AEB_MAX_BURSTS;
	}

	memset(raw_buf, 0, requested);
	memset(jitter_us, 0, sizeof(jitter_us));

	per_packet = expected == 0 ? requested : requested / expected;
	if (expected > 0 && per_packet == 0) {
		per_packet = 1;
	}

	ret = sha256_one(start->header, AEB_HEADER_LEN, transcript_hash);
	if (ret != 0) {
		return ret;
	}

	sample_start = cycles_now();
	prev_cycle = sample_start;

	if (expected == 0) {
		ret = entropy_read_exact(entropy_dev, raw_buf, requested);
		if (ret != 0) {
			return ret;
		}
		sample_used = requested;
		k_busy_wait(AEB_IDLE_SPACING_US);
	} else {
		for (uint32_t i = 0; i < expected; i++) {
			struct zsock_pollfd pfd = {
				.fd = fd,
				.events = ZSOCK_POLLIN,
			};
			struct aeb_msg burst;
			uint32_t now;
			size_t to_read;

			ret = zsock_poll(&pfd, 1, AEB_BURST_TIMEOUT_MS);
			if (ret <= 0) {
				break;
			}

			ret = zsock_recv(fd, recv_buf, sizeof(recv_buf), ZSOCK_MSG_DONTWAIT);
			if (ret < 0) {
				break;
			}

			if (parse_msg(recv_buf, (size_t)ret, &burst) != 0 ||
			    burst.type != AEB_TYPE_BURST || burst.trial_id != start->trial_id) {
				continue;
			}

			now = cycles_now();
			jitter_us[packets_seen] = cycles_to_us(prev_cycle, now);
			prev_cycle = now;

			to_read = per_packet;
			if (sample_used + to_read > requested) {
				to_read = requested - sample_used;
			}
			if (to_read > 0) {
				int e = entropy_read_exact(entropy_dev, &raw_buf[sample_used],
							   to_read);
				if (e != 0) {
					return e;
				}
				sample_used += to_read;
			}

			packets_seen++;
		}

		if (sample_used < requested) {
			ret = entropy_read_exact(entropy_dev, &raw_buf[sample_used],
						 requested - sample_used);
			if (ret != 0) {
				return ret;
			}
			sample_used = requested;
		}
	}

	sample_end = cycles_now();

	ret = sha256_one(raw_buf, requested, raw_hash);
	if (ret == 0) {
		ret = sha256_jitter(jitter_us, packets_seen, jitter_hash);
	}
	if (ret == 0) {
		ret = sha256_response(raw_buf, requested, jitter_us, packets_seen, response_hash);
	}
	if (ret == 0) {
		ret = derive_seed(response_hash, transcript_hash, seed);
	}
	if (ret == 0) {
		ret = sha256_one(seed, sizeof(seed), seed_hash);
	}
	if (ret != 0) {
		return ret;
	}

	jitter_stats(jitter_us, packets_seen, &min_jitter, &mean_jitter, &max_jitter);

	bytes_to_hex(start->nonce, sizeof(start->nonce), nonce_hex, sizeof(nonce_hex));
	bytes_to_hex(raw_hash, sizeof(raw_hash), raw_hex, sizeof(raw_hex));
	bytes_to_hex(jitter_hash, sizeof(jitter_hash), jitter_hex, sizeof(jitter_hex));
	bytes_to_hex(response_hash, sizeof(response_hash), response_hex, sizeof(response_hex));
	bytes_to_hex(seed_hash, sizeof(seed_hash), seed_hash_hex, sizeof(seed_hash_hex));

	snprintf(result_line, sizeof(result_line),
		 "[AEB_RESULT] trial=%u condition=%s nonce=%s "
		 "sample_bytes=%u packets_expected=%u packets_seen=%u "
		 "interval_us=%u sample_us=%u ones=%llu transitions=%llu "
		 "jitter_min_us=%u jitter_mean_us=%u jitter_max_us=%u "
		 "raw_sha256=%s jitter_sha256=%s response_sha256=%s "
		 "seed_sha256=%s\n",
		 start->trial_id, condition, nonce_hex, requested, expected, packets_seen,
		 start->interval_us, cycles_to_us(sample_start, sample_end),
		 (unsigned long long)bit_count(raw_buf, requested),
		 (unsigned long long)bit_transitions(raw_buf, requested), min_jitter, mean_jitter,
		 max_jitter, raw_hex, jitter_hex, response_hex, seed_hash_hex);

	printk("%s", result_line);
	if (send_udp_result) {
		(void)send_text(fd, result_line);
	}

	maybe_dump_seed(start->trial_id, seed);
	maybe_dump_raw_hex(start->trial_id, raw_buf, requested);

	if (raw_len_out) {
		*raw_len_out = requested;
	}
	if (jitter_count_out) {
		*jitter_count_out = packets_seen;
	}

	memset(seed, 0, sizeof(seed));

	return 0;
}

static int create_client_socket(void)
{
	struct sockaddr_in gateway;
	int fd;
	int ret;

	fd = zsock_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
	if (fd < 0) {
		return -errno;
	}

	memset(&gateway, 0, sizeof(gateway));
	gateway.sin_family = AF_INET;
	gateway.sin_port = htons(AEB_GATEWAY_PORT);
	ret = zsock_inet_pton(AF_INET, AEB_GATEWAY_IP, &gateway.sin_addr);
	if (ret != 1) {
		(void)zsock_close(fd);
		return -EINVAL;
	}

	ret = zsock_connect(fd, (struct sockaddr *)&gateway, sizeof(gateway));
	if (ret < 0) {
		int err = errno;

		(void)zsock_close(fd);
		return -err;
	}

	return fd;
}

static int send_hello(int fd, uint32_t trial_id, const uint8_t nonce[16])
{
	uint8_t header[AEB_HEADER_LEN];

	build_msg(header, AEB_TYPE_HELLO, trial_id, AEB_BURST_COUNT, AEB_INTERVAL_US,
		  AEB_MAX_SAMPLE_BYTES, nonce);

	int ret = zsock_send(fd, header, sizeof(header), 0);

	if (ret < 0) {
		return -errno;
	}
	if (ret != sizeof(header)) {
		return -EIO;
	}
	return 0;
}

static int wait_for_start(int fd, uint32_t trial_id, struct aeb_msg *start)
{
	uint8_t recv_buf[AEB_RECV_BUF_LEN];
	int64_t deadline = k_uptime_get() + AEB_START_TIMEOUT_MS;

	while (k_uptime_get() < deadline) {
		struct zsock_pollfd pfd = {
			.fd = fd,
			.events = ZSOCK_POLLIN,
		};
		int remaining = (int)(deadline - k_uptime_get());
		int ret;

		if (remaining < 0) {
			remaining = 0;
		}

		ret = zsock_poll(&pfd, 1, remaining);
		if (ret <= 0) {
			return ret == 0 ? -ETIMEDOUT : -errno;
		}

		ret = zsock_recv(fd, recv_buf, sizeof(recv_buf), ZSOCK_MSG_DONTWAIT);
		if (ret < 0) {
			return -errno;
		}

		ret = parse_msg(recv_buf, (size_t)ret, start);
		if (ret != 0) {
			continue;
		}

		if (start->type == AEB_TYPE_START && start->trial_id == trial_id) {
			return 0;
		}
	}

	return -ETIMEDOUT;
}

static void client_hello_loop(const struct device *entropy_dev)
{
	int fd = create_client_socket();

	if (fd < 0) {
		printk("[AEB_ERR] client_socket,%d gateway=%s:%d\n", fd, AEB_GATEWAY_IP,
		       AEB_GATEWAY_PORT);
		return;
	}

	printk("[AEB_READY] mode=client_hello gateway=%s:%d trials=%d "
	       "sample_bytes=%d bursts=%d interval_us=%d raw_chunk_bytes=%d\n",
	       AEB_GATEWAY_IP, AEB_GATEWAY_PORT, AEB_CLIENT_TRIALS, AEB_MAX_SAMPLE_BYTES,
	       AEB_BURST_COUNT, AEB_INTERVAL_US, AEB_RAW_CHUNK_BYTES);

	for (uint32_t trial = 0; trial < AEB_CLIENT_TRIALS; trial++) {
		struct aeb_msg start;
		uint8_t nonce[16];
		uint32_t raw_len = 0;
		uint32_t jitter_count = 0;
		int ret;

		fill_public_nonce(trial, nonce);

		ret = send_hello(fd, trial, nonce);
		if (ret != 0) {
			printk("[AEB_ERR] trial=%u hello=%d\n", trial, ret);
			k_sleep(K_MSEC(AEB_TRIAL_GAP_MS));
			continue;
		}

		ret = wait_for_start(fd, trial, &start);
		if (ret != 0) {
			printk("[AEB_ERR] trial=%u start_timeout=%d\n", trial, ret);
			k_sleep(K_MSEC(AEB_TRIAL_GAP_MS));
			continue;
		}

		ret = run_trial(fd, entropy_dev, &start, true, &raw_len, &jitter_count);
		if (ret != 0) {
			printk("[AEB_ERR] trial=%u ret=%d\n", trial, ret);
			memset(raw_buf, 0, sizeof(raw_buf));
			k_sleep(K_MSEC(AEB_TRIAL_GAP_MS));
			continue;
		}

		ret = send_raw_stream(fd, &start, raw_buf, raw_len, jitter_us, jitter_count);
		if (ret != 0) {
			printk("[AEB_ERR] trial=%u raw_send=%d\n", trial, ret);
		}

		memset(raw_buf, 0, raw_len);
		k_sleep(K_MSEC(AEB_TRIAL_GAP_MS));
	}

	printk("[AEB_DONE] trials=%d\n", AEB_CLIENT_TRIALS);
	(void)zsock_close(fd);
}

#if !AEB_CLIENT_INITIATED
static int create_listener(void)
{
	struct sockaddr_in addr;
	int fd;
	int ret;

	fd = zsock_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
	if (fd < 0) {
		return -errno;
	}

	memset(&addr, 0, sizeof(addr));
	addr.sin_family = AF_INET;
	addr.sin_addr.s_addr = htonl(INADDR_ANY);
	addr.sin_port = htons(AEB_UDP_PORT);

	ret = zsock_bind(fd, (struct sockaddr *)&addr, sizeof(addr));
	if (ret < 0) {
		int err = errno;

		(void)zsock_close(fd);
		return -err;
	}

	return fd;
}

static void stimulus_loop(const struct device *entropy_dev)
{
	uint8_t recv_buf[AEB_RECV_BUF_LEN];
	int fd = create_listener();

	if (fd < 0) {
		printk("[AEB_ERR] listen,%d\n", fd);
		return;
	}

	printk("[AEB_READY] udp_port=%d max_sample_bytes=%d max_bursts=%d "
	       "dump_raw_hex=%d dump_seed=%d\n",
	       AEB_UDP_PORT, AEB_MAX_SAMPLE_BYTES, AEB_MAX_BURSTS, AEB_DUMP_RAW_HEX, AEB_DUMP_SEED);

	while (true) {
		struct aeb_msg msg;
		int ret;

		ret = zsock_recv(fd, recv_buf, sizeof(recv_buf), 0);
		if (ret < 0) {
			printk("[AEB_ERR] recv,%d\n", errno);
			continue;
		}

		ret = parse_msg(recv_buf, (size_t)ret, &msg);
		if (ret != 0) {
			printk("[AEB_ERR] parse,%d\n", ret);
			continue;
		}

		if (msg.type != AEB_TYPE_START) {
			continue;
		}

		ret = run_trial(fd, entropy_dev, &msg, false, NULL, NULL);
		if (ret != 0) {
			printk("[AEB_ERR] trial=%u ret=%d\n", msg.trial_id, ret);
		}
		memset(raw_buf, 0, sizeof(raw_buf));
	}
}
#endif /* !AEB_CLIENT_INITIATED */

int main(void)
{
	const struct device *entropy_dev;
	int ret;

	printk("\n========================================\n");
	printk("RF-Actuated Boot Entropy\n");
	printk("========================================\n");

	entropy_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_entropy));
	if (!device_is_ready(entropy_dev)) {
		printk("[AEB_FATAL] entropy device not ready\n");
		return 1;
	}

	printk("[AEB_META] entropy_device=%s\n", entropy_dev->name);
	printk("[AEB_META] seed_derivation=HKDF-SHA256(response_hash,transcript_hash)\n");
	printk("[AEB_META] raw_export=pre_hash_wdev_bytes\n");
	printk("[AEB_META] no_puf=true no_pre_shared_seed=true no_kem=true\n");

	wifi_set_event_logging(true);
	ret = wifi_init(NULL);
	if (ret != 0) {
		printk("[AEB_FATAL] wifi_init=%d\n", ret);
		return 1;
	}

	ret = connect_to_wifi();
	if (ret != 0) {
		printk("[AEB_FATAL] wifi_connect=%d\n", ret);
		return 1;
	}

#if AEB_CLIENT_INITIATED
	client_hello_loop(entropy_dev);
#else
	stimulus_loop(entropy_dev);
#endif
	return 0;
}
