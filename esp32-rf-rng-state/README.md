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
| `udp_burst` | Associated, DHCP obtained | Deterministic gateway-to-DUT UDP bursts | AEB-like RF stress while measuring WDEV bytes |

`wifi_traffic` is accepted by the helper scripts as a legacy alias for
`udp_burst`. The old ESP32-to-host flood direction is no longer the default
because it does not isolate the gateway-to-DUT RF workload used by the
entropization mechanism.

For each condition, the firmware reports:

- throughput for `entropy_get_entropy()` calls
- per-sample latency
- raw WDEV bytes over UART

## Build and Run

Each condition uses a separate firmware build because the Wi-Fi driver changes
the RF subsystem state at boot.

Wi-Fi conditions require an initialized Zephyr workspace with the Espressif HAL
module and blobs. If the build reports missing blobs, run this once from the
Zephyr workspace:

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

./scripts/run_condition.sh --condition udp_burst \
  --wifi-ssid SSID --wifi-pass PASS --port /dev/ttyUSB0 --raw-delay-ms 1000 \
  --udp-port 9999 --udp-payload-bytes 64 --udp-byte 0x42 --udp-interval-us 1000
```

Outputs are written to `data/<condition>_<bytes>.bin` by default. Long captures
print progress every 30 seconds. Use `--progress-interval <seconds>` to change
that cadence.

For paper captures across multiple boards, prefer a board- and date-labeled
output directory, for example:

```bash
./scripts/run_condition.sh --condition udp_burst \
  --wifi-ssid SSID --wifi-pass PASS --port /dev/ttyUSB0 \
  --output-dir ../../paper/data/rf_rng_state_esp32_devkitc_v4_wroom32_20260702/raw
```

If the local Zephyr tree uses the newer board name, add:

```bash
--board esp32_devkitc/esp32/procpu
```

For the older Zephyr board naming used by the complete local v3.7 workspace,
the default is `esp32_devkitc_wroom/esp32/procpu`.

For long captures from an editor terminal, use the detached launcher:

```bash
./scripts/run_condition_detached.sh --condition wifi_scan \
  --wifi-ssid SSID --wifi-pass PASS --port /dev/ttyUSB0 --raw-delay-ms 1000
./scripts/check_condition.sh --condition wifi_scan
```

## Wi-Fi Idle Reboot Slices

To check whether Wi-Fi-idle RNG quality changes after repeated resets, use the
reboot-slice collector. It builds and flashes one `wifi_idle` firmware image,
then resets the ESP32 before each short raw dump. The per-boot files are kept
separate and concatenated for aggregate analysis.

The recommended paper-sized run captures 256 MiB total as 32 reset slices of
8 MiB each:

```bash
python3 scripts/collect_reboot_slices.py \
  --port /dev/ttyUSB0 \
  --runs 32 \
  --total-bytes 268435456 \
  --raw-delay-ms 1000 \
  --output-root ../../paper/data/wifi_idle_reboot_esp32_devkitc_wroom32_YYYYMMDD
```

Use `--wifi-ssid` and `--wifi-pass` if they are not supplied by `.env` or the
environment. Use `--skip-build` to reuse an already flashed matching image.

Analyze the resulting dataset:

```bash
python3 scripts/analyze_reboot_slices.py \
  --dataset-root ../../paper/data/wifi_idle_reboot_esp32_devkitc_wroom32_YYYYMMDD \
  --run-randlab
```

The analyzer writes per-reboot metrics, fixed-window metrics, a JSON summary,
and a Markdown interpretation under `results/`. With `--run-randlab`, it also
runs the paper-profile randlab battery on the concatenated stream. Use
`--window-bytes` to change the within-reboot chunk size and `--prefix-bytes`
to change the prefix length checked for cross-reboot repeats.

This benchmark is a post-association Wi-Fi-idle reset test. It is useful for
detecting reset-to-reset degradation or concentration of failures in later
slices, but it is not a first-instruction cold-boot entropy capture.

## Manual Capture

Build and flash without `--monitor`, because the capture script needs exclusive
serial access:

```bash
./scripts/build.sh --condition rf_disabled --clean --flash
./scripts/build.sh --condition wifi_idle --wifi-ssid SSID --wifi-pass PASS \
  --clean --flash
./scripts/build.sh --condition wifi_scan --wifi-ssid SSID --wifi-pass PASS \
  --clean --flash
./scripts/build.sh --condition udp_burst --wifi-ssid SSID --wifi-pass PASS \
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
- `--udp-burst`: send fixed UDP packets during capture.
- `--udp-target-ip`: override ESP32 IPv4 if DHCP parsing is not available.
- `--udp-port`, `--udp-payload-bytes`, `--udp-byte`, `--udp-interval-us`:
  deterministic burst parameters.

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

For a board/date paper dataset:

```bash
./scripts/analyze_dataset.sh \
  --raw-dir ../../paper/data/rf_rng_state_esp32_devkitc_v4_wroom32_20260702/raw \
  --results-dir ../../paper/data/rf_rng_state_esp32_devkitc_v4_wroom32_20260702/results \
  --label 256m --profile paper
```
