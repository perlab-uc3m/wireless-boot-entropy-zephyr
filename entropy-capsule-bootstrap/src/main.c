#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/drivers/entropy.h>
#include <zephyr/drivers/entropy_blake2s_renewal.h>
#include <zephyr/kernel.h>
#include <zephyr/net/socket.h>
#include <zephyr/sys/mem_stats.h>
#include <zephyr/sys/sys_heap.h>

#include "teb_crypto.h"
#include "teb_pq_client_keys.h"
#include "teb_protocol.h"
#include "teb_puf.h"
#include "wifi.h"

#ifdef CONFIG_SYS_HEAP_RUNTIME_STATS
extern struct k_heap _system_heap;
#endif

#ifndef TEB_SERVER_IP
#define TEB_SERVER_IP "192.168.1.136"
#endif

#ifndef TEB_SERVER_PORT
#define TEB_SERVER_PORT 6767
#endif

#ifndef TEB_DEVICE_ID
#define TEB_DEVICE_ID 0x4553503332544542ULL
#endif

#if TEB_SELECTED_PROFILE == TEB_PROFILE_DEV_ED25519_PUF_BEACON
static const uint8_t gateway_ed25519_pub[TEB_ED25519_PUB_SIZE] = {
	0x91, 0xb5, 0x57, 0x29, 0xfd, 0x6d, 0x88, 0x1d, 0xf8, 0xdc, 0x43,
	0xee, 0x86, 0x03, 0x28, 0x0b, 0x95, 0x46, 0xe8, 0x75, 0xfb, 0x18,
	0x3e, 0x53, 0x3d, 0x0f, 0xa5, 0xe9, 0xb5, 0xd3, 0xeb, 0x1d};
#endif

struct retained_counter {
	uint32_t magic;
	uint32_t value;
	uint32_t check;
};

__attribute__((section(".noinit.teb_counter"), aligned(4),
	       used)) static volatile struct retained_counter retained_boot_counter;

#define TEB_COUNTER_MAGIC 0x54454243U

static uint32_t next_boot_counter(void)
{
	uint32_t value;

	if (retained_boot_counter.magic != TEB_COUNTER_MAGIC ||
	    retained_boot_counter.check != (retained_boot_counter.value ^ TEB_COUNTER_MAGIC)) {
		value = 1;
	} else {
		value = retained_boot_counter.value + 1U;
	}

	retained_boot_counter.magic = TEB_COUNTER_MAGIC;
	retained_boot_counter.value = value;
	retained_boot_counter.check = value ^ TEB_COUNTER_MAGIC;

	return value;
}

static void report_heap(const char *label)
{
#ifdef CONFIG_SYS_HEAP_RUNTIME_STATS
	struct sys_memory_stats stats;

	if (sys_heap_runtime_stats_get(&_system_heap.heap, &stats) == 0) {
		printf("[TEB_META] heap_free_%s,%zu\n", label, stats.free_bytes);
		printf("[TEB_META] heap_used_%s,%zu\n", label, stats.allocated_bytes);
		printf("[TEB_META] heap_peak_%s,%zu\n", label, stats.max_allocated_bytes);
	}
#else
	ARG_UNUSED(label);
#endif
}

static int setup_destination(struct sockaddr_in *dst)
{
	memset(dst, 0, sizeof(*dst));
	dst->sin_family = AF_INET;
	dst->sin_port = htons(TEB_SERVER_PORT);

	if (inet_pton(AF_INET, TEB_SERVER_IP, &dst->sin_addr) <= 0) {
		return -EINVAL;
	}

	return 0;
}

struct teb_exchange_metrics {
	uint32_t exchange_ms;
	uint32_t send_us;
	uint32_t wait_ms;
};

static int request_capsule(const uint8_t hello[TEB_HELLO_LEN], uint8_t capsule[TEB_CAPSULE_LEN],
			   struct teb_exchange_metrics *metrics)
{
	struct sockaddr_in dst;
	struct timeval timeout = {
		.tv_sec = 10,
		.tv_usec = 0,
	};
	int fd;
	int ret;
	uint32_t exchange_start_ms;
	uint32_t send_start_cycles;
	uint32_t send_end_cycles;
	uint32_t wait_start_ms;
	uint32_t recv_end_ms;

