# Health Dashboard Live Protocol V1

The Dashboard extends the existing authenticated WebSocket session without
changing `ecg_batch`, `status`, viewer authentication, ECG sequencing, or the
legacy `/live` viewer.

## Telemetry

The PC relay sends `telemetry` at no more than 5 Hz. Heart and motion metrics
may update more slowly than orientation and are repeated as the latest bounded
snapshot.

```json
{
  "type": "telemetry",
  "timestamp_ms": 1785031200000,
  "heart": {"bpm": 76, "sqi": 0.93, "lead_off": false},
  "imu": {
    "online": true,
    "roll_deg": 2.4,
    "pitch_deg": -7.1,
    "yaw_deg": 14.2,
    "motion_score": 28.5,
    "still_ratio_percent": 63.3,
    "level": "light"
  },
  "uwb": {"distance_m": null, "quality": null, "source": "unavailable"},
  "device": {
    "transport": "ble",
    "ecg_sample_rate_hz": 500,
    "imu_sample_rate_hz": 50,
    "packet_loss": 0,
    "crc_errors": 0,
    "data_age_ms": 84
  }
}
```

The server adds `session_id`, stores only the latest telemetry under the
session TTL, publishes it to viewers, and acknowledges with
`message_type=telemetry`. Null means unavailable; it must not be rendered as
zero. Yaw is gyro-integrated and may drift.

## Health event

```json
{
  "type": "health_event",
  "event_id": "50d40557-8df6-47b5-abce-1ef447bf5543",
  "event_revision": 2,
  "event_type": "signal.lead_off",
  "transition": "resolved",
  "severity": "warning",
  "priority": "normal",
  "timestamp_ms": 1785031200120,
  "summary": "ECG lead contact restored."
}
```

The server accepts only a newer revision for an existing event ID, keeps at
most 20 recent events, and acknowledges with `message_type=health_event`.
Events are engineering notifications, not diagnoses or emergency claims.

## Viewer snapshot

The existing `snapshot` message adds:

```json
{
  "telemetry": null,
  "recent_events": []
}
```

Both fields are backward-compatible defaults. Existing clients may ignore
them. Dashboard clients then continue with live `ecg_batch`, `status`,
`telemetry`, and `health_event` messages.

## Data and safety boundaries

- ECG shown publicly is the PC-cleaned visualization branch; raw recordings remain local.
- No raw IMU arrays, voice transcripts, MCP traces, secrets, or long-term public history are transmitted.
- UWB uses `live`, `demo`, or `unavailable` provenance. Browser demo data is always visibly marked.
- The protocol does not authorize robot actions and does not infer dog state.
