# Volcengine TTS MCP plan

## Goal

Provide a lightweight standalone Python MCP server for an Ubuntu 22.04 Agent
process. A successful MCP call must enqueue speech and return immediately while
Volcengine TTS audio is streamed to the operating system's default 3.5 mm audio
output.

## Current state

- The repository already pins the Python MCP SDK for the independent Health MCP.
- Existing MCP servers use stdio and reserve stdout for JSON-RPC.
- There is no TTS playback service in the repository.
- The target host is Ubuntu 22.04 with ALSA `aplay` available.

## Scope

Included:

- one standalone Python script exposing `tts.speak` over MCP stdio;
- Volcengine V3 HTTP unidirectional streaming with PCM output;
- startup ALSA device discovery and default-device playback;
- a bounded background queue and immediate acceptance response;
- per-request 0--100 software volume and speech-rate control;
- unit tests, environment template, dependency list, and operator guide.

Excluded:

- firmware changes, microphone/ASR changes, GUI integration, and hardware flash;
- synchronous playback-completion callbacks;
- changing the system mixer or selecting a physical jack behind the OS default.

## Design decisions

- Use the V3 HTTP unidirectional endpoint because each MCP request contains a
  complete text string before synthesis starts.
- Use stdio MCP so the A process can own and supervise one persistent child.
- Return after bounded-queue admission. Synthesis and playback failures after
  admission are written to stderr with the request ID.
- Keep one `aplay` process open with a small ALSA buffer to reduce per-utterance
  process and device-open latency.
- Use only the Python standard library on the audio/network path. External
  Python dependencies are limited to the MCP SDK and `python-dotenv`.

## Work breakdown

1. Implement configuration validation, concatenated JSON parsing, PCM scaling,
   Volcengine streaming, ALSA discovery/playback, and the background service.
2. Add the MCP `tts.speak` contract and stdio entry point.
3. Add deterministic tests for parsing, scaling, queue behavior, and payloads.
4. Document Ubuntu installation and A-process configuration.

## Validation

```powershell
.\tools\project.ps1 pc-test
git diff --check
```

No real Volcengine credentials or Ubuntu sound device are available in the
Windows workspace, so live synthesis and 3.5 mm playback remain deployment
checks.

## Risks and rollback

- ALSA device names vary by image; default to `default` and allow an explicit
  `VOLCENGINE_TTS_AUDIO_DEVICE` override.
- Acceptance is asynchronous; post-acceptance failures are observable only on
  stderr. The request ID correlates logs with MCP calls.
- A persistent direct ALSA hardware device may be exclusive. Use the Ubuntu
  `default` device or a PipeWire/Pulse ALSA plugin unless exclusivity is wanted.
- Rollback is removal of the standalone script, its tests, docs, and examples;
  no existing runtime or protocol is modified.

## Progress

- [x] Confirm repository MCP conventions and Volcengine interface choice.
- [x] Implement the standalone service.
- [x] Add tests and documentation.
- [x] Run validation and review the final diff.

## Discoveries

- The repository's Health MCP establishes the required stdio child-process
  pattern and stderr-only operational logging convention.
- The host Windows PowerShell execution policy blocks direct `.ps1` execution;
  the same repository test wrapper passed when invoked with a process-local
  `-ExecutionPolicy Bypass`.

## Result

Implemented a standalone V3 HTTP streaming TTS MCP with a persistent ALSA sink,
bounded asynchronous queue, per-call volume and speech-rate controls, Ubuntu
deployment documentation, and six focused tests. The full PC suite passed with
216 tests. Live Volcengine synthesis and Ubuntu 3.5 mm playback remain unverified
because the Windows workspace has neither target audio hardware nor credentials.
