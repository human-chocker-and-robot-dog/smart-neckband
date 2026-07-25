# MIC1 Audio Protocol

MIC1 frames share the encrypted BLE UART TX characteristic with V0 packets.
All integer fields are little-endian and every frame ends with
CRC-16/CCITT-FALSE over the header and payload.

## Header

| Field | Type | Notes |
|---|---|---|
| magic | `char[4]` | `MIC1` |
| version | `uint8` | `1` |
| frame_type | `uint8` | audio `1`, status `2`, wake `3` |
| encoding | `uint8` | unified firmware uses IMA-ADPCM `3` |
| flags | `uint16` | clipped, I2S error, TX error |
| sequence | `uint32` | generated audio sequence or event counter |
| sample_rate | `uint32` | `16000` |
| first_sample_index | `uint64` | monotonic microphone sample index |
| sample_count | `uint16` | audio uses `400`; status/wake use `0` |
| payload_length | `uint16` | bytes after the 29-byte header |

An ADPCM audio payload starts with predictor `int16`, step index `uint8`, one
reserved byte, then packed low-nibble-first IMA-ADPCM codes. A 400-sample frame
is 235 bytes including header and CRC.

Status payload is `<IIIIHBB>`: I2S errors, TX errors, clipped frames, BLE
connection interval in 1.25 ms units, PCM shift, encoding, and capture state
(`0` stopped, `1` streaming, `2` armed).

Wake payload is `<IHH>`: wake count, WakeNet word index, reserved.

The shared vector [mic1_golden_vectors.json](mic1_golden_vectors.json) is checked
by firmware startup self-test and the Python parser test suite.
