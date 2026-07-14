import { useEffect, useMemo, useRef, useState, type CSSProperties, type RefObject } from "react";
import { createRoot } from "react-dom/client";
import QRCode from "qrcode";
import {
  ECG_SAMPLE_RATE_HZ,
  LiveEcgBatch,
  PublicStatus,
  ServerEventSchema,
  Snapshot,
  SNAPSHOT_SECONDS
} from "../lib/protocol";
import { DedupeState, reconnectDelayMs, shouldAcceptLiveBatch } from "./live-client";
import {
  DEFAULT_VIEWER_SETTINGS,
  ViewerSettings,
  loadViewerSettings,
  particleDriveFor,
  saveViewerSettings,
  solidBackgroundStyle
} from "./viewer-settings";
import "./styles.css";

const MAX_SAMPLES = ECG_SAMPLE_RATE_HZ * SNAPSHOT_SECONDS;
const SVG_WIDTH = 960;
const SVG_HEIGHT = 320;

type ConnectionState = "missing_config" | "connecting" | "live" | "reconnecting" | "closed";

type WaveformState = {
  samples: number[];
  rPeaks: number[];
};

type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  phase: number;
  depth: number;
  angle: number;
  radius: number;
};

type Plot = {
  polyline: string;
  markers: Array<{ x: number; y: number; key: string }>;
};

function wsUrl(sessionId: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL("/api/ws", `${protocol}//${window.location.host}`);
  url.searchParams.set("session_id", sessionId);
  return url.toString();
}

function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function statusLabel(status: PublicStatus | null, connection: ConnectionState): string {
  if (connection === "missing_config") {
    return "WAITING";
  }
  if (connection === "closed") {
    return "CLOSED";
  }
  if (!status) {
    return connection === "reconnecting" ? "RECONNECTING" : "OFFLINE";
  }
  if (status.state === "signal_lost") {
    return "SIGNAL LOST";
  }
  return status.state.toUpperCase();
}

function leadLabel(status: PublicStatus | null): string {
  if (!status) {
    return "--";
  }
  return status.lead_off ? "OFF" : "OK";
}

function trimWaveform(waveform: WaveformState, maxSamples: number): WaveformState {
  if (waveform.samples.length <= maxSamples) {
    return waveform;
  }
  const dropped = waveform.samples.length - maxSamples;
  return {
    samples: waveform.samples.slice(dropped),
    rPeaks: waveform.rPeaks.filter((peak) => peak >= dropped).map((peak) => peak - dropped)
  };
}

function appendBatch(waveform: WaveformState, batch: Pick<LiveEcgBatch, "samples" | "r_peaks">): WaveformState {
  const offset = waveform.samples.length;
  const newPeaks = batch.r_peaks
    .filter((peak) => peak >= 0 && peak < batch.samples.length)
    .map((peak) => offset + peak);
  return trimWaveform(
    {
      samples: [...waveform.samples, ...batch.samples],
      rPeaks: [...waveform.rPeaks, ...newPeaks]
    },
    MAX_SAMPLES
  );
}

function buildSnapshotWaveform(snapshot: Snapshot): WaveformState {
  return trimWaveform(
    snapshot.batches.reduce<WaveformState>(
      (current, batch) => appendBatch(current, batch),
      { samples: [], rPeaks: [] }
    ),
    MAX_SAMPLES
  );
}

function buildPlot(waveform: WaveformState): Plot {
  const { samples } = waveform;
  if (samples.length === 0) {
    return { polyline: "", markers: [] };
  }

  const min = Math.min(...samples);
  const max = Math.max(...samples);
  const span = Math.max(1e-6, max - min);
  const xFor = (index: number) => (samples.length === 1 ? SVG_WIDTH / 2 : (index / (samples.length - 1)) * SVG_WIDTH);
  const yFor = (value: number) => SVG_HEIGHT - ((value - min) / span) * SVG_HEIGHT;

  return {
    polyline: samples
      .map((value, index) => `${xFor(index).toFixed(1)},${yFor(value).toFixed(1)}`)
      .join(" "),
    markers: waveform.rPeaks
      .filter((peak) => peak >= 0 && peak < samples.length)
      .map((peak) => ({
        x: xFor(peak),
        y: yFor(samples[peak]),
        key: `${peak}-${samples[peak]}`
      }))
  };
}

