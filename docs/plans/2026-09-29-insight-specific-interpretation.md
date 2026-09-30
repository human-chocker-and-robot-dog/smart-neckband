# Specific data interpretations

## Goal
Replace the poetic, repetitive output shown in the user's screenshot with concise
observations that relate HR, RMSSD and movement and justify a relevant action.

## Current state
0.1.6 explicitly asks for gentle companionship, and its examples/local fallbacks
repeat breathing and shoulder suggestions. The model does not receive the actual
definition of the IMU score or still ratio. It misreads 100% still as no movement.

## Scope
Prompt, input metric definitions, response quality checks, local/demo content and
small UI copy changes. No acquisition, firmware or HRV algorithm change.

## Design decisions
- Keep reference RMSSD in the input with its existing approximate label.
- Ground movement semantics in health_motion.py: trailing up-to-30-second window,
  one-second buckets; still means bucket score <=10, not absence of all motion.
- Require observation/meaning/action themes with cited facts, cross-metric reading
  where possible, no rest recommendation solely because movement is already low.
- Replace examples and fallback templates too; no manufactured normal/high/low
  HRV classification, pressure diagnosis or baseline/trend claims.
- Version the content contract to 3 so existing vague cards remain in history,
  rather than appearing as the default result of the updated prompt.

## Work breakdown
1. Inspect screenshot, prompt, fallbacks and actual movement semantics.
2. Revise prompt/context/validation and user-facing content coherently.
3. Review, assemble, install, document and commit/push.

## Validation
User forbids automated tests/navigation. Only source review, assembleDebug,
authorized adb install -r and read-only version confirmation. User tests the
provider manually; no API request or screenshot of a sensitive settings screen.

## Risks and rollback
Generative prose still needs manual acceptance. Keep explicit metric references,
reference labels, bounded responses and honest provider/local provenance. Preserve
API settings and original records. Reinstall prior APK if necessary.

## Progress
- [x] Identify concrete prompt and metric-definition errors.
- [x] Implement/review.
- [x] Build; install is pending because ADB currently reports no device.
- [x] Document/commit/push.

## Discoveries
100% still ratio means all covered one-second buckets have score <=10; it cannot
establish zero movement, posture, prolonged sitting or relaxation.

## Result
Schema v3 now sends metric definitions and movement-window semantics, requires three
specific observation/meaning/action cards, rejects the old poetic and diagnostic
fallback language, and maps the same rules into local/demo cards. Debug assembly
succeeded for version 0.1.7 (code 8). Commit `c55bea3` is pushed to
`feat/android-companion-mvp`; the authorized phone install is pending reconnection.
