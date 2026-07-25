# Health Event Catalog V0.3

This catalog is owned by `smart-neckband`. Event types are open namespaced
strings; the RDK Gateway must not implement a closed event-type enum.

All events use the envelope in
`docs/specs/health-event-bridge-v0.3.contract.json`. Webhooks contain the latest
trigger snapshot only. When `recommended_capabilities` contains
`health.inspect_recent_metrics`, the Agent policy queries the three Health MCP
tools with the `evidence.recommended_window_s` value, normally 30 seconds.

These events are engineering notifications, not medical diagnoses or emergency
classifications.

## `signal.lead_off`

- Meaning: the latest fresh device status reports ECG lead-off.
- Lifecycle: `opened` when lead-off is present; `resolved` after three distinct
  fresh clear status packets.
- Typical severity/priority: `warning` / `normal`.
- Evidence: `lead_off`, clipping ratio, packet ages, quality level.
- Recommended capabilities: none by default.

## `signal.adc_clipping`

- Meaning: at least 80 percent of a complete ten-second ECG window carries the
  ADC clipping flag.
- Lifecycle: `opened` after a complete qualifying window; `resolved` after ten
  seconds of fresh data below the clear threshold.
- Typical severity/priority: `warning` / `normal`.
- Evidence: clipping ratio, lead-off, packet ages, quality level.
- Recommended capabilities: none by default.

## `input.stale`

- Meaning: ECG input age is greater than two seconds and no more than ten
  seconds while the acquisition process remains available.
- Lifecycle: `opened` when stale; `resolved` after one second of fresh input or
  superseded by `input.offline`.
- Typical severity/priority: `warning` / `normal`.
- Evidence: ECG and transport packet ages and quality level.
- Recommended capabilities: none by default.

## `input.offline`

- Meaning: ECG input is older than ten seconds or the acquisition transport is
  disconnected/error.
- Lifecycle: `opened` when offline; `resolved` after one second of fresh input.
- Typical severity/priority: `critical` / `urgent`. This priority controls Agent
  queueing and is not a medical emergency level.
- Evidence: ECG and transport packet ages and quality level.
- Recommended capabilities: none by default because recent physiology may be
  unavailable.

## Configurable cardio rule events

Configured rules are loaded from `SMART_COLLAR_HEALTH_RULES_PATH`. The checked-
in `config/health_rules.example.json` contains disabled examples. Thresholds,
operators, hold duration, clear duration, cooldown, quality gate, severity, and
priority are local configuration, not MCP tools.

### `cardio.high_hr_low_motion`

- Suggested conditions: `motion_score <= configured value` and
  `heart_rate_bpm >= configured value`.
- Evidence: rule ID, current BPM, current RMSSD, 30-second motion score, signal
  quality, valid NN count, thresholds, required duration, and recommended query
  window.
- Recommended capability: `health.inspect_recent_metrics`.

### `cardio.hrv_threshold_low_motion`

- Suggested conditions: low motion plus a configured comparison against
  `hrv_rmssd_ms`.
- RMSSD direction is explicitly configured; the system must not label either a
  high or low value as medically abnormal.
- Recommended capability: `health.inspect_recent_metrics`.

### `cardio.extreme_hr_high_motion`

- Suggested conditions: high motion plus BPM above the configured extreme
  threshold, with a non-zero hold duration and acceptable ECG signal quality.
- Recommended capability: `health.inspect_recent_metrics`.
- Even when severity is `critical`, the event does not authorize robot motion
  or claim an emergency.

## Agent capability mapping

The collar sends this abstract capability:

```text
health.inspect_recent_metrics
```

The RDK Agent policy maps it to:

```text
health.get_heart_rate({"window_s": 30})
health.get_hrv({"window_s": 30})
health.get_imu_state({"window_s": 30})
```

The Gateway must not guess a robot MCP tool from an unknown Health capability.
