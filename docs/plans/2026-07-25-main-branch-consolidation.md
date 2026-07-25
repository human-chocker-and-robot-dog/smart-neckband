# Main Branch Consolidation

## Goal

Consolidate `docs/health-mcp-spec` and `feat/health-event-bridge-v0.3`
into `main`, validate the resulting unified firmware and PC application, then
delete every other merged local and remote branch while keeping
`fix/live-fb892fa-reliability` independent.

## Current state

- `main` is checked out in `data/worktrees/unified-integration` at `00e7e39`
  and is clean.
- `feat/health-event-bridge-v0.3` is checked out in the primary worktree and
  contains four commits not in `main`.
- `docs/health-mcp-spec` contains one commit not in `main`.
- `fix/live-fb892fa-reliability` contains two commits not in `main` and must
  remain independent.
- The primary and `docs/webhook-handoff` worktrees contain user-owned HANDOFF
  changes that must be backed up before worktree or branch removal.

## Scope

Included:

- preserve dirty HANDOFF files outside the worktrees being removed;
- merge the Health MCP specification and V0.3 Health Event Bridge into main;
- resolve conflicts in favor of the unified main architecture while retaining
  the three-tool MCP/Webhook product contract;
- run PC, protocol, and firmware validation required by the touched files;
- push main;
- remove all merged local and remote branches except main;
- retain `fix/live-fb892fa-reliability` locally and leave it unmerged.

Excluded:

- flashing, erasing, serial monitoring, eFuse changes, or body-connected work;
- merging or deleting `fix/live-fb892fa-reliability`;
- silently discarding user-owned HANDOFF content.

## Design decisions

- Main's unified sensor/microphone architecture is authoritative when merge
  conflicts touch firmware, protocol, acquisition, or build structure.
- Health V0.3 contributes exactly three public MCP tools, authenticated
  Streamable HTTP, derived metric history, motion scoring, local rules, and
  signed event Webhooks.
- Dirty HANDOFF files are copied to an ignored backup directory before any
  worktree cleanup.
- Branch deletion occurs only after required validation and a successful push.

## Work breakdown

1. Back up dirty HANDOFF files and record deleted-file markers.
2. Merge `docs/health-mcp-spec` into main and resolve documentation conflicts.
3. Merge `feat/health-event-bridge-v0.3` into main and resolve conflicts without
   regressing unified firmware/microphone behavior.
4. Run focused tests, the complete PC suite, protocol checks, firmware build,
   and size validation as required.
5. Review and push main.
6. Remove obsolete worktrees and delete all merged local/remote branches,
   retaining only main and `fix/live-fb892fa-reliability` locally.

## Validation

```powershell
.\tools\project.ps1 pc-test
.\tools\project.ps1 build -Target esp32c3 -Voice
.\tools\project.ps1 size -Target esp32c3 -Voice
git diff --check
git status --short --branch
git branch --all
git worktree list
```

No soak longer than five minutes is permitted. No flash or hardware access is
authorized.

## Risks and rollback

- Directly merging an older branch can roll back unified main files. Resolve
  conflicts and review the final tree against main rather than accepting an
  entire side mechanically.
- Deleting a checked-out branch requires removing or switching its worktree.
- Dirty HANDOFF files can be lost by forced worktree removal; back them up and
  verify the copies first.
- Rollback uses ordinary revert commits. Do not rewrite main history or force
  push.

## Progress

- [x] Back up dirty HANDOFF changes.
- [ ] Merge `docs/health-mcp-spec`.
- [ ] Merge `feat/health-event-bridge-v0.3`.
- [ ] Complete validation and review.
- [ ] Push main.
- [ ] Delete obsolete branches and worktrees.

## Discoveries

- Most non-main branches are already ancestors of main.
- `docs/health-mcp-spec`, `feat/health-event-bridge-v0.3`, and
  `fix/live-fb892fa-reliability` are the only local branches with unique
  commits relative to main.
- Three untracked HANDOFF replacements and two deletion intents were preserved
  under ignored `data/branch-cleanup-backup-20260725` before cleanup.

## Result

Work is in progress.
