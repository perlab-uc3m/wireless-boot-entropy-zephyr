/*
 * esp32-rf-rng-state/src/udp_flood.h
 *
 * Copyright (C) 2026 Javier Blanco-Romero @fj-blanco (UC3M, QURSA project)
 *
 * UDP traffic generator for the wifi_traffic condition.
 *
 * Sends and receives UDP packets at maximum rate to stress the ESP32's
 * RF subsystem, interrupt controller, and DMA without requiring any
 * TLS/DTLS or application-layer protocol complexity.
 *
 * The traffic target is a simple UDP echo server (e.g. `socat` or `ncat`)
 * running on the host machine:
 *
 *   socat -v UDP-LISTEN:9999,fork,reuseaddr PIPE
 *
 * or with ncat:
 *
 *   ncat -u -l 9999 --keep-open --exec "/bin/cat"
 */

#ifndef UDP_FLOOD_H
#define UDP_FLOOD_H

#include <stdint.h>
#include <stdbool.h>

/* Payload size per UDP packet (bytes) */
#define UDP_FLOOD_PAYLOAD_SIZE 1024

/* Inter-packet delay in microseconds (0 = tight loop) */
#define UDP_FLOOD_DELAY_US 0

/**
 * Start the UDP flood in a background thread.
 *
 * The flood runs until udp_flood_stop() is called. It sends
 * UDP_FLOOD_PAYLOAD_SIZE-byte packets to UDP_TARGET_IP:UDP_TARGET_PORT
 * and attempts to receive echoed responses.
 *
 * @return 0 on success, negative errno on failure.
 */
int udp_flood_start(void);

/**
 * Stop the UDP flood background thread.
 */
void udp_flood_stop(void);

/**
 * Report UDP flood statistics via BENCH_META.
 */
void udp_flood_report_stats(void);

#endif /* UDP_FLOOD_H */
