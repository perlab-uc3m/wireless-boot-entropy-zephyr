#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/drivers/uart.h>

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define REGION_BYTES 4096U
#define DATA_BYTES_PER_LINE 64U
#define COMMAND_BYTES 32U

/*
 * This is the only object placed in the measured region. The linker map and
 * the address printed at runtime are retained with each experiment.
 */
__attribute__((section(".noinit.sram_startup_region"), aligned(16), used))
volatile uint8_t sram_startup_region[REGION_BYTES];

static uint8_t captured_region[REGION_BYTES];

static int capture_startup_region(void)
{
	for (size_t i = 0; i < REGION_BYTES; ++i) {
		captured_region[i] = sram_startup_region[i];
	}

	return 0;
}

/* Earliest portable Zephyr initialization level, before application main. */
SYS_INIT(capture_startup_region, EARLY, 0);

static char hex_digit(uint8_t value)
{
	return value < 10U ? (char)('0' + value) : (char)('a' + value - 10U);
}

static void print_capture(void)
{
	char hex[DATA_BYTES_PER_LINE * 2U + 1U];

	printk("[SRAM_CAPTURE_BEGIN] bytes=%u address=%p\n",
	       REGION_BYTES, (void *)sram_startup_region);

	for (size_t offset = 0; offset < REGION_BYTES;
	     offset += DATA_BYTES_PER_LINE) {
		size_t count = REGION_BYTES - offset;

		if (count > DATA_BYTES_PER_LINE) {
			count = DATA_BYTES_PER_LINE;
		}

		for (size_t i = 0; i < count; ++i) {
			uint8_t value = captured_region[offset + i];

			hex[2U * i] = hex_digit(value >> 4);
			hex[2U * i + 1U] = hex_digit(value & 0x0fU);
		}
		hex[2U * count] = '\0';
		printk("[SRAM_DATA] offset=%u hex=%s\n", (unsigned int)offset, hex);
	}

	printk("[SRAM_CAPTURE_END]\n");
}

static int parse_hex_byte(const char *text, uint8_t *value)
{
	uint8_t result = 0U;

	for (size_t i = 0; i < 2U; ++i) {
		char c = text[i];
		uint8_t nibble;

		if (c >= '0' && c <= '9') {
			nibble = (uint8_t)(c - '0');
		} else if (c >= 'a' && c <= 'f') {
			nibble = (uint8_t)(c - 'a' + 10);
		} else if (c >= 'A' && c <= 'F') {
			nibble = (uint8_t)(c - 'A' + 10);
		} else {
			return -1;
		}
		result = (uint8_t)((result << 4) | nibble);
	}

	*value = result;
	return 0;
}

static void fill_and_verify(uint8_t pattern)
{
	bool valid = true;

	for (size_t i = 0; i < REGION_BYTES; ++i) {
		sram_startup_region[i] = pattern;
	}

	for (size_t i = 0; i < REGION_BYTES; ++i) {
		if (sram_startup_region[i] != pattern) {
			valid = false;
			break;
		}
	}

	printk("[SRAM_ARMED] pattern=%02x verify=%s\n",
	       pattern, valid ? "ok" : "failed");
}

int main(void)
{
	const struct device *console = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
	char command[COMMAND_BYTES];
	size_t used = 0U;

	print_capture();
	printk("[SRAM_READY] command=FILL_HEX_BYTE\n");

	if (!device_is_ready(console)) {
		printk("[SRAM_ERROR] console_not_ready\n");
		return 1;
	}

	for (;;) {
		unsigned char c;

		if (uart_poll_in(console, &c) != 0) {
			k_sleep(K_MSEC(1));
			continue;
		}

		if (c == '\r' || c == '\n') {
			uint8_t pattern;

			command[used] = '\0';
			if (used == 7U && strncmp(command, "FILL ", 5U) == 0 &&
			    parse_hex_byte(&command[5], &pattern) == 0) {
				fill_and_verify(pattern);
			} else if (used != 0U) {
				printk("[SRAM_ERROR] invalid_command\n");
			}
			used = 0U;
			continue;
		}

		if (used + 1U < sizeof(command)) {
			command[used++] = (char)c;
		} else {
			used = 0U;
		}
	}

	return 0;
}