	ret = setup_destination(&dst);
	if (ret < 0) {
		return ret;
	}

	fd = zsock_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
	if (fd < 0) {
		return -errno;
	}

	(void)zsock_setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	(void)zsock_setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

	printf("[TEB_NET] connect,%s:%d\n", TEB_SERVER_IP, TEB_SERVER_PORT);
	ret = zsock_connect(fd, (const struct sockaddr *)&dst, sizeof(dst));
	if (ret < 0) {
		int err = errno;

		(void)zsock_close(fd);
		return -err;
	}

	printf("[TEB_NET] send_hello,%u\n", TEB_HELLO_LEN);
	exchange_start_ms = k_uptime_get_32();
	send_start_cycles = k_cycle_get_32();
	ret = zsock_send(fd, hello, TEB_HELLO_LEN, 0);
	send_end_cycles = k_cycle_get_32();
	if (ret != TEB_HELLO_LEN) {
		int err = errno;

		(void)zsock_close(fd);
		return ret < 0 ? -err : -EIO;
	}

	printf("[TEB_NET] recv_capsule_wait,%u\n", TEB_CAPSULE_LEN);
	struct zsock_pollfd pfd = {
		.fd = fd,
		.events = ZSOCK_POLLIN,
	};

	wait_start_ms = k_uptime_get_32();
	ret = zsock_poll(&pfd, 1, 10000);
	if (ret == 0) {
		(void)zsock_close(fd);
		return -ETIMEDOUT;
	}
	if (ret < 0) {
		int err = errno;

		(void)zsock_close(fd);
		return -err;
	}

	ret = zsock_recv(fd, capsule, TEB_CAPSULE_LEN, ZSOCK_MSG_DONTWAIT);
	if (ret != TEB_CAPSULE_LEN) {
		int err = errno;

		(void)zsock_close(fd);
		return ret < 0 ? -err : -EMSGSIZE;
	}
	recv_end_ms = k_uptime_get_32();

	if (metrics != NULL) {
		metrics->exchange_ms = recv_end_ms - exchange_start_ms;
		metrics->send_us = k_cyc_to_us_floor32(send_end_cycles - send_start_cycles);
		metrics->wait_ms = recv_end_ms - wait_start_ms;
	}
	printf("[TEB_METRIC] capsule_exchange_ms,%u\n", recv_end_ms - exchange_start_ms);
	printf("[TEB_METRIC] hello_send_us,%u\n",
	       k_cyc_to_us_floor32(send_end_cycles - send_start_cycles));
	printf("[TEB_METRIC] capsule_wait_ms,%u\n", recv_end_ms - wait_start_ms);

	(void)zsock_close(fd);
	return 0;
}

