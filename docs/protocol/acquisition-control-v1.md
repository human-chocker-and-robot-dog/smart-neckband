# Acquisition control extension (V0 types 7 and 8)

This optional extension retains the V0 header (SN, version 1, little endian) and
CRC-16/CCITT-FALSE. It does not change ECG, IMU, status or voice packets. BLE RX
writes may be split at the ATT boundary; a partial command expires after 1 second.
No request changes sensor pins, rates, filtering or raw ADC representation.

| Packet | Type | Payload |
| --- | --- | --- |
| Command | 7 | uint32 request ID; uint8 operation; 3 reserved zero bytes |
| ACK | 8 | uint32 request ID; uint8 active; uint8 result; uint16 reserved zero |

Both frames are 28 bytes. Commands use sequence/timestamp zero. Operations are
STOP=0, START=1, QUERY=2. ACKs use the device's global packet sequence and monotonic
clock. Active is 0 or 1. Result 0 means applied, result 1 means sensor operation
failed; active always reports the actual state. Malformed/CRC-invalid commands
produce no ACK. A full control queue may drop a request, never return false success.

START/STOP are idempotent desired-state commands. The Android client registers a
request ID before writing, serializes GATT writes, waits up to 5 seconds for the
matching ACK, and compares result and active state. GATT write completion alone
does not confirm collection. No ACK means unsupported firmware or lost control;
the UI must not claim the hardware stopped.

The packet task applies commands. STOP waits for in-flight ECG/IMU reads, stops
the ECG GPTimer and gates subsequent IMU reads. Pending sensor ring samples are
cleared as an explicit session boundary; counters and sample indices persist.
Already-enqueued BLE telemetry drains before the ACK. Status packets and BLE stay
alive while stopped. START resumes at the same rates with new timestamps. Android
starts a fresh analysis/RR window; no RR can cross STOP/reconnect/gap boundaries.

Boot still starts sampling for compatibility with the existing PC app. This is
not a power-off command: AD8232 SDN remains tied to 3.3 V, MPU6050 is not placed in
hardware sleep, microphone behavior is unchanged. Phone force-stop/radio loss
does not send STOP. A regular Settings/notification Stop requests hardware STOP.

Golden vectors: `acquisition_control_golden_vectors.json`; C boot self-test and
Python/Kotlin tests share those bytes. First target: ESP32-C3 SuperMini + Xiaomi
13 Ultra, Android 16. Flashing and physical tests are separate acceptance steps.
