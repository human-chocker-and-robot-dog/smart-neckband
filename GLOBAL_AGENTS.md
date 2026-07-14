# Global Codex Instructions for Windows and PowerShell

## 1. Scope

These rules apply whenever the host is Windows or the active shell is PowerShell. Follow them before running commands, inspecting or changing files, invoking external CLIs, editing `.ps1` scripts, handling Windows paths, or diagnosing command failures.

Do not depend on an installed PowerShell Skill. These instructions are the execution contract.

## 2. Shell and command selection

- Use PowerShell syntax in PowerShell sessions.
- Do not emit Bash syntax, Unix-only flags, or `cmd.exe` syntax unless the task explicitly requires that shell.
- Prefer repository-provided wrapper scripts over reconstructing long commands.
- Prefer native PowerShell cmdlets for file operations:
  - `Get-ChildItem`
  - `Get-Content`
  - `Set-Content`
  - `Copy-Item`
  - `Move-Item`
  - `Remove-Item`
  - `Test-Path`
  - `Resolve-Path`
- Prefer one clear command per step. Avoid dense one-liners that hide failures.
- Do not use `Invoke-Expression` for normal command execution.

## 3. Command risk classification

Classify each command before execution:

- `normal`: read-only inspection, version checks, `git status`, file listings, builds, and tests inside the workspace.
- `high`: external CLIs with uncertain availability, paths containing spaces or non-ASCII characters, archives, generated scripts, encoding-sensitive output, flashing hardware, or commands with multiple environment assumptions.
- `destructive`: recursive delete or move, broad overwrite, commands outside the workspace, Git history rewriting, force push, flash erase, eFuse operations, or partition changes on hardware.
- `diagnostic`: a changed command shape used after a previous command failed.

Normal commands may proceed. High-risk commands require extra validation. Destructive commands require explicit user approval unless a nearer repository instruction grants a narrower permission.

## 4. Preflight checks

Before a high-risk command:

1. Confirm the current directory with `Get-Location` when it is not already certain.
2. Check external tools with:

```powershell
Get-Command <tool> -ErrorAction SilentlyContinue
```

3. Check exact paths with:

```powershell
Test-Path -LiteralPath '<path>'
Resolve-Path -LiteralPath '<path>'
```

4. For destructive work, verify that the resolved target is the intended path and is inside the allowed workspace.
5. State or preserve the expected output, exit condition, and rollback path when applicable.

## 5. Paths and quoting

- Use `-LiteralPath` when operating on an exact path.
- Quote paths containing spaces, brackets, wildcard characters, or non-ASCII text.
- Use single quotes for literal strings and double quotes only when interpolation is intended.
- Use `Join-Path` rather than manually concatenating Windows path separators.
- Use the call operator for executable paths stored in variables:

```powershell
& $Executable @Arguments
```

- Do not assume the working directory. Use an explicit `-WorkingDirectory`, `Push-Location`/`Pop-Location`, or a repository wrapper.
- Do not silently operate outside the current repository.

## 6. External CLI execution

- Check `$LASTEXITCODE` after external applications.
- Treat nonzero exit codes as failures even when some output was produced.
- When output capture matters, use a clear pattern:

```powershell
$output = & $Executable @Arguments 2>&1
$exitCode = $LASTEXITCODE
$output
if ($exitCode -ne 0) {
    throw "Command failed with exit code $exitCode"
}
```

- Do not use PowerShell success state alone to judge an external application.
- Use argument arrays instead of constructing one interpolated command string.
- Avoid `Start-Process` when stdout, stderr, or the real exit code must be inspected. If it is required, use `-Wait` and `-PassThru`.
- Do not invoke helper scripts to bypass permissions, sandbox limits, or user approval requirements.

## 7. Encoding and text files

- Preserve the existing file encoding when practical.
- For new text files, use UTF-8 without relying on legacy Windows code pages.
- When writing files, specify encoding explicitly when the cmdlet supports it:

```powershell
Set-Content -LiteralPath $Path -Value $Content -Encoding utf8
```

- Do not pipe binary data through text cmdlets.
- If non-ASCII output is corrupted, diagnose console and process encoding before modifying source data.
- Do not commit raw private logs, credentials, tokens, serial numbers, or personal data.

## 8. PowerShell script quality

When creating or editing `.ps1` files:

- Add `Set-StrictMode -Version Latest` when compatible with the script.
- Set `$ErrorActionPreference = 'Stop'` for automation scripts.
- Validate parameters and paths before writes.
- Use `try`/`finally` around temporary directory changes or resources.
- Use `Push-Location` and `Pop-Location` in a `try`/`finally` pair.
- Use `$PSScriptRoot` to locate repository-relative files.
- Check `$LASTEXITCODE` after every external CLI whose success matters.
- Keep local machine settings, COM ports, secrets, and installation paths outside committed files.
- Do not hide errors with broad `-ErrorAction SilentlyContinue` except for intentional discovery checks.

## 9. Failure and retry policy

- Do not repeat the same failed command unchanged.
- After the first failure, inspect the error and change the command shape based on evidence.
- Check shell, working directory, quoting, path existence, tool availability, permissions, encoding, and exit code.
- After two distinct failed command shapes, stop and report the blocker unless new evidence clearly changes the diagnosis.
- Do not paper over failures by disabling validation or using a more destructive command.

## 10. Destructive-operation rules

Do not run any of the following without explicit user approval and resolved-target validation:

- `Remove-Item -Recurse -Force` on broad paths
- recursive moves outside the workspace
- broad overwrites
- `git reset --hard`
- `git clean -fd` or stronger variants
- force push or history rewriting
- disk, partition, registry, driver, eFuse, or firmware erase operations

Before an approved destructive filesystem operation:

1. Resolve the exact target.
2. Confirm it is not a drive root, user profile root, repository parent, or unrelated directory.
3. Prefer a narrowly scoped operation.
4. Report exactly what was changed.

## 11. Git defaults

- Inspect `git status --short --branch` before editing.
- Preserve unrelated user changes.
- Do not commit, amend, tag, push, force push, rebase published work, or rewrite history unless explicitly requested.
- Prefer small, reviewable changes.
- Report untracked and modified files that remain at the end.

## 12. Completion reporting

At the end of technical work, report:

- files changed,
- commands run,
- build and test results,
- anything not run,
- assumptions not verified on real hardware,
- remaining blockers.

Clearly separate verified facts from inferences.
