# Phone-direct AI Insight

## Goal
Configure DeepSeek official or an OpenAI-compatible Chat Completions API on the
phone and turn bounded body-event JSON into validated explanation cards.

## Current state
Android 0.1.3 has local rules and independent synthetic demos. The old hidden
gateway accepts a flat response and cannot authenticate to model providers.
The user authorizes installation but explicitly prohibits automated tests and
automatic phone navigation; validation for this change is source review and build.

## Scope
Settings, encrypted personal credentials, direct HTTPS client, event/response
contract, request status, manual/optional automatic generation and documentation.
No firmware, ECG algorithms, Health Connect or medical/stress inference changes.

## Design decisions
- Share one engine between the service and UI; serialize calls and bound cadence.
- Use non-streaming Chat Completions with Bearer authentication and JSON mode.
- DeepSeek preset uses the current official `deepseek-flash` model and disables
  thinking for short explanatory cards. Model remains editable.
- Encrypt keys using Android Keystore AES/GCM, binding ciphertext to the exact
  normalized endpoint. Never log credentials, prompts or raw provider responses.
- Send summaries only. Missing/untrusted metrics stay null; manual quality cards
  can explain unavailability. Existing physiological event quality gates remain.
- Validate response types, bounds, event identity and evidence references locally.
  Render evidence from local values; preserve labelled local fallback and demos.
- Offer an explicit synthetic API check, separate from demo mode and real history.
- Default network access and automatic generation off. No automatic network retry.

## Work breakdown
1. Read official API documents and existing UI/service/data ownership.
2. Implement settings, credential storage and bounded Chat Completions client.
3. Integrate structured events, cards, settings/status and manual actions.
4. Review, assemble only, install on the authorized Xiaomi, update docs, commit/push.

## Validation
Run `:app:assembleDebug` with the local JDK/SDK/Python and mirror init script.
Install with `adb -s 40c87980 install -r` and verify package version read-only.
Do not run unit/instrumentation/UI tests or real provider requests on the user's
behalf. User will enter their key on the phone and perform manual acceptance.

## Risks and rollback
Compatible servers differ in JSON mode/token parameters; expose compatibility
options and clear errors. Provider errors must not leak bodies/credentials.
Disabling AI returns to local rules. Prior APK can be reinstalled if needed.

## Progress
- [x] Inspect source and official provider protocol documentation.
- [x] Implement and review.
- [x] Compile without automated tests (assembleDebug succeeded, 17 seconds).
- [x] Install the final APK after the user's follow-up HRV change (0.1.5/code 6).
- [x] Document result; AI implementation committed and pushed as `e2f2f74`.

## Discoveries
DeepSeek's current documentation specifies `deepseek-flash` / `deepseek-v4-pro`
and defaults thinking to enabled. JSON mode still requires local schema checking.

## Result
Direct Chat Completions, encrypted settings, manual/optional automatic generation,
safe failure feedback, validated evidence references and synthetic API check are
implemented. Compilation passed; actual provider calls remain for the user after
they enter a key on the phone. The user added a follow-up request to address ADC
clipping markers blocking HRV. The final 0.1.5/code 6 APK includes that follow-up,
was installed successfully on the authorized Xiaomi (40c87980), and the installed
version was confirmed read-only. The app was not automatically launched or tested.