static int accept_capsule(const struct device *entropy_dev, const uint8_t hello[TEB_HELLO_LEN],
			  const struct teb_hello_fields *hello_fields,
			  const struct teb_puf_sample *puf, const uint8_t capsule[TEB_CAPSULE_LEN])
{
	struct teb_capsule_fields fields;
	uint8_t hello_hash[TEB_HASH_SIZE];
	uint8_t ikm[TEB_HASH_SIZE * 2 + TEB_MLKEM512_SHARED_SECRET_SIZE];
	uint8_t salt[TEB_HASH_SIZE * 2];
	uint8_t seed[TEB_BOOT_SEED_SIZE];
	uint8_t pq_shared_secret[TEB_MLKEM512_SHARED_SECRET_SIZE];
	uint8_t pq_ciphertext_hash[TEB_HASH_SIZE];
#if TEB_SELECTED_PROFILE == TEB_PROFILE_DEV_ED25519_PUF_BEACON
	static const uint8_t info[] = "TEB-PUF-BEACON-v1";
#else
	static const uint8_t info[] = "TEB-PQ-MLKEM512-MLDSA44-v1";
#endif
	uint32_t start;
	uint32_t end;
	int ret;

	ret = teb_parse_capsule(capsule, TEB_CAPSULE_LEN, &fields);
	if (ret < 0) {
		printf("[TEB_ERR] parse_capsule,%d\n", ret);
		return ret;
	}

	if (fields.profile != TEB_SELECTED_PROFILE) {
		printf("[TEB_ERR] profile,%u\n", fields.profile);
		return -EPROTONOSUPPORT;
	}

	if (fields.device_id != hello_fields->device_id ||
	    fields.boot_counter != hello_fields->boot_counter) {
		printf("[TEB_ERR] capsule_binding\n");
		return -EACCES;
	}

	ret = teb_sha256(hello, TEB_HELLO_LEN, hello_hash);
	if (ret != 0) {
		return ret;
	}

	if (memcmp(hello_hash, fields.hello_hash, TEB_HASH_SIZE) != 0) {
		printf("[TEB_ERR] hello_hash_mismatch\n");
		return -EACCES;
	}

	start = k_cycle_get_32();
#if TEB_SELECTED_PROFILE == TEB_PROFILE_DEV_ED25519_PUF_BEACON
	ret = teb_verify_ed25519(gateway_ed25519_pub, capsule, TEB_CAPSULE_SIGNED_LEN,
				 fields.signature);
#else
	ret = teb_verify_mldsa44(teb_mldsa44_public_key, capsule, TEB_CAPSULE_SIGNED_LEN,
				 fields.signature);
#endif
	end = k_cycle_get_32();
	printf("[TEB_METRIC] verify_us,%u\n", k_cyc_to_us_floor32(end - start));
	if (ret != 0) {
		printf("[TEB_ERR] signature,%d\n", ret);
		return ret;
	}

#if TEB_SELECTED_PROFILE == TEB_PROFILE_PQ_MLKEM512_MLDSA44
	start = k_cycle_get_32();
	ret = teb_mlkem512_decapsulate(teb_mlkem512_private_key, fields.kem_ciphertext,
				       pq_shared_secret);
	end = k_cycle_get_32();
	printf("[TEB_METRIC] kem_decaps_us,%u\n", k_cyc_to_us_floor32(end - start));
	if (ret != 0) {
		printf("[TEB_ERR] mlkem_decaps,%d\n", ret);
		return ret;
	}

	ret = teb_sha256(fields.kem_ciphertext, TEB_MLKEM512_CIPHERTEXT_SIZE, pq_ciphertext_hash);
	if (ret != 0) {
		return ret;
	}
#else
	memcpy(pq_shared_secret, fields.beacon, TEB_BEACON_SIZE);
	ret = teb_sha256(fields.beacon, TEB_BEACON_SIZE, pq_ciphertext_hash);
	if (ret != 0) {
		return ret;
	}
#endif

	memcpy(ikm, puf->sram_secret, TEB_HASH_SIZE);
	memcpy(ikm + TEB_HASH_SIZE, puf->timing_secret, TEB_HASH_SIZE);
	memcpy(ikm + TEB_HASH_SIZE * 2, pq_shared_secret, TEB_MLKEM512_SHARED_SECRET_SIZE);
	memcpy(salt, pq_ciphertext_hash, TEB_HASH_SIZE);
	memcpy(salt + TEB_HASH_SIZE, fields.hello_hash, TEB_HASH_SIZE);

	start = k_cycle_get_32();
	ret = teb_hkdf_sha256(ikm, sizeof(ikm), salt, sizeof(salt), info, sizeof(info) - 1, seed,
			      sizeof(seed));
	end = k_cycle_get_32();
	printf("[TEB_METRIC] hkdf_us,%u\n", k_cyc_to_us_floor32(end - start));
	if (ret != 0) {
		printf("[TEB_ERR] hkdf,%d\n", ret);
		return ret;
	}

	ret = entropy_add_entropy(entropy_dev, seed, sizeof(seed), sizeof(seed) * 8U);
	if (ret != 0) {
		printf("[TEB_ERR] entropy_add,%d\n", ret);
		return ret;
	}

	printf("[TEB_META] gateway_time_ms,%llu\n", (unsigned long long)fields.gateway_time_ms);
	printf("[TEB_META] gateway_sequence,%u\n", fields.sequence);
	teb_print_hex("[TEB_SEED_COMMIT] ", seed, sizeof(seed));

	memset(ikm, 0, sizeof(ikm));
	memset(pq_shared_secret, 0, sizeof(pq_shared_secret));
	memset(seed, 0, sizeof(seed));
	return 0;
}

