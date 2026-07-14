import React, { useEffect, useMemo, useRef, useState } from "react";
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
import { DedupeState, reconnectDelayMs, shouldAcceptLiveBatch, trimToRecentSamples } from "./live-client";
import "./styles.css";

const MAX_SAMPLES = ECG_SAMPLE_RATE_HZ * SNAPSHOT_SECONDS;

type ConnectionState = "missing_config" | "connecting" | "live" | "reconnecting" | "closed";

function wsUrl(sessionId: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL("/api/ws", `${protocol}//${window.location.host}`);
  url.searchParams.set("session_id", sessionId);
  return url.toString();
}

function statusLabel(status: PublicStatus | null): string {
  if (!status) {
    return "OFFLINE";
  }
  if (status.state === "signal_lost") {
    return "SIGNAL LOST";
  }
  return status.state.toUpperCase();
}

function samplesToPolyline(samples: number[], width: number, height: number): string {
  if (samples.length === 0) {
    return "";
  }
  const min = Math.min(...samples);
  const max = Math.max(...samples);
  const span = Math.max(1, max - min);
  return samples
    .map((value, index) => {
      const x = samples.length === 1 ? width : (index / (samples.length - 1)) * width;
      const y = height - ((value - min) / span) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function App() {
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const sessionId = params.get("session_id") ?? "";
  const viewerToken = params.get("viewer_token") ?? "";
  const [connection, setConnection] = useState<ConnectionState>(sessionId && viewerToken ? "connecting" : "missing_config");
  const [status, setStatus] = useState<PublicStatus | null>(null);
  const [samples, setSamples] = useState<number[]>([]);
  const [rPeaks, setRPeaks] = useState<number[]>([]);
  const [lastSeq, setLastSeq] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string>("");
  const dedupe = useRef<DedupeState>({ lastSeq: -1, lastTimestampMs: 0 });

  useEffect(() => {
    QRCode.toDataURL(window.location.href, { margin: 1, width: 180 })
      .then(setQrDataUrl)
      .catch(() => setQrDataUrl(""));
  }, []);

  useEffect(() => {
    if (!sessionId || !viewerToken) {
      return;
    }

    let socket: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let reconnectTimer: number | null = null;

    const applySnapshot = (snapshot: Snapshot) => {
      const snapshotSamples = snapshot.batches.flatMap((batch) => batch.samples);
      setSamples(trimToRecentSamples(snapshotSamples, MAX_SAMPLES));
      setRPeaks([]);
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
      setSamples((current) => trimToRecentSamples([...current, ...batch.samples], MAX_SAMPLES));
      setRPeaks(batch.r_peaks);
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
        const parsed = ServerEventSchema.safeParse(JSON.parse(String(event.data)));
        if (!parsed.success) {
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

  const polyline = samplesToPolyline(samples, 960, 320);
  const hr = status?.hr_bpm == null ? "--" : status.hr_bpm.toFixed(0);
  const sqi = status?.sqi == null ? "--" : status.sqi.toFixed(2);

  return (
    <main className="app-shell">
      <header>
        <div>
          <p className="eyebrow">AI Smart Collar V0</p>
          <h1>Live Heartbeat</h1>
        </div>
        <div className={`status ${status?.state ?? "offline"}`}>{statusLabel(status)}</div>
      </header>

      {connection === "missing_config" ? (
        <section className="notice">Missing viewer URL parameters. Open a session viewer_url generated by the admin API.</section>
      ) : null}

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
          <strong>{status?.lead_off ? "OFF" : "OK"}</strong>
        </div>
        <div>
          <span>Seq</span>
          <strong>{lastSeq ?? "--"}</strong>
        </div>
        <div>
          <span>Link</span>
          <strong>{connection}</strong>
        </div>
      </section>

      <section className="waveform" aria-label="clean ECG waveform">
        <svg viewBox="0 0 960 320" role="img">
          <line x1="0" y1="160" x2="960" y2="160" />
          <polyline points={polyline} />
          {rPeaks.map((peak) => {
            const x = samples.length > 1 ? (peak / Math.max(1, MAX_SAMPLES - 1)) * 960 : 0;
            return <circle key={`${lastSeq}-${peak}`} cx={x} cy="42" r="4" />;
          })}
        </svg>
      </section>

      <section className="footer-panel">
        <div>
          <h2>Viewer URL</h2>
          <p>This token is intentionally kept in the URL for this session only and is not written to localStorage.</p>
          {error ? <p className="error">{error}</p> : null}
        </div>
        {qrDataUrl ? <img src={qrDataUrl} alt="Viewer URL QR code" /> : null}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
