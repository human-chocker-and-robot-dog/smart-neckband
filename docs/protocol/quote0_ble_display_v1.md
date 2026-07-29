# Quote/0 BLE Display Protocol v1 compatibility draft

Status: PC implementation draft pending adoption by the Quote/0 firmware repository. The eventual Quote/0 `docs/PROTOCOL_BLE_DISPLAY_V1.md` is the normative source; this copy must record its source commit when firmware adopts the contract.

## Transport and security

- Device role: BLE Peripheral.
- Windows upper computer role: BLE Central.
- Advertising name: `InkCanvas-Quote0-XXXX`.
- All multi-byte integers are little-endian.
- All frame/control writes require an encrypted link. Bonding is expected.
- The display accepts one receive transaction at a time and keeps at most one latest pending committed frame while the panel refreshes.

## GATT UUIDs

| Object | UUID | Properties |
| --- | --- | --- |
| Display service | `7f510001-1b15-4a7b-9e9f-6b64d5f7c100` | primary service |
| Device info | `7f510002-1b15-4a7b-9e9f-6b64d5f7c100` | read |
| Control | `7f510003-1b15-4a7b-9e9f-6b64d5f7c100` | write with response |
| Frame data | `7f510004-1b15-4a7b-9e9f-6b64d5f7c100` | write with response |
| Status | `7f510005-1b15-4a7b-9e9f-6b64d5f7c100` | read, notify |

## Frame

- Width: 296.
- Height: 152.
- Stride: 37 bytes.
- Length: 5,624 bytes.
- Pixel location: `frame[y * 37 + floor(x / 8)]`.
- Pixel mask: `0x80 >> (x % 8)`.
- `1 = white`, `0 = black`.
- CRC: CRC-32/ISO-HDLC, the value returned by standard zlib/IEEE CRC32.

## Control characteristic

### `BEGIN_FRAME` (`command=1`)

Packed shape: `<BBIHIHBBQQ>`.

| Field | Type |
| --- | --- |
| command | u8 |
| protocol version | u8 |
| frame ID | u32, nonzero |
| frame length | u16, exactly 5624 |
| expected CRC32 | u32 |
| rotation | u16, 90 or 270 |
| refresh request | u8: 0 auto, 1 force full |
| flags | u8, currently zero |
| source ECG sample ordinal | u64 |
| source device monotonic timestamp us | u64 |

### Frame-ID controls

`COMMIT_FRAME` (`command=2`) and `CANCEL_FRAME` (`command=3`) use `<BI>`: command plus frame ID.

### Simple controls

`GET_STATUS` (`command=4`) and `FORCE_FULL_NEXT` (`command=5`) use `<B>`.

## Frame data characteristic

Each write is `<IHH>` followed by payload bytes:

| Field | Type |
| --- | --- |
| frame ID | u32 |
| offset | u16 |
| payload length | u16 |
| payload | bytes |

The first offset is zero. Later chunks must use the next sequential offset. The PC default is 180 payload bytes per write and may reduce it to the device-reported maximum.

## Device info characteristic

Packed shape: `<BBHHHH16s>`: protocol version, capability bits, width, height, frame bytes, maximum chunk payload bytes, and a zero-padded ASCII firmware version.

Capabilities: partial refresh `1<<0`, force full `1<<1`, battery status `1<<2`, encrypted writes `1<<3`, latest-pending queue `1<<4`.

## Status characteristic

Packed shape: `<BBIHHBBHHHHIHHBBHHHH>`.

It reports protocol version, state, frame ID, received/expected bytes, actual refresh mode, error code, changed region, refresh time, partial count, battery, link flags and cumulative replacement/CRC/timeout/display-failure counters.

States: ready 0, receiving 1, queued 2, refreshing 3, done 4, error 5.

Refresh modes: none 0, full 1, partial 2.

The device must notify `DONE` or `ERROR` for every accepted committed frame. Panel refresh is never performed in the GATT callback.

## Golden vectors

Machine-readable vectors live in `docs/protocol/quote0_ble_display_v1_golden_vectors.json` and are verified by `pc_app/tests/test_epaper_protocol.py`. The Quote/0 C implementation must consume equivalent vectors before hardware integration is considered complete.
