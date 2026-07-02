/*
 * Deterministic UDP-burst receiver for the RF-state WDEV benchmark.
 *
 * The host sends public, fixed-size UDP packets to the ESP32 while the
 * firmware streams raw WDEV bytes over UART.  This creates an AEB-like
 * gateway-to-DUT RF workload without mixing packet timing into the tested
 * WDEV byte stream.
 */

#ifndef UDP_FLOOD_H
#define UDP_FLOOD_H

#include <stdbool.h>
#include <stdint.h>

#ifndef UDP_BURST_PORT
#define UDP_BURST_PORT 9999
#endif

#ifndef UDP_BURST_PAYLOAD_SIZE
#define UDP_BURST_PAYLOAD_SIZE 64
#endif

#ifndef UDP_BURST_EXPECT_BYTE
#define UDP_BURST_EXPECT_BYTE 0x42
#endif

int udp_flood_start(void);
void udp_flood_stop(void);
void udp_flood_report_stats(void);

#endif /* UDP_FLOOD_H */
