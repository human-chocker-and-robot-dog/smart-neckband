# Readable ECG scale and explicit HRV availability

## Goal
Make the now-visible live ECG readable on a phone and explain exactly why HRV is unavailable when heart rate is shown.

## Current state
0.1.2 is installed. User confirms live data now appears. Direct screenshots show complete raw and cleaned 5000-point traces compressed into approximately 330 dp over 10 seconds, and HRV at 0/60 s. The visible analysis message indicates timing/clipping flags. An earlier Today screenshot also shows motion score 17.9 (above the existing <10 rest gate). No saved phone capture exists, so the precise device flag is not yet known.

## Scope
Phone presentation and explanatory analysis metadata only. Preserve raw samples, full analysis window, filtering and HRV quality gates. No firmware, medical thresholds or provider changes. No automated tests or automated phone navigation per user instruction.

## Design decisions
- Shared 3/5/10-second display selector defaults to 3 seconds. Crop presentation by device timestamps, retaining every visible sample and explicit gaps. Analysis still uses 5000 samples.
- Label negative time relative to newest sample, add ADC tick labels, and provide automatic vs fixed vertical ranges. Fixed raw range is 0–4095; fixed cleaned range is -2048–2048 ADC counts. Never imply mV calibration.
- Export HRV status and specific reasons for flags, motion, quality, data age, startup and RR-window accumulation. Show them on Today, Detail and notification. Do not fabricate RMSSD or weaken the gates to force a number.
- Use a separate composition identity per page/detail view so tab changes cannot reuse another page's lazy-list item state.

## Work breakdown
1. Inspect screenshots and existing HRV gates.
2. Add readable waveform time/vertical scales.
3. Carry explicit HRV status from Python through repository/UI.
4. Build only, install update, document manual acceptance, commit and push.

## Validation
Source review and `:app:assembleDebug` only. No test tasks or instrumentation. Verify installed package version read-only. User owns live display and HRV acceptance.

## Risks and rollback
Shorter display is not data loss; retained analysis/export stays complete. HRV can remain unavailable until actual quality and rest conditions are satisfied. Automatic vertical scaling must use extrema, never clip or normalize raw values. Revert this application change without changing the collar firmware.

## Progress
- [x] Capture and inspect actual phone screenshots.
- [x] Implement scale and HRV explanations.
- [x] Build 0.1.3 with `:app:assembleDebug` only; successful in 15 seconds.
- [x] Cover-install on Xiaomi 13 Ultra and verify versionName 0.1.3 / versionCode 4, preserving data.
- [x] Document and review source changes; no captured data included.
- [ ] User manually verifies readability and reads the live HRV blocker list.

## Discoveries
Firmware packet flags OR the 20 sample flags. A single packet warning consequently affects every sample represented by that packet; warning counts are flagged sample slots, not a precise count of missed ADC conversions. Current HRV intentionally rejects any warning in the analysis window and resets accumulation.

## Result
Waveform presentation now defaults to 3 seconds with 5/10-second options, shared raw/cleaned controls, labelled ADC ticks and automatic/fixed amplitude ranges. The complete 5000-point acquisition/analysis data is preserved. Fixed-scale overflow is explicitly labelled. Page/detail compositions have separate identities.

HRV exposes per-flag, motion, quality, freshness and accumulation explanations through Python, repository, Today, Detail, notification and diagnostic export. Quality gates and scientific algorithms were not relaxed or replaced. The phone screenshot already confirmed a sampling/clipping warning, but the precise device flag is still not known from saved logs; the new live reason list will distinguish it.

Build and install succeeded. No automated tests, test APK installation, automatic navigation or body-connected acquisition were performed for this change. Existing test text expectations were maintained in source only. Manual phone acceptance remains with the user as requested.
