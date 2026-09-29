# Everyday Insight cards

## Goal
Use the phone's current HR/HRV/activity values, including labelled HRV references,
to produce everyday body observations like the demo. Replace the single long
diagnostic explanation with three concise, visually distinct cards.

## Current state
0.1.5 discards reference HRV before an API request and explicitly prompts the
model to explain missing data and signal quality. Its input also repeats hardware
errors in evidence. The UI renders the resulting text inside one large card.

## Scope
Insight input/prompt/response contract, local fallbacks, persistence, demos and
Compose presentation. Preserve acquisition algorithms and raw data. Do not run
automated tests or navigate the phone; build and authorized installation only.

## Design decisions
- Send the same fresh values the user can see, including reference RMSSD with a
  reference label. Omit absent metrics; no invented replacement measurements.
- Keep hardware diagnostics in Detail/Settings. Insight evidence contains actual
  body metrics, without missing-data messages or troubleshooting instructions.
- One API call returns three schema-v2 cards: rhythm, activity and suggestion.
  Each cites supplied evidence. Numeric evidence is mapped locally.
- Use compact themed cards, evidence chips and one group provenance/footer.
- Group each response atomically. Keep old history accessible but collapsed;
  old diagnostic cards must not remain the default first view after upgrade.
- Preserve the existing network opt-in, credentials, bounded requests and demo
  isolation. Automatic generation can use current reference data too.

## Work breakdown
1. Replace the input builder and prompt; validate three-card responses.
2. Integrate grouped persistence, local fallbacks and three-card example/demo.
3. Build the card feed, update copy and documents, bump the version.
4. Review sources, assemble, install, then commit/push.

## Validation
`gradlew :app:assembleDebug` only. `adb install -r` and read-only package version.
No automated tests, replay, paid API calls or automatic phone navigation. The
user will manually validate live model wording and the new layout.

## Risks and rollback
Reference values can be inaccurate; keep approximate labels and avoid numerical
stress scores, diagnoses, or invented historical trends. A valid JSON response
does not guarantee factual correctness. Keep prior cards/configuration intact.

## Progress
- [x] Identify the prompt/input/UI causes.
- [x] Implement and review.
- [x] Build/install and document results.
- [x] Prepare reviewed changes for the repository commit/push workflow.

## Discoveries
The former demo also contained a hardware-quality card; replace it so the demo
consistently represents the desired everyday interpretation experience.

## Result
0.1.6/code 7 uses fresh displayed values, including reference RMSSD, as everyday
Insight input. Missing metrics are omitted and hardware messages are excluded.
The full prompt is now in InsightPrompt.kt. A validated schema-v2 response holds
three distinct themes; all three persist atomically with locally mapped evidence.
The feed uses separate green/blue/warm cards, evidence chips, a highlighted action
and grouped provenance. Older diagnostic history is preserved behind a collapsed
history control. Local fallback and synthetic API checks use the same structure.

`:app:assembleDebug` succeeded in 14 seconds. Source review and `git diff --check`
completed. `adb -s 40c87980 install -r` returned Success; read-only package output
confirms versionCode=7/versionName=0.1.6. No automated tests, synthetic replay,
automatic phone navigation or actual model requests were run. Provider wording
and visual behavior on the user's live record remain for manual acceptance.
