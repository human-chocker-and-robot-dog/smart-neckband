# ADC clipping interpretation and early HRV display

## Goal
Correct packet-level clipping interpretation and display a calculated RMSSD
reference as soon as usable recent RR intervals exist, without inventing values.

## Current state
Firmware ORs sample flags into each packet. Android expands packet flags onto
every sample. A single rail hit therefore labels all samples in that packet.
The adapter resets strict RR accumulation on any flag and refuses any HRV before
a clean 60-second quiet window, even while the shared PC detector reports HR.

## Scope
Android adapter, UI, notification and diagnostics. No firmware flash, hardware
gain change, raw waveform repair or PC algorithm change. User prohibits automatic
tests/navigation; only review, compilation and an authorized APK installation.

## Design decisions
- Preserve every raw value and original wire flag in both rings/diagnostics.
- On a private analysis copy, map only ADC_CLIPPING to actual 12-bit rail hits
  (raw <= 0 or raw >= 4095), leaving other flags intact. The shared cleaner and
  its 80% sustained-clipping threshold remain unchanged.
- Expose actual clipped sample count separately from packet-flag sample slots.
- Keep strict 60-second RMSSD and its eligibility semantics. Compute a separate
  recent-window reference from at least three adjacent valid settled RR intervals
  using device timestamps, only after the existing HR/SQI gate passes.
- A reference is explicitly approximate and carries duration/RR count/warnings;
  it is not sent as qualified RMSSD to AI. Lead-off, true sample loss/overflow,
  stale data, persistent saturation, poor SQI or insufficient RR show no number.

## Work breakdown
1. Review firmware packing, desktop clipping guard and Android RR gates.
2. Implement per-sample clipping evidence and a separate reference calculation.
3. Display it consistently in Today, Detail and notification; update documentation.
4. Compile, install without launching/navigating, commit and push.

## Validation
Source review and `:app:assembleDebug` only. `adb install -r` plus read-only
package-version confirmation. Live trace quality and numerical accuracy remain
for the user's manual test; no automated tests or replay scripts are run.

## Risks and rollback
Clipping and timing jitter still affect R-peak placement and reference RMSSD.
Showing a reference does not repair the analog signal. Strict values remain
separate in JSON and the repository. Reinstall the previous APK if needed.

## Progress
- [x] Identify packet flag amplification and unconditional RR reset.
- [x] Implement, review and document.
- [x] Build/install and prepare reviewed follow-up commit.

## Discoveries
`packet_task.c` aggregates flags with OR; `sensors.c` marks exact ADC rail hits.
The existing strict RR uses nominal 2 ms indices; the reference will instead use
the available device timestamps, while acknowledging packet-internal timing limits.

## Result
Android 0.1.5/code 6 separates packet-flag slots from actual ADC rail hits and
provides a labelled recent-window RMSSD reference alongside the original strict
metric. Today, Detail and the notification use the same display selection; AI
only receives qualified RMSSD. Original ADC values and wire flags remain intact.
Reference calculation never joins RR segments across a rejected interval.

The AI integration review also made automatic-request failure pauses persistent
across restarts and added actual clipping counts to the quality summary.

Final `:app:assembleDebug` succeeded in 9 seconds. `adb -s 40c87980 install -r`
returned Success; read-only package output confirms versionCode=6/versionName=0.1.5.
No automatic tests, synthetic replay, phone navigation, firmware flashing or
actual AI calls were performed. Numerical accuracy with the user's live signal,
the reference's real-world availability and provider credentials remain for
manual acceptance. Real analog rail hits cannot be reconstructed in software.
