# ESP32 RF RNG State

This artifact measures the ESP32 WDEV RNG path under different RF operating
states.

The goal is source-state awareness. The ESP32 can return bytes that pass generic
statistical screens even when the RF subsystem is disabled and the hardware
state should not receive true-entropy credit. This artifact keeps the RF state
visible while collecting raw binary streams for external test batteries.

It uses stock Zephyr v4.1.0 and reads through the stock `entropy_esp32` driver.
The custom BLAKE2s entropy pool is not in this measurement path.

## Conditions

| Label | Wi-Fi state | Traffic | Purpose |
| --- | --- | --- | --- |
| `rf_disabled` | Disabled at boot | None | Pseudorandom control |
| `wifi_idle` | Associated, DHCP obtained | Keep-alive only | RF-enabled baseline |
| `wifi_scan` | Associated, scanning | Periodic scan | RF activity without app payload |
| `wifi_traffic` | Associated, DHCP obtained | UDP flood | RF, interrupt, and DMA stress |

For each condition, the firmware reports:

- throughput for `entropy_get_entropy()` calls
- per-sample latency
- raw WDEV bytes over UART

## Build and Run

Each condition uses a separate firmware build because the Wi-Fi driver changes
the RF subsystem state at boot.

Wi-Fi conditions require Espressif HAL blobs. The helper scripts fetch them
before Wi-Fi builds. If a manually initialized workspace reports missing blobs,
run this once:

```bash
west blobs fetch hal_espressif
```

Recommended commands:

```bash
cd esp32-rf-rng-state
. .venv-zephyr/bin/activate

./scripts/run_condition.sh --condition rf_disabled --port /dev/ttyUSB0

./scripts/run_condition.sh --condition wifi_idle \
  --wifi-ssid SSID --wifi-pass PASS --port /dev/ttyUSB0 --raw-delay-ms 1000

./scripts/run_condition.sh --condition wifi_scan \
  --wifi-ssid SSID --wifi-pass PASS --port /dev/ttyUSB0 --raw-delay-ms 1000

./scripts/run_condition.sh --condition wifi_traffic \
  --wifi-ssid SSID --wifi-pass PASS --port /dev/ttyUSB0 --raw-delay-ms 1000
```

Outputs are written to `data/<condition>_<bytes>.bin` by default. Long captures
print progress every 30 seconds. Use `--progress-interval <seconds>` to change
that cadence.

For long captures from an editor terminal, use the detached launcher:

```bash
./scripts/run_condition_detached.sh --condition wifi_scan \
  --wifi-ssid SSID --wifi-pass PASS --port /dev/ttyUSB0 --raw-delay-ms 1000
./scripts/check_condition.sh --condition wifi_scan
```

## Manual Capture

Build and flash without `--monitor`, because the capture script needs exclusive
serial access:

```bash
./scripts/build.sh --condition rf_disabled --clean --flash
./scripts/build.sh --condition wifi_idle --wifi-ssid SSID --wifi-pass PASS \
  --clean --flash
./scripts/build.sh --condition wifi_scan --wifi-ssid SSID --wifi-pass PASS \
  --clean --flash
./scripts/build.sh --condition wifi_traffic --wifi-ssid SSID --wifi-pass PASS \
  --clean --flash
```

Then capture the raw stream:

```bash
python3 scripts/capture_binary.py \
  --port /dev/ttyUSB0 --baud 921600 --output data/wifi_idle.bin
```

Useful options:

- `--bytes`: byte count to capture. If omitted, the script reads the firmware
  `[BENCH_META] raw_dump_bytes` field.
- `--output`: binary output path.
- `--progress-interval`: seconds between progress lines.

## Randlab

From a workspace with `randlab` checked out as a sibling:

```bash
cd ../randlab
randlab run \
  --input ../wireless-boot-entropy-zephyr/esp32-rf-rng-state/data/wifi_idle_268435456.bin \
  --format raw --profile paper \
  --suite practrand --suite testu01-rabbit --suite testu01-alphabit \
  --suite testu01-block-alphabit --suite gmt-sts \
  --suite entropy-iid --suite entropy-non-iid --suite entropy-restart \
  --suite ais31-p1-t0 --suite ais31-p1-t1-t5 --suite ais31-p2 \
  --suite borel --suite ent \
  --out ../wireless-boot-entropy-zephyr/esp32-rf-rng-state/results/wifi_idle_256m
```

## Output Format

The firmware prints structured serial records:

```text
[BENCH_START]
[BENCH_META] condition,wifi_idle
[BENCH_META] board,esp32_devkitc_wroom
[BENCH_META] sample_size,32
[BENCH_META] iterations,500
[BENCH] trng_latency,32,0,42
[BENCH] trng_latency,32,1,41
[BENCH] trng_throughput,32,0,23456
[BENCH_RAW_START]
...
[BENCH_RAW_END]
[BENCH_END]
```

Use `scripts/compare_results.py` and `scripts/plot_results.py` for local
summary plots after the raw streams have been analyzed.
