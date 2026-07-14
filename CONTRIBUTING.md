# Contributing and Git Workflow

## Branch model

This project uses a lightweight protected-main workflow.

- `main`: always buildable, represents the latest accepted V0 state.
- `feat/<topic>`: new behavior.
- `fix/<topic>`: bug fixes.
- `test/<topic>`: test-only work.
- `docs/<topic>`: documentation-only work.
- `chore/<topic>`: tooling and maintenance.

A long-lived `develop` branch is intentionally not used. For a solo hardware prototype, it adds ceremony without increasing reliability.

## Starting work

```powershell
git status --short --branch
git switch main
git pull --ff-only
git switch -c feat/<topic>
```

Do not run `git pull` automatically if the repository has local changes or if network access was not requested.

## Commit format

Use Conventional Commits:

```text
feat(firmware): add GPTimer ECG sampler
fix(protocol): reject invalid payload length
test(pc): add CRC corruption cases
docs(hardware): correct classic ESP32 ADC pin
chore(build): pin ESP-IDF target and flash size
```

Each commit should have one coherent purpose and should leave the repository in a buildable or intentionally documented intermediate state.

## Before a commit

Firmware work:

```powershell
.\tools\project.ps1 build
.\tools\project.ps1 size
```

PC work:

```powershell
.\tools\project.ps1 pc-test
```

Protocol work requires both.

Then inspect:

```powershell
git diff --check
git status --short
git diff --stat
git diff
```

## Commit and push authority

Codex must not create commits, tags, or pushes unless the user explicitly asks. A request to “write the code” is not permission to commit or push.

## Versioning

Use semantic versions for repository milestones:

- `v0.1.0`: bench acquisition skeleton.
- `v0.2.0`: stable raw ECG and IMU transport.
- `v0.3.0`: NeuroKit2 and real-time UI.
- `v1.0.0`: wearable demonstration baseline.

Use prerelease tags when useful:

```text
v0.2.0-alpha.1
v0.2.0-beta.1
```

## Files that belong in Git

Commit:

- source code,
- tests,
- `sdkconfig.defaults`,
- `partitions.csv`,
- protocol schemas and golden vectors,
- dependency lock files,
- project scripts,
- documentation and plans.

Do not commit:

- `firmware/build/`,
- generated `sdkconfig`,
- local COM-port settings,
- Python virtual environments,
- raw ECG recordings,
- secrets,
- temporary serial logs,
- editor caches.
