# Entropy Renewal

This Zephyr application measures local and network entropy supply during
repeated secure sessions.

The deployment stack is:

```text
ESP32 hardware RNG -> BLAKE2s pool -> wolfSSL RNG -> DTLS/PQC
remote DTLS entropy bytes -> BLAKE2s pool
```

The artifact is pinned to the QEaaS ESP32 stack:

| Component | Version / revision | Purpose |
| --- | --- | --- |
| Zephyr | `fj-blanco/zephyr@028d1947465c192509694cc1b8b5ef6bc7e1bad1` | ESP32 support and BLAKE2s entropy pool |
| wolfSSL | `wolfSSL/wolfssl@v5.8.2-stable` | DTLS 1.3, ML-KEM, ML-DSA |

The build also needs the wolfSSL settings and generated CA header from a local
QEaaS ESP32 client checkout. Pass that path with `--qeaas-client-root`.

## Measurement

Each fresh DTLS session emits one `[RENEWAL_ITER]` row with:

- `rng_block_bytes + rng_byte_bytes`: wolfSSL RNG output requested by the
  application stack.
- `rng_block_cycles`, `rng_byte_cycles`, and `rng_errors`: wrapper diagnostics.
- `local_hw_bytes`: bytes mixed from the ESP32 hardware backend by the pool.
- `external_bytes`: bytes mixed through `entropy_add_entropy()`.
- `pool_debit_bytes`: credited BLAKE2s pool bytes debited by extraction.
- `pool_credit_bits_pre/post`: credited pool balance before and after the
  iteration.
- `pool_timestamp_us_pre/post`: snapshot timestamps from the pool driver.

`pool_debit_bytes` is the pool-extraction numerator. It is deliberately separate
from application RNG output bytes, because a DRBG can expand a smaller credited
pool debit into a larger output stream.

## Instrumentation

wolfSSL is not patched. The benchmark uses GNU ld symbol wrapping:

```text
-Wl,--wrap=wc_RNG_GenerateBlock
-Wl,--wrap=wc_RNG_GenerateByte
```

The wrappers live in `src/renewal_rng_wrap.c` and count successful RNG output
bytes.

Zephyr is patched locally because the BLAKE2s pool needs a snapshot API for its
internal counters:

```text
patches/zephyr-028d194-blake2s-renewal-trace.patch
```

The build script applies the patch idempotently after `west update`.

## Build

Create a local `.env` from `.env.example` or pass Wi-Fi credentials directly.

```bash
cd entropy-renewal
./scripts/build.sh --wifi-ssid SSID --wifi-pass PASS \
  --qeaas-client-root ../qeaas_esp32_client --init
./scripts/build.sh --wifi-ssid SSID --wifi-pass PASS \
  --qeaas-client-root ../qeaas_esp32_client --clean --flash --monitor
```

The dependency checkouts are created under `.west-entropy-renewal/`. Application
build artifacts are written to `entropy-renewal/build/`.

Default build:

- `ML_KEM_512`
- `ML_DSA_44`
- DTLS peer verification enabled
- 50 measured fresh sessions
- 3 warm-up sessions
- 1 second between sessions
- 32 raw entropy bytes fetched over DTLS but not mixed into the pool unless
  `--network-inject` is enabled

Local-only run:

```bash
./scripts/build.sh --wifi-ssid SSID --wifi-pass PASS \
  --qeaas-client-root ../qeaas_esp32_client \
  --groups ML_KEM_512 --sig ML_DSA_44 \
  --iterations 50 --inter-ms 1000 \
  --clean --flash --monitor
```

Network-assisted run:

```bash
./scripts/build.sh --wifi-ssid SSID --wifi-pass PASS \
  --qeaas-client-root ../qeaas_esp32_client \
  --groups ML_KEM_512 --sig ML_DSA_44 \
  --dtls-bytes 32 --network-inject \
  --iterations 50 --inter-ms 1000 \
  --clean --flash --monitor
```

After-bootstrap remote-assisted ablation:

```bash
./scripts/build.sh --wifi-ssid SSID --wifi-pass PASS \
  --qeaas-client-root ../qeaas_esp32_client \
  --groups ML_KEM_512 --sig ML_DSA_44 \
  --dtls-bytes 32 --network-inject --disable-local-refill \
  --iterations 20 --inter-ms 1000 \
  --clean --flash --monitor
```

This ablation still uses the local hardware source at boot. It disables later
local refills so the network contribution is easier to isolate.

Classical baseline:

```bash
./scripts/build.sh --wifi-ssid SSID --wifi-pass PASS \
  --qeaas-client-root ../qeaas_esp32_client \
  --groups P-256 --sig ECDSA_P256 \
  --iterations 50 --inter-ms 1000 \
  --clean --flash --monitor
```

## Parse Logs

Capture the monitor output to a text file, then run:

```bash
python3 scripts/parse_renewal_log.py logs/mlkem512_mldsa44_local.txt \
  --csv data/mlkem512_mldsa44_local.csv \
  --summary data/mlkem512_mldsa44_local_summary.json
```

The summary reports:

- `mu_rng_output_bytes_mean`
- `lambda_out_wall_Bps`
- `lambda_local_wall_Bps`
- `lambda_net_wall_Bps`
- `lambda_supply_wall_Bps`
- `renewal_margin_wall_Bps`
- `drbg_expansion_factor`
- credited pool start/end

Use wall-clock rates for the renewal envelope and active-window rates for
implementation overhead diagnostics.

## Plot Results

```bash
python3 scripts/plot_renewal.py data -o figures
```

The script writes:

- `fig_renewal_rates.pdf`: local and remote supply, pool debit, and mean `mu`.
- `fig_renewal_pool_trace.pdf`: credited pool balance across fresh sessions.
- `renewal_summary_table.csv`: numeric values behind the figures.

## Network Entropy Crediting

The client credits only raw DTLS application bytes received from the entropy
server. There is no JSON, hex, or CoAP framing in the measurement path. With
`--network-inject`, the firmware mixes at most `--dtls-bytes` bytes through
`entropy_add_entropy()` and credits them at 8 bits per accepted byte.
