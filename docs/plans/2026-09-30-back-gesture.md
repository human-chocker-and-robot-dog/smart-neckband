# Back gesture safety

## Goal
Make Android back and edge-swipe behavior match the in-app hierarchy: a metric
detail returns to Today, while a top-level page requires two presses within two
seconds before the Activity exits.

## Scope
Use the existing Compose `BackHandler`; do not change acquisition or service
lifecycle behavior. The first top-level back shows a short native toast.

## Validation
Build the Debug APK only. The user will manually test edge-swipe from ECG,
heart-rate, HRV and activity pages, then test the two-step exit on Today.

## Progress
- [x] Add detail-page interception and double-back exit guard.
- [x] Build; installation is pending because ADB currently reports no device.
- [x] Commit and push.

## Result
Compose now consumes system back and edge-swipe events. A metric detail closes
to Today, while a top-level page shows a two-second double-back exit guard.
Debug APK 0.1.9 (code 10) builds successfully; the phone install is pending
until the test device is connected.
