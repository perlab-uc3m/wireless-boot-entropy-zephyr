# RF-Actuated Boot Entropy

This artifact tests a deliberately narrow idea:

> A public RF stimulus can trigger a device-local entropy event whose seed is
> extracted inside the node and is not known to the gateway that caused the
> event.

The stimulus is not secret. The firmware derives the seed only from local
measurements captured during the stimulus window:

- ESP32 WDEV RNG bytes sampled after each received burst packet.
- Packet-arrival timing deltas as observed by the device.
- A public transcript hash used only as HKDF salt/domain separation.

No PUF, SRAM startup state, pre-shared seed, device private key, DTLS, or KEM is
used here. The goal is to isolate the physical claim before combining
it with stronger bootstraps such as asymmetric entropy capsules.

## Threat Model

The adversary may observe the stimulus descriptor, nonce, packet burst schedule,
and all network traffic. The adversary may replay, delay, drop, inject, or jam
packets. Security requires that the device-local response retain min-entropy
conditioned on that public view:

```text
H_inf(local_response | public_stimulus, adversary_observations) > 0
```

If the local RNG is in a deterministic fallback mode, or if RF saturation makes
the source uncreditable, the artifact must report a failure to credit local
entropy. Passing statistical tests is not enough.

## Experiment

1. Start the host collector.
2. Flash or reset the ESP32 with the firmware.
3. The ESP32 joins Wi-Fi and sends a public boot `HELLO` to the collector.
4. The collector replies with a public `START` descriptor and then a burst of
   `BURST` packets.
5. For each burst packet, the ESP32 records the arrival cycle count and samples
   a slice of raw WDEV RNG output.
6. The ESP32 computes:

```text
response = raw_rng_bytes || packet_arrival_deltas
transcript = public nonce || trial id || burst parameters
seed = HKDF-SHA256(ikm=response, salt=SHA256(transcript),
                   info="rf-actuated-entropy-v1")
```

By default, only hashes are printed. Use `--dump-raw` or `--dump-seed` at build
time only for lab characterization. Raw dumps leak the candidate seed source.
For entropy-test batteries, prefer the collector's binary output:

```text
results/.../raw/aeb_all.bin
results/.../raw/aeb_trial_000000.bin
```

These files contain the pre-hash WDEV bytes only. They do not include packet
jitter, public nonces, transcript bytes, SHA-256 output, HKDF output, or other
conditioning. Packet-arrival deltas are stored separately under
`results/.../jitter/`.

## Core Conditions

- `idle`: host sends `START` only; the ESP32 samples without burst traffic.
- `burst-fixed`: repeated trials with the same nonce and same packet schedule.
  This is the critical within-device variability test.
- `burst-random`: repeated trials with fresh nonce values; useful operationally,
  but not sufficient by itself as evidence of entropy because the public nonce
  changes HKDF output.
- `interval-sweep`: repeat at several packet spacings such as 250, 1000, and
  5000 microseconds.
- `distance-rssi`: manually repeat near and far from the AP/stimulator.
- `interference`: repeat while a second board or host creates background Wi-Fi
  traffic.

## Build

Create `.env` or pass credentials directly:

```bash
cp .env.example .env
./scripts/build.sh --wifi-ssid MySSID --wifi-pass MyPass \
  --gateway-ip 192.168.1.50 --trials 128 \
  --max-sample-bytes 8192 --bursts 64 --interval-us 1000 \
  --clean --flash
```

The script uses the shared Zephyr workspace at the repository root.

## Client-Initiated Collector

Run this before resetting the ESP32:

```bash
./scripts/aeb_collector.py --port 7778 --trials 128 \
  --fixed-nonce --out-dir results/aeb_fixed_boothello
```

The collector writes:

- `raw/aeb_all.bin`: concatenation of complete raw WDEV trials.
- `raw/aeb_trial_*.bin`: one raw WDEV file per complete trial.
- `jitter/aeb_trial_*.csv`: packet-arrival deltas in microseconds.
- `aeb_trials.csv`: hashes, counts, packet loss, bit counts, and file paths.
- `manifest.json`: run parameters and the raw/conditioning boundary.

`--fixed-nonce` is recommended for the main variability test. The nonce is
public and is not entropy; keeping it fixed prevents a changing transcript from
explaining changing commitments.

## Stimulus Pattern

The baseline collector stimulus is deliberately deterministic and public. For
each `HELLO`, the collector sends one `START`, waits `--start-delay-ms`, then
sends `--bursts` `BURST` packets at `--interval-us` spacing. The default burst
payload is `64` bytes of ASCII `S`. In `--fixed-nonce` mode, the `START` nonce
is sixteen zero bytes for every trial.

No burst timing, nonce, or payload is counted as entropy. Optional randomized
stimuli are available with `--interval-jitter-us` and
`--payload-mode random`. These are useful for studying a weaker adversary who
misses part of the public stimulus, but they change the claim: the gateway is
then contributing an opportunistic remote uncertainty source. The collector logs
the exact schedule, payload bytes, and payload hashes under `stimulus/` so
randomized runs can be analyzed as public-metadata conditions rather than
silently credited as local entropy.

## Plot Results

Create a compact raw-run figure from a collector directory:

```bash
./scripts/plot_aeb_results.py results/aeb_boothello_entropy_1m_20260701_clean \
  --out results/aeb_boothello_entropy_1m_20260701_clean/aeb_summary_plot.png \
  --title "RF-Actuated Boot Entropy Raw Run"
```

The plot shows raw bit balance, pairwise raw-window Hamming distance, aggregate
byte-frequency deviation, and per-trial byte entropy estimates. Integrity
checks remain in `aeb_trials.csv` as `raw_sha256_match`. For publication figures,
omit `--title` and save as PDF.

## Legacy Stimulus Sender

After the ESP32 prints its IP address:

```bash
./scripts/stimulus_sender.py --target-ip 192.168.0.149 \
  --trials 100 --bursts 64 --interval-us 1000 --sample-bytes 4096 \
  --fixed-nonce --out results/stimulus_fixed.csv
```

For an idle control:

```bash
./scripts/stimulus_sender.py --target-ip 192.168.0.149 \
  --trials 100 --bursts 0 --sample-bytes 4096 --fixed-nonce \
  --out results/stimulus_idle.csv
```

## Parse ESP32 Logs

Save the serial monitor output and run:

```bash
./scripts/parse_aeb_log.py serial.log \
  --csv results/aeb_trials.csv \
  --summary results/aeb_summary.json
```

The parser reports unique response hashes, unique seed hashes, digest-level
pairwise Hamming distances, packet loss, raw monobit summaries, timing spread,
and optional raw-window statistics when raw hex logging was enabled.

## What Counts as Evidence

Good evidence:

- Same device, same public nonce, same public burst schedule, many distinct
  local response hashes.
- Optional raw captures have broad pairwise Hamming distances across trials.
- Packet-arrival deltas vary under fixed public stimulus.
- RF-disabled or deterministic-fallback controls are rejected or clearly marked
  as uncreditable.

Weak evidence:

- Only seed hashes change while the nonce also changes.
- Only post-HKDF output is tested, with no view of raw response variation.
- The source state is unknown.

## Reboots and Restart Tests

One boot can produce the long raw stream needed by ENT, PractRand, TestU01,
GM/T, AIS31-style screening, and SP 800-90B non-IID estimators. Restart tests
ask a different question: how variable are the first samples after reset? For
that, keep the collector running and reset or power-cycle the board many times,
then treat each per-trial file as one restart observation. Do not concatenate
restart observations blindly unless the target test expects a continuous stream.