function hexToRgb(color: string): [number, number, number] {
  const match = /^#([0-9a-fA-F]{6})$/.exec(color);
  if (!match) {
    return [255, 255, 255];
  }
  const value = Number.parseInt(match[1], 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function createParticle(width: number, height: number): Particle {
  const angle = Math.random() * Math.PI * 2;
  const radius = Math.random() * Math.max(width, height) * 0.54;
  return {
    x: Math.random() * width,
    y: Math.random() * height,
    vx: (Math.random() - 0.5) * 0.26,
    vy: (Math.random() - 0.5) * 0.26,
    size: 0.6 + Math.random() * 2.1,
    phase: Math.random() * Math.PI * 2,
    depth: 0.4 + Math.random() * 0.8,
    angle,
    radius
  };
}

function drawDot(
  context: CanvasRenderingContext2D,
  particle: Particle,
  x: number,
  y: number,
  rgb: [number, number, number],
  alpha: number,
  sizeBoost: number
): void {
  context.beginPath();
  context.fillStyle = `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
  context.arc(x, y, particle.size * particle.depth * sizeBoost, 0, Math.PI * 2);
  context.fill();
}

function useParticleLayer(
  canvasRef: RefObject<HTMLCanvasElement | null>,
  settings: ViewerSettings,
  drive: ReturnType<typeof particleDriveFor>
): void {
  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) {
      return undefined;
    }

    let width = 1;
    let height = 1;
    let frameId = 0;
    let particles: Particle[] = [];
    const rgb = hexToRgb(settings.particleColor);

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      width = Math.max(1, rect.width);
      height = Math.max(1, rect.height);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      particles = Array.from({ length: settings.particleCount }, () => createParticle(width, height));
    };

    const render = (timeMs: number) => {
      const time = timeMs / 1000;
      const pulse =
        drive.pulsePerSecond > 0 ? Math.max(0, Math.sin(time * Math.PI * 2 * drive.pulsePerSecond)) ** 4 : 0;
      const brightness = drive.brightness + pulse * drive.intensity * 0.38;
      const speed = Math.max(0.05, drive.speed);
      context.clearRect(0, 0, width, height);

      if (drive.active) {
        for (const particle of particles) {
          if (settings.particleEffect === "pulse") {
            const radius = particle.radius * (1 + pulse * 0.2 * drive.intensity);
            const spin = time * speed * 0.16 + particle.phase;
            const x = width / 2 + Math.cos(particle.angle + spin) * radius;
            const y = height / 2 + Math.sin(particle.angle + spin) * radius * 0.55;
            drawDot(context, particle, x, y, rgb, Math.min(0.92, brightness * particle.depth), 1 + pulse * 1.5);
          } else if (settings.particleEffect === "wave") {
            particle.x += (0.35 + particle.depth * 0.35) * speed;
            if (particle.x > width + 12) {
              particle.x = -12;
            }
            const wave = Math.sin(time * speed * 2 + particle.phase + particle.x * 0.015);
            const y = particle.y + wave * (18 + drive.intensity * 18) + pulse * 18;
            drawDot(context, particle, particle.x, y, rgb, Math.min(0.85, brightness * 0.8), 1 + pulse);
          } else {
            particle.x += particle.vx * (1 + speed);
            particle.y += particle.vy * (1 + speed);
            if (particle.x < -8) particle.x = width + 8;
            if (particle.x > width + 8) particle.x = -8;
            if (particle.y < -8) particle.y = height + 8;
            if (particle.y > height + 8) particle.y = -8;
            const shimmer = 0.65 + Math.sin(time * 1.3 + particle.phase) * 0.25 + pulse * 0.45;
            drawDot(context, particle, particle.x, particle.y, rgb, Math.min(0.9, brightness * shimmer), 1 + pulse);
          }
        }
      }

      frameId = window.requestAnimationFrame(render);
    };

    resize();
    window.addEventListener("resize", resize);
    frameId = window.requestAnimationFrame(render);

    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("resize", resize);
      context.clearRect(0, 0, width, height);
    };
  }, [
    canvasRef,
    drive.active,
    drive.brightness,
    drive.intensity,
    drive.pulsePerSecond,
    drive.speed,
    settings.particleColor,
    settings.particleCount,
    settings.particleEffect
  ]);
}

function App() {
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const sessionId = params.get("session_id") ?? "";
  const viewerToken = params.get("viewer_token") ?? "";
  const storage = useMemo(() => browserStorage(), []);
  const [settings, setSettings] = useState<ViewerSettings>(() => loadViewerSettings(storage));
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [connection, setConnection] = useState<ConnectionState>(sessionId && viewerToken ? "connecting" : "missing_config");
  const [status, setStatus] = useState<PublicStatus | null>(null);
  const [waveform, setWaveform] = useState<WaveformState>({ samples: [], rPeaks: [] });
  const [lastSeq, setLastSeq] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string>("");
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const dedupe = useRef<DedupeState>({ lastSeq: -1, lastTimestampMs: 0 });

  const drive = useMemo(
    () => particleDriveFor(status, settings),
    [settings, status?.hr_bpm, status?.state]
  );
  const pageStyle = useMemo(
    () =>
      ({
        ...solidBackgroundStyle(settings),
        "--accent": settings.accentColor
      }) as CSSProperties,
    [settings]
  );
  const plot = useMemo(() => buildPlot(waveform), [waveform]);

  useParticleLayer(canvasRef, settings, drive);

  useEffect(() => {
    saveViewerSettings(settings, storage);
  }, [settings, storage]);

  useEffect(() => {
    QRCode.toDataURL(window.location.href, { margin: 1, width: 180 })
      .then(setQrDataUrl)
      .catch(() => setQrDataUrl(""));
  }, []);

  useEffect(() => {
    if (!sessionId || !viewerToken) {
      return undefined;
    }

    let socket: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let reconnectTimer: number | null = null;

    const applySnapshot = (snapshot: Snapshot) => {
      setWaveform(buildSnapshotWaveform(snapshot));
      setStatus(snapshot.status);
      const lastBatch = snapshot.batches.at(-1);
      if (lastBatch) {
        dedupe.current.lastSeq = lastBatch.seq;
        dedupe.current.lastTimestampMs = lastBatch.timestamp_ms;
        setLastSeq(lastBatch.seq);
      }
    };

    const applyLiveBatch = (batch: LiveEcgBatch) => {
      if (!shouldAcceptLiveBatch(batch, dedupe.current)) {
        return;
      }
      setLastSeq(batch.seq);
      setWaveform((current) => appendBatch(current, batch));
    };

    const connect = () => {
      if (closed) {
        return;
      }
      setConnection(attempt === 0 ? "connecting" : "reconnecting");
      socket = new WebSocket(wsUrl(sessionId));
      socket.addEventListener("open", () => {
        setError(null);
        socket?.send(
          JSON.stringify({
            type: "auth",
            role: "viewer",
            viewer_token: viewerToken
          })
        );
      });
      socket.addEventListener("message", (event) => {
        let raw: unknown;
        try {
          raw = JSON.parse(String(event.data));
        } catch {
          setError("Ignored malformed server message");
          return;
        }

        const parsed = ServerEventSchema.safeParse(raw);
        if (!parsed.success) {
          setError("Ignored unsupported server message");
          return;
        }
        const message = parsed.data;
        if (message.type === "snapshot") {
          applySnapshot(message);
          setConnection("live");
          attempt = 0;
        } else if (message.type === "ecg_batch") {
          applyLiveBatch(message);
        } else if (message.type === "status") {
          setStatus(message);
        } else if (message.type === "signal_lost") {
          setStatus((current) =>
            current
              ? { ...current, state: "signal_lost", lead_off: true, timestamp_ms: message.timestamp_ms }
              : current
          );
        } else if (message.type === "session_stopped") {
          setConnection("closed");
          socket?.close();
        } else if (message.type === "error") {
          setError(message.message);
        }
      });
      socket.addEventListener("close", () => {
        if (closed) {
          return;
        }
        setConnection("reconnecting");
        const delay = reconnectDelayMs(attempt++);
        reconnectTimer = window.setTimeout(connect, delay);
      });
      socket.addEventListener("error", () => {
        setError("WebSocket connection error");
      });
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      socket?.close();
    };
  }, [sessionId, viewerToken]);

  const hr = status?.hr_bpm == null ? "--" : status.hr_bpm.toFixed(0);
  const sqi = status?.sqi == null ? "--" : status.sqi.toFixed(2);
  const statusText = statusLabel(status, connection);

  const updateSettings = (patch: Partial<ViewerSettings>) => {
    setSettings((current) => ({ ...current, ...patch }));
  };

  return (
    <main className="live-page" style={pageStyle}>
      <canvas ref={canvasRef} className="particle-layer" aria-hidden="true" />
      <div className="live-content">
        <header className="live-header">
          <div>
            <p className="eyebrow">AI Smart Collar V0</p>
            <h1>Live Beta</h1>
          </div>
          <div className="header-actions">
            <div className={`live-badge ${status?.state ?? connection}`}>{statusText}</div>
            <button type="button" className="ghost-button" onClick={() => setSettingsOpen(true)}>
              Settings
            </button>
          </div>
        </header>

        {connection === "missing_config" ? (
          <section className="notice">
            Missing viewer URL parameters. Open a session viewer_url generated by the admin API.
          </section>
        ) : null}

        <section className="hero-band" aria-label="live heart status">
          <div className={`heart-orb ${drive.mode}`}>
            <span>{hr}</span>
            <small>BPM</small>
          </div>
          <div className="hero-copy">
            <p className="eyebrow">Realtime clean ECG stream</p>
            <h2>{statusText}</h2>
            <div className="signal-row">
              <span>Lead {leadLabel(status)}</span>
              <span>SQI {sqi}</span>
              <span>Seq {lastSeq ?? "--"}</span>
            </div>
          </div>
        </section>

        <section className="stats" aria-label="live status">
          <div>
            <span>HR</span>
            <strong>{hr}</strong>
          </div>
          <div>
            <span>SQI</span>
            <strong>{sqi}</strong>
          </div>
          <div>
            <span>Lead</span>
            <strong>{leadLabel(status)}</strong>
          </div>
          <div>
            <span>Loss</span>
            <strong>{status?.packet_loss ?? "--"}</strong>
          </div>
          <div>
            <span>CRC</span>
            <strong>{status?.crc_errors ?? "--"}</strong>
          </div>
        </section>

        <section className={`ecg-panel ${settings.showEcgGrid ? "show-grid" : ""}`} aria-label="clean ECG waveform">
          <div className="panel-title">
            <div>
              <p className="eyebrow">Clean ECG</p>
              <h2>Last 10 seconds</h2>
            </div>
            <span>{waveform.samples.length} samples</span>
          </div>
          <svg viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`} preserveAspectRatio="none" role="img">
            {settings.showEcgGrid ? (
              <g className="ecg-grid-lines">
                {Array.from({ length: 12 }, (_, index) => (
                  <line key={`v-${index}`} x1={index * 80} y1="0" x2={index * 80} y2={SVG_HEIGHT} />
                ))}
                {Array.from({ length: 8 }, (_, index) => (
                  <line key={`h-${index}`} x1="0" y1={index * 40} x2={SVG_WIDTH} y2={index * 40} />
                ))}
              </g>
            ) : null}
            <line className="baseline" x1="0" y1="160" x2={SVG_WIDTH} y2="160" />
            {plot.polyline ? <polyline points={plot.polyline} /> : <text x="36" y="178">WAITING FOR LIVE ECG</text>}
            {plot.markers.map((marker) => (
              <circle key={marker.key} cx={marker.x} cy={marker.y} r="4.5" />
            ))}
          </svg>
        </section>

        <footer className="live-footer">
          <div>
            <strong>Live Beta</strong>
            <span>{sessionId || "No session"}</span>
            <span>Realtime visualization only, not medical diagnosis.</span>
            {error ? <span className="error">{error}</span> : null}
          </div>
          {qrDataUrl ? <img src={qrDataUrl} alt="Viewer URL QR code" /> : null}
        </footer>
      </div>

      <div className={`settings-backdrop ${settingsOpen ? "open" : ""}`} onClick={() => setSettingsOpen(false)} />
      <aside className={`settings-panel ${settingsOpen ? "open" : ""}`} aria-hidden={!settingsOpen}>
        <div className="settings-header">
          <div>
            <p className="eyebrow">Live Beta</p>
            <h2>Settings</h2>
          </div>
          <button type="button" className="ghost-button" onClick={() => setSettingsOpen(false)}>
            Close
          </button>
        </div>

        <section className="settings-section">
          <h3>Display</h3>
          <label>
            Background
            <input
              type="color"
              value={settings.backgroundColor}
              onChange={(event) => updateSettings({ backgroundColor: event.currentTarget.value })}
            />
          </label>
          <label>
            Theme
            <input
              type="color"
              value={settings.accentColor}
              onChange={(event) => updateSettings({ accentColor: event.currentTarget.value })}
            />
          </label>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={settings.showEcgGrid}
              onChange={(event) => updateSettings({ showEcgGrid: event.currentTarget.checked })}
            />
            ECG grid
          </label>
        </section>

        <section className="settings-section">
          <h3>Visual Effects</h3>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={settings.particlesEnabled}
              onChange={(event) => updateSettings({ particlesEnabled: event.currentTarget.checked })}
            />
            Particles
          </label>
          <label>
            Effect
            <select
              value={settings.particleEffect}
              onChange={(event) => updateSettings({ particleEffect: event.currentTarget.value as ViewerSettings["particleEffect"] })}
            >
              <option value="stars">Stars</option>
              <option value="pulse">Pulse</option>
              <option value="wave">Wave</option>
            </select>
          </label>
          <label>
            Particle color
            <input
              type="color"
              value={settings.particleColor}
              onChange={(event) => updateSettings({ particleColor: event.currentTarget.value })}
            />
          </label>
          <label>
            Particle count
            <input
              type="range"
              min="120"
              max="900"
              step="20"
              value={settings.particleCount}
              onChange={(event) => updateSettings({ particleCount: Number(event.currentTarget.value) })}
            />
          </label>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={settings.bindEffectsToHeartRate}
              onChange={(event) => updateSettings({ bindEffectsToHeartRate: event.currentTarget.checked })}
            />
            Bind to HR
          </label>
          <label>
            Binding strength
            <input
              type="range"
              min="0.1"
              max="2"
              step="0.1"
              value={settings.heartRateEffectIntensity}
              onChange={(event) => updateSettings({ heartRateEffectIntensity: Number(event.currentTarget.value) })}
            />
          </label>
        </section>

        <button type="button" className="reset-button" onClick={() => setSettings({ ...DEFAULT_VIEWER_SETTINGS })}>
          Reset visual settings
        </button>
      </aside>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