int main(void)
{
	const struct device *entropy_dev;
	struct teb_puf_sample puf;
	struct teb_hello_fields hello_fields;
	uint8_t hello[TEB_HELLO_LEN];
	uint8_t capsule[TEB_CAPSULE_LEN];
	struct entropy_blake2s_renewal_stats stats;
	struct teb_exchange_metrics exchange_metrics;
	uint32_t reset_ms = k_uptime_get_32();
	uint32_t boot_counter;
	int ret;

	printf("\n========================================\n");
	printf("Asymmetric Entropy Capsule Bootstrap\n");
	printf("Profile: %s\n", TEB_PROFILE_NAME);
	printf("Server: %s:%d\n", TEB_SERVER_IP, TEB_SERVER_PORT);
	printf("========================================\n");

	entropy_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_entropy));
	if (!device_is_ready(entropy_dev)) {
		printf("[TEB_ERR] entropy_not_ready\n");
		return 1;
	}

	boot_counter = next_boot_counter();

#if TEB_DISABLE_LOCAL_REFILL
	entropy_blake2s_renewal_set_hw_refill_enabled(false);
	printf("[TEB_META] local_hw_refill,disabled_after_init\n");
#else
	printf("[TEB_META] local_hw_refill,enabled\n");
#endif

	teb_puf_capture(&puf);
	teb_print_hex("[TEB_PUF] sram_commitment,", puf.sram_commitment, TEB_HASH_SIZE);
	teb_print_hex("[TEB_PUF] timing_commitment,", puf.timing_commitment, TEB_HASH_SIZE);
	printf("[TEB_PUF] sram_ones,%u\n", puf.sram_ones);
	printf("[TEB_PUF] sram_transitions,%u\n", puf.sram_transitions);
	printf("[TEB_PUF] timing_min_delta,%u\n", puf.timing_min_delta);
	printf("[TEB_PUF] timing_max_delta,%u\n", puf.timing_max_delta);
	printf("[TEB_PUF] timing_sum_delta,%llu\n", (unsigned long long)puf.timing_sum_delta);

	memset(&hello_fields, 0, sizeof(hello_fields));
	hello_fields.device_id = TEB_DEVICE_ID;
	hello_fields.boot_counter = boot_counter;
	hello_fields.uptime_ms = k_uptime_get_32();
	memcpy(hello_fields.sram_commitment, puf.sram_commitment, TEB_HASH_SIZE);
	memcpy(hello_fields.timing_commitment, puf.timing_commitment, TEB_HASH_SIZE);
	teb_build_hello(hello, &hello_fields);

	printf("[TEB_META] device_id,0x%llx\n", (unsigned long long)hello_fields.device_id);
	printf("[TEB_META] boot_counter,%u\n", hello_fields.boot_counter);
	report_heap("before_wifi");

	wifi_set_event_logging(true);
	wifi_init(NULL);
	ret = connect_to_wifi();
	if (ret != 0) {
		printf("[TEB_ERR] wifi,%d\n", ret);
		return 1;
	}
	ret = wait_for_ipv4_address();
	if (ret != 0) {
		printf("[TEB_ERR] ipv4,%d\n", ret);
		return 1;
	}
	k_sleep(K_MSEC(1000));

	report_heap("before_capsule");

	memset(&exchange_metrics, 0, sizeof(exchange_metrics));
	ret = request_capsule(hello, capsule, &exchange_metrics);
	if (ret != 0) {
		printf("[TEB_ERR] request_capsule,%d\n", ret);
		return 1;
	}

	ret = accept_capsule(entropy_dev, hello, &hello_fields, &puf, capsule);
	if (ret != 0) {
		printf("[TEB_RESULT] failed,%d\n", ret);
		return 1;
	}

	if (entropy_blake2s_renewal_snapshot(&stats) == 0) {
		printf("[TEB_POOL] credited_bits,%u\n", stats.credited_pool_bits);
		printf("[TEB_POOL] external_bytes,%llu\n",
		       (unsigned long long)stats.total_external_bytes);
		printf("[TEB_POOL] hw_bytes,%llu\n", (unsigned long long)stats.total_hw_bytes);
	}

	report_heap("after_capsule");
	printf("[TEB_METRIC] time_to_seed_ms,%u\n", k_uptime_get_32() - reset_ms);
	printf("[TEB_RESULT] seeded\n");

	return 0;
}
