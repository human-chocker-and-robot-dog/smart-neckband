# INMP441 BLE capture experiment

## Goal

Create a disposable ESP32-C3 firmware and a small Windows PC application that
verify the INMP441 wiring, stream microphone samples over BLE UART, display the
waveform and link counters, and save the received samples as a WAV file.

This experiment is intentionally separate from the production sensor firmware.
It contains no ECG, IMU, OLED, wake-word, Wi-Fi, ASR, or Agent functionality.

## Hardware and safety

- Board: ESP32-C3 SuperMini.
- INMP441 VDD: 3.3 V only.
- INMP441 GND: GND.
- INMP441 SCK/BCLK: GPIO4.
- INMP441 WS/LRCL: GPIO5.
- INMP441 SD/DOUT: GPIO20.
- INMP441 L/R: GND, selecting the left channel.
- Sample format at the I2S peripheral: 16 kHz, mono, 32-bit Philips slot.
- The test firmware converts the left slot to signed PCM16 with a configurable
  right shift, initially 14 bits.

Before wiring or changing wiring, remove USB and every other power source.
Flashing and USB bench testing require that no body electrodes are connected.

## Design

The standalone firmware lives under
`experiments/inmp441_ble_capture/firmware`. It reuses only the repository's
generic ESP-IDF BLE UART component and advertises as `CollarMic-XXXX`. The
service and characteristic UUIDs match the existing PC application:

- Service: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
- PC-to-device RX: `6e400002-b5a3-f393-e0a9-e50e24dcca9e`
- Device-to-PC TX: `6e400003-b5a3-f393-e0a9-e50e24dcca9e`

The PC sends newline-terminated ASCII controls:

- `START PCM16`
- `START PCM8`
- `STOP`
- `SHIFT <10..20>`
- `INFO`

The device sends self-synchronizing `MIC1` binary frames. Every frame includes
version, type, encoding, flags, sequence, sample rate, first sample index,
sample count, payload length, and CRC16-CCITT-FALSE. PCM8 transport is expanded
back to PCM16 by the PC before plotting and WAV recording. PCM16 is the
quality mode; PCM8 is the lower-bandwidth wiring and stability mode.

The small PC UI is a separate entry point, `smart-neckband-mic`. It scans for
the experimental device, connects over BLE, starts/stops capture, plots a
rolling waveform, reports RMS/peak/link integrity, and writes a mono 16 kHz
16-bit WAV file.

## Validation

Software validation:

```powershell
.\tools\project.ps1 pc-test
.\tools\inmp441-test.ps1 build
.\tools\inmp441-test.ps1 size
git diff --check
```

Hardware validation, only after explicit safety confirmation:

```powershell
.\tools\inmp441-test.ps1 flash
.\tools\project.ps1 pc-mic
```

Acceptance evidence:

- Firmware build and size complete for `esp32c3`.
- PC protocol tests cover arbitrary notification fragmentation, resync, CRC,
  sequence gaps, PCM16/PCM8 decoding, and WAV output.
- Flash completes with hash verification.
- In the PC UI, quiet-room and speech samples are visibly different, CRC
  errors remain zero, and sequence gaps are measured rather than hidden.
- A WAV file opens as mono, 16 kHz, 16-bit PCM.

## Progress

- [x] Isolated branch/worktree created from the voice/BLE baseline.
- [x] Existing BLE UART and I2S patterns inspected.
- [x] Standalone firmware and protocol implemented.
- [x] PC capture UI and WAV recorder implemented.
- [x] Software validation completed.
- [ ] Safe hardware flash completed.
- [ ] Live audio evidence captured.

## Software validation result

On 2026-07-24, ESP-IDF v6.0.2 built the standalone `esp32c3` firmware
successfully. The final image was `0x7fc20` bytes, leaving 50% of the 1 MiB
factory application partition free. The size report showed 91,871 bytes of
DRAM use (28.59%).

The complete PC suite passed 75 tests. The five experiment-specific tests
passed again after the final implementation change, the GUI and its BLE/Qt
dependencies imported successfully in the existing project environment, and
`git diff --check` passed.
