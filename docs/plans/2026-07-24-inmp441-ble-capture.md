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
- `START ADPCM`
- `ARM PCM16`
- `ARM PCM8`
- `ARM ADPCM`
- `STOP`
- `SHIFT <10..20>`
- `INFO`

The device sends self-synchronizing `MIC1` binary frames. Every frame includes
version, type, encoding, flags, sequence, sample rate, first sample index,
sample count, payload length, and CRC16-CCITT-FALSE. PCM8 transport is expanded
back to PCM16 by the PC before plotting and WAV recording. IMA-ADPCM is the
recommended real-time mode: it preserves the 16 kHz sample rate while reducing
the audio payload to about 8 KB/s. Each independently decodable 25 ms block,
header, and CRC fit in one notification at a 247-byte ATT MTU. PCM8 and PCM16
are retained as explicit bandwidth stress modes and are not expected to be
lossless on every Windows BLE adapter.

Frame type 3 reports an official `Hi ESP` WakeNet event with the cumulative
wake count, WakeNet word index, and monotonic detection sample index. `ARM`
keeps audio local to WakeNet until this event, then starts the selected stream.
The status payload's final state byte is 0 for stopped, 1 for streaming, and 2
for armed. Older firmware reports only 0/1, so the PC can detect an unsupported
`ARM` command instead of leaving the user waiting without an explanation.

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
  sequence gaps, PCM16/PCM8/IMA-ADPCM decoding, and WAV output.
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
- [x] Safe hardware flash completed.
- [x] Live audio evidence captured.

## Software validation result

On 2026-07-24, ESP-IDF v6.0.2 built the standalone `esp32c3` firmware
successfully. The final image was `0x80210` bytes, leaving 50% of the 1 MiB
factory application partition free. The size report showed 91,879 bytes of
DRAM use (28.60%).

The complete PC suite passed 76 tests. The six experiment-specific tests
passed again after the final implementation change, the GUI and its BLE/Qt
dependencies imported successfully in the existing project environment, and
`git diff --check` passed.

## Hardware flash result

On 2026-07-24, after the user explicitly confirmed that no body electrodes
were connected, the standalone image was written to the ESP32-C3 on COM21.
The final flash run explicitly hash-verified the bootloader, partition table,
and 524,816-byte application image, then hard-reset the board. A BLE scan found
the firmware-specific advertisement `CollarMic-2E4A` at
`44:B1:76:1A:2E:4A`.

The first PCM8 hardware run demonstrated that the Windows adapter could not
sustain a 16 KB/s raw stream: notifications starved control writes and caused
sequence gaps. The final design uses 400-sample independently decodable
IMA-ADPCM blocks, an I2S producer/BLE consumer queue, disconnect auto-stop, and
a requested 7.5–15 ms connection interval.

The final three-second hardware run received 121 frames and 48,400 samples,
corresponding to 3.025 seconds at 16 kHz. Sequence gaps, CRC errors, malformed
frames, discarded bytes, I2S errors, BLE TX errors, and clipped frames were
all zero. The negotiated connection interval was 15 ms. The decoded signal
had RMS 1,362 and peak 8,887 at PCM shift 16. The saved smoke-test file was
verified as mono, 16-bit, 16 kHz WAV with 48,400 frames.

One ESP-IDF integration bug was found during the hardware test:
`i2s_channel_read()` takes milliseconds, but the initial implementation passed
`pdMS_TO_TICKS(200)`. With `CONFIG_FREERTOS_HZ=100`, this became a 20 ms
timeout, shorter than the 25 ms needed for a 400-sample block. Passing `200U`
directly removed all I2S timeouts and restored the complete sample timeline.

The first microphone GUI scan also reproduced the Windows Qt/WinRT failure
previously fixed in the main ECG application: running `asyncio.run()` and
Bleak directly on Qt's GUI/STA thread prevented Windows callbacks from being
pumped. The microphone scanner now runs on a dedicated Python thread and
returns results to the Qt thread through a signal. The complete PC suite still
passed 76 tests, and a PySide6 application-thread smoke test discovered
`CollarMic-2E4A` without the callback error.

## Official Hi ESP wake-word extension

The custom/template wake-word experiment is superseded by Espressif's
official `Hi ESP` model. The ESP32-C3 test board uses the no-PSRAM
`WakeNet9s` model `wn9s_hiesp` through the direct WakeNet interface; this is
the single-microphone, lower-memory path recommended by the official
ESP-Skainet example.

The intended test flow is:

1. The INMP441 continuously supplies mono 16 kHz signed PCM to WakeNet9s.
2. The PC opens a WAV recorder and sends an `ARM` command over BLE.
3. Saying `Hi ESP` emits a wake event and starts the existing ADPCM stream.
4. The PC records post-wake audio and displays wake count and link integrity.
5. `STOP` ends the stream and disarms the wake action.

The wake model is stored in a dedicated `model` data partition. The temporary
4 MB C3 partition layout keeps NVS and PHY data, allocates 1.5 MB to the
factory application, and 640 KiB to the model. Flashing this new image remains
blocked on a fresh explicit user instruction.

### Hi ESP extension progress

- [x] Official C3 model and direct WakeNet example verified.
- [x] Component dependency, model selection, and partition layout added.
- [x] Continuous WakeNet9s inference integrated with the I2S producer.
- [x] BLE wake event and armed-recording protocol implemented.
- [x] PC parser, tests, and GUI updated.
- [x] GUI distinguishes the intentionally silent armed state from unsupported
  old firmware and provides a manual waveform self-test.
- [x] Firmware build and size validation completed.
- [ ] Hardware flash and spoken `Hi ESP` test explicitly authorized.

The software-only Hi ESP build completed on ESP-IDF v6.0.2 with
`esp-sr` 2.4.6. The selected `wn9s_hiesp` model is 122.83 KiB. The application
binary is `0x98840` bytes, leaving 60% of the temporary 1.5 MiB factory
partition free; DRAM use is 98,559 bytes (30.68%). The PC suite passed 77
tests, including the new wake-event frame.
