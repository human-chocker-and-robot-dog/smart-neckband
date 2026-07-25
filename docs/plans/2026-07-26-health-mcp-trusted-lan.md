# Health MCP Trusted LAN Mode

## Goal

Make the hackathon Health MCP directly callable through the trusted CPE LAN
without application-level authentication, while retaining bounded process
lifecycle handling and the exact three read-only tools.

## Current state

- Streamable HTTP requires a Bearer token and Host allowlist.
- The main GUI exposes token generation and Host configuration.
- The headless launcher refuses to start without authentication settings.
- The official MCP client integration test starts a subprocess and already
  terminates and waits for it in `finally`.

## Scope

Included:

- remove MCP Bearer middleware, token validation, Host allowlist, and related
  CLI/environment/UI configuration;
- default the HTTP listener to `0.0.0.0:8765/mcp` for CPE access;
- update tests and operator documentation;
- run a real Streamable HTTP client call and verify that spawned test/service
  processes are gone afterward.

Excluded:

- changing the three MCP tools or their schemas;
- changing Health Webhook signing;
- firmware, packet, flash, firewall, CPE, or hardware changes.

## Design decisions

- The hackathon deployment trusts the CPE-managed private LAN boundary.
- Streamable HTTP is intentionally unauthenticated and must not be exposed to
  the public Internet.
- The GUI still manages MCP as a child process so closing the GUI stops it.
- Tests use `finally` with terminate, bounded wait, and kill fallback.

## Work breakdown

1. Remove MCP authentication and Host validation from the server and CLI.
2. Simplify GUI settings and the PowerShell launcher.
3. Update focused tests and current documentation.
4. Run focused HTTP integration and the complete PC test suite.
5. Audit and stop residual Health MCP processes, then merge to `main`.

## Validation

```powershell
.\tools\project.ps1 pc-test
git diff --check
```

The HTTP integration test must list all three tools and call
`health.get_imu_state` without an Authorization header. Its subprocess must be
waited to completion in all outcomes.

## Risks and rollback

- Anyone able to reach the port can call the read-only MCP tools. The CPE and
  private LAN are the only boundary for this hackathon version.
- Public exposure is explicitly unsupported.
- Rollback uses an ordinary revert commit.

## Progress

- [x] Confirm trusted-LAN requirement and inventory authentication paths.
- [x] Remove authentication code and configuration.
- [x] Validate real MCP connectivity and process cleanup.
- [x] Complete merge and push.

## Discoveries

- The MCP SDK accepts `security_settings=None`, which removes Host/Origin
  transport checks without custom replacement middleware.

## Result

The Streamable HTTP MCP now runs without Bearer authentication, Host
allowlisting, or transport-security settings. The main GUI and headless
launcher require only wearer/database plus host, port, and path. The official
MCP client connected without an Authorization header, listed the exact three
tools, and called `health.get_imu_state`; focused validation passed with
`16 passed`. The complete PC suite passed with `210 passed in 53.07s`. Process
audits after both runs found zero Health MCP and zero task-specific pytest
Python processes. Firmware and hardware were unchanged and not tested.
Commit `958296d` was fast-forwarded to `main` and pushed to the canonical
`origin`.
