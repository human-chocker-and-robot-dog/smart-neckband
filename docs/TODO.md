# TODO

## Realtime Web Push

- Do not implement the web dashboard or WebSocket transport in the current V0 SPP loop.
- Keep `pc_app` serial parsing independent of publishers.
- Add a future `WebSocketPublisher` behind the existing `DataPublisher` interface so parsed ECG, IMU, and status packets can be mirrored to a local web UI without modifying the serial parser.
- Preserve the binary protocol as the source of truth; any web JSON should be derived from parsed packets on the PC side, not used as the firmware realtime protocol.
