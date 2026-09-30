# Independent metric detail pages

## Goal
Make Today metric cards open to their own detail page. Heart rate, HRV and ECG
must no longer share one ECG screen; each detail page shows its current value,
meaning, and recent history.

## Scope
- Add a bounded in-memory history of fresh HR, RMSSD, motion and still values.
- Add independent heart, HRV, motion and ECG detail routes in Compose.
- Add compact recent trend charts on Today and full charts on metric detail pages.
- Keep acquisition, decoding and HRV calculation unchanged.

## Validation
Build the Debug APK only. Do not run automated tests or automatic navigation;
the user will manually verify each Today card and its back navigation.

## Progress
- [x] Implement history and routes.
- [x] Build; installation is pending because ADB currently reports no device.
- [x] Commit and push.

## Result
Today now routes heart rate, HRV, motion and ECG to separate detail pages.
Fresh analysis results retain a bounded 180-point in-memory metric history, with
recent HR/RMSSD previews on Today and full charts on their detail pages. The
Debug APK 0.1.8 (code 9) builds successfully and commit `bad03bb` is pushed;
installation remains pending until the test phone is connected.
