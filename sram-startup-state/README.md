# SRAM startup state experiment

This application tests whether a 4096 byte `.noinit` SRAM region depends on
its contents before reset or power removal. It does not assign entropy to the
captured bytes.

The firmware copies the measured region at Zephyr's `EARLY` initialization
level. It exports that copy after startup. The host then writes and verifies
one of four patterns in the measured region before the next transition. The
patterns are `00`, `ff`, `aa`, and `55` and are interleaved on every run.

## Automatic RTS experiment

Run the full workflow with one command. A stable serial path is recommended.

```sh
./scripts/run_experiment.py \
  --serial /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_DEVICE-if00-port0 \
  --board esp32s3_devkitc/esp32s3/procpu \
  --conditions rts \
  --repetitions 10
```

The command builds, flashes, collects 40 captures, and writes the analysis to
a new directory under `results/`. It requires `west`, the Zephyr SDK, and
PySerial. The script also recognizes the repository virtual environment and a
Zephyr SDK installed under the adjacent `code` directory.

For the primary ESP32 board, use
`--board esp32_devkitc_wroom/esp32/procpu` and its serial device. The board
configuration enables the explicit Zephyr opt-in required by its revision 1.0
silicon.

## Controlled power removal

True power tests require hardware that can disconnect every supply path to the
board. The host script supports a USB hub or relay controller through two
commands. It does not treat an RTS pulse as power removal.

```sh
./scripts/run_experiment.py \
  --serial /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_DEVICE-if00-port0 \
  --conditions rts,power-1,power-10,power-30 \
  --repetitions 10 \
  --power-off-command "controller-command off" \
  --power-on-command "controller-command on"
```

The power commands are executed directly without a shell. Replace the example
commands with the command for the measured USB port or relay. Confirm that
UART, debugger, and GPIO wiring do not provide another power path.

## Results

Each result directory contains the following files.

* `manifest.json` records the board, schedule, capture hook, and zero entropy
  credit.
* `raw/` contains every 4096 byte capture.
* `captures.csv` records the preceding pattern and per-capture measurements.
* `summary.csv` and `summary.json` report agreement with the preceding pattern,
  within-group repeatability, and unique hashes.
* `linker_evidence.txt` records the measured symbol from the linker map.
* `serial.log` preserves the complete device output.

High agreement with the preceding pattern indicates retention. Low dependence
on the preceding pattern after controlled power removal is necessary before a
startup fingerprint interpretation can be considered. Neither outcome alone
provides a min-entropy estimate.
