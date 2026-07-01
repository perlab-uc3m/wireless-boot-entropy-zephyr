# wireless-boot-entropy-zephyr

Zephyr and ESP32 experiments around boot-time *entropization* for boot-starved wireless sensors.

## Subprojects

- [`rf-actuated-boot-entropy`](rf-actuated-boot-entropy/README.md):
  client-initiated RF stimulus experiment. The ESP32 sends a boot `HELLO`,
  samples pre-hash WDEV bytes, uploads raw trial files, and records seed
  commitments.
- [`esp32-rf-rng-state`](esp32-rf-rng-state/README.md): ESP32 WDEV RNG
  characterization under RF-disabled, Wi-Fi idle, Wi-Fi scan, and traffic
  states.
- [`entropy-capsule-bootstrap`](entropy-capsule-bootstrap/README.md):
  asymmetric entropy capsule prototype. The gateway pays the first randomized
  cryptographic cost; the client verifies, decapsulates, and mixes
  deterministically before DTLS.
- [`entropy-renewal`](entropy-renewal/README.md): local and network entropy
  renewal experiment during repeated secure sessions.

## Previous Work

This repository builds on the
[QEaaS ESP32 client](https://github.com/qursa-uc3m/qeaas_esp32_client), the
Zephyr/ESP32 implementation behind *Post-Quantum Entropy as a Service for
Embedded Systems*. That client retrieves quantum entropy over CoAP/DTLS and
mixes it with local hardware entropy in a custom BLAKE2s entropy pool.

The BLAKE2s pool lives in the Zephyr fork
[fj-blanco/zephyr](https://github.com/fj-blanco/zephyr). The subrepo here use
two Zephyr lines:

- `rf-actuated-boot-entropy` and `esp32-rf-rng-state` use stock Zephyr v4.1.0.
  These experiments read the ESP32 WDEV RNG path without the custom pool, so
  source-state measurements stay close to the hardware driver.
- `entropy-capsule-bootstrap` and `entropy-renewal` use the `fj-blanco/zephyr`
  fork at commit `028d1947465c192509694cc1b8b5ef6bc7e1bad1`, matching the
  QEaaS ESP32 stack used for the BLAKE2s entropy-pool experiments.

## Zephyr Workspace

Each subrepo has its own `west.yml`. A shared local West workspace can live at
this repo root:

```bash
cd rf-actuated-boot-entropy
west init -l .
west update
west blobs fetch hal_espressif
```

Generated workspace content such as `.west/`, `zephyr/`, `modules/`, `tools/`,
`bootloader/`, and `zephyr-sdk-*` is ignored.

## Minimal Checks

```bash
ruff check \
  rf-actuated-boot-entropy/scripts \
  esp32-rf-rng-state/scripts \
  entropy-capsule-bootstrap/scripts \
  entropy-capsule-bootstrap/server/teb_beacon_server.py \
  entropy-renewal/scripts

ruff format --check \
  rf-actuated-boot-entropy/scripts \
  esp32-rf-rng-state/scripts \
  entropy-capsule-bootstrap/scripts \
  entropy-capsule-bootstrap/server/teb_beacon_server.py \
  entropy-renewal/scripts

find rf-actuated-boot-entropy esp32-rf-rng-state \
  entropy-capsule-bootstrap entropy-renewal \
  -name '*.sh' -exec bash -n {} \;

find rf-actuated-boot-entropy esp32-rf-rng-state \
  entropy-capsule-bootstrap entropy-renewal \
  -path '*/build' -prune -o \
  -name 'teb_pq_client_keys.h' -prune -o \
  -name 'teb_pq_server_keys.h' -prune -o \
  -type f \( -name '*.c' -o -name '*.h' \) \
  -exec clang-format --dry-run --Werror -style=file {} +
```

Hardware runs need an ESP32 over USB, Wi-Fi credentials, and the relevant host
collector or capsule server.

## Data Boundary

Entropy tests should run on raw source files, not on HKDF output.

For the RF-actuated artifact, the preferred input is the collector's binary raw
stream:

```text
rf-actuated-boot-entropy/results/.../raw/aeb_all.bin
```

That file concatenates complete pre-hash WDEV trials. Jitter, transcript hashes,
seed commitments, and packet metadata are stored in separate companion files.

## Contributors

- Ideas, validation and experiments: [fj-blanco](https://github.com/fj-blanco)
- Most of the code and documentation: GPT 5.5 Extra High with Codex.

## License

MIT. See `LICENSE`.
