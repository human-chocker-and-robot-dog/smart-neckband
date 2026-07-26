# AdventureX Health Dashboard ExecPlan

## Goal

Add a mobile-first public `/dashboard` that reuses the existing Vercel live
session and the main PC GUI's single acquisition pipeline to display cleaned
ECG, heart rate, device state, IMU attitude, motion score, UWB state, and recent
engineering health events.

## Progress

- [x] Create isolated `feat/health-dashboard` worktree from `main`.
- [x] Add backward-compatible telemetry/event schemas, Redis state, Pub/Sub, and tests.
- [x] Add shared-state PC relay, UWB provider interface, reconnect behavior, and GUI status.
- [x] Add independent AdventureX React Dashboard and explicit `?demo=1` mode.
- [x] Complete full web and PC validation.
- [x] Prepare validated changes for three coherent commits and clean `main` integration.

## Constraints

- Preserve `/live` and `/viewer` behavior.
- Never open a second hardware acquisition connection from the relay.
- Keep raw ECG/IMU, secrets, voice content, and long-term history off the public service.
- Keep demo and unavailable values visibly distinct from live measurements.
- No flashing, serial monitor, or body-connected acquisition is required.
