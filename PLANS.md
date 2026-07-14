# ExecPlan Rules

Use an ExecPlan for work that is expected to involve multiple components, more than one validation loop, protocol or architecture changes, or more than roughly 30 minutes of focused implementation.

Examples:

- introducing the ECG sampler and queue pipeline,
- adding Bluetooth SPP end to end,
- changing the binary protocol,
- integrating NeuroKit2 and the GUI,
- migrating from SPP to BLE,
- changing the partition table or storage design.

Store plans under:

```text
docs/plans/YYYY-MM-DD-short-topic.md
```

An ExecPlan is a living document. Update progress and discoveries while implementing.

## Required sections

```markdown
# <Plan title>

## Goal
Describe the user-visible or engineering outcome.

## Current state
List relevant files, behavior, constraints, and known hardware facts.

## Scope
State what is included and explicitly excluded.

## Design decisions
Record interfaces, pin assignments, timing, data ownership, and tradeoffs.

## Work breakdown
Use ordered, independently verifiable steps.

## Validation
List exact commands and hardware checks.

## Risks and rollback
Describe likely failure modes and how to revert safely.

## Progress
- [ ] item

## Discoveries
Record unexpected behavior, measurements, and decisions made during work.

## Result
Summarize what was actually completed and what remains unverified.
```

Do not create a ceremonial plan for a one-line documentation fix. Do not skip a plan for a cross-cutting hardware and software change.
