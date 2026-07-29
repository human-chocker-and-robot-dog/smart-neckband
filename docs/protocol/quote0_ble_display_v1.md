# Quote/0 BLE Display Protocol v1 compatibility copy

Status: implemented by the PC application. The normative source is
`InkCanvas-Rewrite/docs/PROTOCOL_BLE_DISPLAY_V1.md` at firmware commit
`cbb351deb634c6463e0e85cd271916f51f87e349`. If this copy differs from that
source or its golden vectors, the firmware repository wins.

The verbatim machine-readable vectors copied from the firmware repository are
stored in `docs/protocol/quote0_ble_display_v1_golden_vectors.json` and are
verified byte-for-byte by `pc_app/tests/test_epaper_protocol.py`.

## Transport and security

- Quote/0 is the BLE Peripheral; the Windows application is the BLE Central.
- Advertising name: `InkCanvas-Quote0-XXXX`.
- All multi-byte integers use little-endian byte order.
- `CONTROL` and `FRAME_DATA` writes require an encrypted link and use write
  with response. Bonding is supported.
- `STATUS` is obtained through characteristic reads and notifications. There
  is no `GET_STATUS` control opcode.
- The device accepts one receive transaction and may retain one latest pending
  committed frame while the panel is refreshing.

## GATT UUIDs

| Object | UUID | Properties |
| --- | --- | --- |
| Display service | `7b7d1000-4a30-4b15-9f41-5d2e6a8f6c20` | primary service |
| DEVICE_INFO | `7b7d1001-4a30-4b15-9f41-5d2e6a8f6c20` | read |
| CONTROL | `7b7d1002-4a30-4b15-9f41-5d2e6a8f6c20` | encrypted write with response |
| FRAME_DATA | `7b7d1003-4a30-4b15-9f41-5d2e6a8f6c20` | encrypted write with response |
| STATUS | `7b7d1004-4a30-4b15-9f41-5d2e6a8f6c20` | read, notify |

## Framebuffer and CRC

- Width: 296 pixels.
- Height: 152 pixels.
- Stride: 37 bytes.
- Length: 5,624 bytes.
- Pixel location: `frame[y * 37 + floor(x / 8)]`.
- Pixel mask: `0x80 >> (x % 8)`.
- `1 = white`, `0 = black`.
- CRC: CRC-32/ISO-HDLC, equivalent to the unsigned result of `zlib.crc32`.

## CONTROL messages

Only these opcodes exist:

| Opcode | Value |
| --- | ---: |
| `BEGIN_FRAME` | 1 |
| `COMMIT_FRAME` | 2 |
| `CANCEL_FRAME` | 3 |

`GET_STATUS` and `FORCE_FULL_NEXT` are not part of the firmware protocol.
A force-full request is carried by `BEGIN_FRAME.refresh_request = 1`.

### BEGIN_FRAME

Packed shape: `<BBBBIHHIQQ>`, exactly 32 bytes.

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | u8 | protocol version, 1 |
| 1 | u8 | opcode, `BEGIN_FRAME` |
| 2 | u8 | refresh request: 0 auto, 1 force full |
| 3 | u8 | reserved, zero |
| 4 | u32 | nonzero frame ID |
| 8 | u16 | frame length, exactly 5,624 |
| 10 | u16 | rotation, 90 or 270 |
| 12 | u32 | expected framebuffer CRC32 |
| 16 | u64 | source ECG sample index |
| 24 | u64 | source device monotonic timestamp in microseconds |

### COMMIT_FRAME and CANCEL_FRAME

Packed shape: `<BBHI>`, exactly 8 bytes: protocol version, opcode, zero
reserved field, and the nonzero frame ID.

## FRAME_DATA

Each write is a 12-byte `<BBHIHH>` header followed by the payload:

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | u8 | protocol version, 1 |
| 1 | u8 | flags, zero |
| 2 | u16 | payload length, 1 through 180 |
| 4 | u32 | frame ID |
| 8 | u16 | sequential framebuffer offset |
| 10 | u16 | reserved, zero |
| 12 | bytes | payload |

The first offset is zero. Each subsequent offset must equal the current
received byte count. The first version caps payloads at 180 bytes so a packet
fits the preferred ATT MTU of 200.

## DEVICE_INFO

Packed shape: `<BBBBBBHHHHHHHHHII>`, exactly 32 bytes.

Fields in order: protocol version, structure version, firmware major/minor/
patch, reserved byte, capability flags, width, height, frame bytes, maximum
chunk payload, status size, begin-frame size, preferred ATT MTU, reserved
field, device ID tail, and build ID.

Capability flags:

| Bit | Meaning |
| ---: | --- |
| 0 | encrypted writes required |
| 1 | bonding supported |
| 2 | status notify supported |
| 3 | partial refresh supported |
| 4 | USB transport supported |
| 5 | latest-pending queue supported |

## STATUS

Packed shape: `<BBBBIIHHHHHHIHBBIHHIIIIII>`, exactly 64 bytes.

Fields in order:

1. protocol version, state, last error, actual refresh mode;
2. active frame ID and last frame ID;
3. received bytes and flags;
4. changed x, y, width and height;
5. refresh milliseconds and partial-refresh count;
6. battery percent (`255` unknown) and owner (`0` none, `1` USB, `2` BLE);
7. milliseconds since last full refresh, battery mV and reserved field;
8. pending-replaced, CRC-error, timeout and display-failure counters;
9. free heap bytes and status sequence.

`active_frame_id` identifies the transaction currently being received or
processed. `last_frame_id` identifies the most recently completed or failed
frame and is the value used to resolve a committed PC transaction.

STATUS flags:

| Bit | Meaning |
| ---: | --- |
| 0 | link encrypted |
| 1 | bonded |
| 2 | connected |
| 3 | pending frame present |
| 4 | legacy mode |
| 5 | sync mode |
| 6 | low-battery advisory |

States: `READY=0`, `RECEIVING=1`, `QUEUED=2`, `REFRESHING=3`, `DONE=4`,
`ERROR=5`.

Actual refresh modes: `NONE=0`, `FULL=1`, `PARTIAL=2`.

## Error codes

| Name | Value |
| --- | ---: |
| `NONE` | 0 |
| `BAD_VERSION` | 1 |
| `BAD_OPCODE` | 2 |
| `NOT_ENCRYPTED` | 3 |
| `BUSY` | 4 |
| `INVALID_LENGTH` | 5 |
| `INVALID_ROTATION` | 6 |
| `INVALID_REFRESH_REQUEST` | 7 |
| `FRAME_ID_MISMATCH` | 8 |
| `OFFSET_MISMATCH` | 9 |
| `OVERFLOW` | 10 |
| `CRC_MISMATCH` | 11 |
| `TIMEOUT` | 12 |
| `NO_MEMORY` | 13 |
| `QUEUE_FAILURE` | 14 |
| `DISPLAY_FAILURE` | 15 |
| `INVALID_STATE` | 16 |
| `MALFORMED_MESSAGE` | 17 |
| `INTERNAL` | 255 |

## Refresh timing

Firmware sync mode initially uses a five-minute or 20-partial-refresh
maintenance threshold, whichever arrives first. The PC default completion
timeout is 45 seconds because firmware sync throttling may wait about 15
seconds before the panel's approximately 4.1-second refresh begins.
