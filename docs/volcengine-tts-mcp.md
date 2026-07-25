# Volcengine TTS MCP for Ubuntu 22.04

`tools/volcengine_tts_mcp.py` is a standalone stdio MCP child process for the
A process framework. It exposes one tool, `tts.speak`, and sends Volcengine V3
PCM directly to Ubuntu's default ALSA output.

The implementation uses the HTTP V3 unidirectional streaming endpoint because
each MCP call supplies complete text. It incrementally parses Volcengine's
concatenated JSON frames and writes each decoded PCM frame to a persistent
`aplay` process. This avoids temporary WAV files and repeated sound-device
startup.

## Call semantics

`tts.speak` returns as soon as the request enters the bounded local queue:

```json
{
  "text": "系统已经启动。",
  "volume": 70,
  "speech_rate": 0
}
```

Example result:

```json
{
  "accepted": true,
  "request_id": "e9b86bbd-1435-4edb-a85c-339e54951e0f",
  "queue_depth": 1,
  "volume": 70,
  "speech_rate": 0,
  "error": null
}
```

`accepted=true` means the persistent MCP process owns the request; it does not
mean playback has already ended. Network, permission, or ALSA failures after
acceptance are logged to stderr with `request_id`. The A process must keep the
MCP child alive after the tool call returns.

## Ubuntu installation

```bash
sudo apt-get update
sudo apt-get install -y alsa-utils python3-venv

cd /opt/smart-neckband
python3 -m venv .venv-tts
.venv-tts/bin/pip install -r tools/volcengine_tts_mcp.requirements.txt

sudo install -d -m 755 /etc/smart-neckband
sudo install -m 600 tools/volcengine_tts_mcp.env.example /etc/smart-neckband/tts.env
sudoedit /etc/smart-neckband/tts.env
```

Edit `/etc/smart-neckband/tts.env` and set at least:

- `VOLCENGINE_APPID`: V3 `X-Api-App-Key` value;
- `VOLCENGINE_TOKEN`: V3 `X-Api-Access-Key` value;
- `VOLCENGINE_RESOURCE_ID`: usually `seed-tts-2.0` for TTS 2.0;
- `VOLCENGINE_TTS_VOICE`: a speaker ID authorized for that resource.

The script loads credentials with `python-dotenv`. It never writes credentials
to MCP stdout or normal logs.

## Default 3.5 mm output

At startup the process runs both `aplay -L` and `aplay -l`, writes the scan to
stderr, then opens `VOLCENGINE_TTS_AUDIO_DEVICE`. The default value is
`default`, so Ubuntu's selected default sink is used. Select the headphone
output in Ubuntu before starting the service.

For a headless image, inspect names with:

```bash
aplay -L
aplay -l
```

Then override, for example:

```text
VOLCENGINE_TTS_AUDIO_DEVICE=plughw:CARD=Headphones,DEV=0
```

Prefer `default` when PipeWire or PulseAudio owns routing. A direct `hw:` device
can be exclusive and can reject the 24 kHz mono format; `plughw:` performs ALSA
format conversion when needed.

## A process MCP configuration

Use the virtual-environment Python directly; do not wrap stdio MCP in a shell:

```json
{
  "mcpServers": {
    "volcengine-tts": {
      "command": "/opt/smart-neckband/.venv-tts/bin/python",
      "args": [
        "/opt/smart-neckband/tools/volcengine_tts_mcp.py",
        "--env-file",
        "/etc/smart-neckband/tts.env"
      ]
    }
  }
}
```

Operational logs and the startup sound-card scan use stderr. stdout is reserved
exclusively for MCP JSON-RPC messages.

## Volume and latency

- `VOLCENGINE_TTS_VOLUME` sets the default output volume from 0 to 100.
- Each `tts.speak` call can override `volume` without changing the system mixer.
- `speech_rate` accepts the Volcengine range -50 to 100.
- `VOLCENGINE_TTS_ALSA_BUFFER_TIME_US=100000` and
  `VOLCENGINE_TTS_ALSA_PERIOD_TIME_US=20000` are low-latency starting values.
- The HTTPS connection and `aplay` process remain open across calls.
- The queue is bounded by `VOLCENGINE_TTS_QUEUE_SIZE`; a full queue returns an
  MCP error immediately instead of accumulating unbounded speech.

## Deployment verification

Run the server under an MCP inspector or the A process, list tools, and call
`tts.speak` with a short phrase. Verify:

1. startup stderr lists the expected ALSA card and PCM names;
2. the MCP result returns `accepted=true` promptly;
3. audio is heard on the selected 3.5 mm output;
4. stderr reports the same request ID as completed;
5. volume 0 is silent and volume 100 is the unscaled PCM level.

These live checks require real Volcengine credentials and the target Ubuntu
audio hardware; they cannot be proven by the Windows unit-test environment.
