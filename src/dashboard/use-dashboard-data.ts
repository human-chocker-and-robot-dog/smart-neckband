import { useEffect, useMemo, useState } from "react";
import {
  ECG_SAMPLE_RATE_HZ,
  LiveHealthEvent,
  LiveTelemetry,
  PublicStatus,
  ServerEventSchema,
  Snapshot
} from "../../lib/protocol.js";
import { appendEcgBatch, EcgPointBuffer } from "../live-rendering.js";

const MAX_SAMPLES = ECG_SAMPLE_RATE_HZ * 10;

export type DashboardConnection = "connecting" | "live" | "reconnecting" | "offline" | "demo";

export type HeartTrendPoint = {
  timestampMs: number;
  bpm: number;
};

export type DashboardData = {
  demo: boolean;
  connection: DashboardConnection;
  waveform: EcgPointBuffer;
  telemetry: LiveTelemetry | null;
  status: PublicStatus | null;
  events: LiveHealthEvent[];
  heartTrend: HeartTrendPoint[];
  lastSeq: number | null;
};

type LiveSessionResponse = {
  type: "live_session";
  session_id: string;
  viewer_token: string;
};

function isLiveSessionResponse(value: unknown): value is LiveSessionResponse {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    record.type === "live_session" &&
    typeof record.session_id === "string" &&
    typeof record.viewer_token === "string"
  );
}

function appendHeartTrend(points: HeartTrendPoint[], telemetry: LiveTelemetry): HeartTrendPoint[] {
  if (telemetry.heart.bpm === null) return points;
  const next = [...points, { timestampMs: telemetry.timestamp_ms, bpm: telemetry.heart.bpm }];
  const cutoff = telemetry.timestamp_ms - 60_000;
  return next.filter((point, index) => point.timestampMs >= cutoff && (index === 0 || point.timestampMs !== next[index - 1]?.timestampMs));
}

function mergeEvent(events: LiveHealthEvent[], event: LiveHealthEvent): LiveHealthEvent[] {
  const existing = events.find((item) => item.event_id === event.event_id);
  if (existing && existing.event_revision >= event.event_revision) return events;
  return [event, ...events.filter((item) => item.event_id !== event.event_id)]
    .sort((left, right) => right.timestamp_ms - left.timestamp_ms)
    .slice(0, 20);
}

function waveformFromSnapshot(snapshot: Snapshot): EcgPointBuffer {
  return snapshot.batches.reduce<EcgPointBuffer>(
    (buffer, batch) => appendEcgBatch(buffer, batch.samples, batch.r_peaks, MAX_SAMPLES),
    { samples: [], rPeaks: [] }
  );
}

function websocketUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/ws`;
}

function demoEcgValue(index: number, bpm: number): number {
  const samplesPerBeat = (ECG_SAMPLE_RATE_HZ * 60) / bpm;
  const phase = (index % samplesPerBeat) / samplesPerBeat;
  const gaussian = (center: number, width: number, height: number) =>
    height * Math.exp(-((phase - center) ** 2) / (2 * width ** 2));
  return (
    0.025 * Math.sin((index / ECG_SAMPLE_RATE_HZ) * Math.PI * 2 * 0.35) +
    gaussian(0.18, 0.025, 0.12) -
    gaussian(0.38, 0.012, 0.18) +
    gaussian(0.405, 0.009, 1.15) -
    gaussian(0.435, 0.014, 0.32) +
    gaussian(0.68, 0.055, 0.26)
  );
}

function useDemoData(enabled: boolean): DashboardData {
  const [waveform, setWaveform] = useState<EcgPointBuffer>({ samples: [], rPeaks: [] });
  const [telemetry, setTelemetry] = useState<LiveTelemetry | null>(null);
  const [status, setStatus] = useState<PublicStatus | null>(null);
  const [heartTrend, setHeartTrend] = useState<HeartTrendPoint[]>([]);
  const sessionId = "sess_demo_dashboard_0123456789";

  const events = useMemo<LiveHealthEvent[]>(
    () => [
      {
        type: "health_event",
        session_id: sessionId,
        event_id: "demo-event-motion",
        event_revision: 1,
        event_type: "activity.motion_changed",
        transition: "opened",
        severity: "info",
        priority: "normal",
        timestamp_ms: Date.now() - 18_000,
        summary: "运动强度从静止切换为轻度活动。"
      },
      {
        type: "health_event",
        session_id: sessionId,
        event_id: "demo-event-contact",
        event_revision: 2,
        event_type: "signal.lead_off",
        transition: "resolved",
        severity: "warning",
        priority: "normal",
        timestamp_ms: Date.now() - 52_000,
        summary: "ECG 电极接触已恢复。"
      }
    ],
    []
  );

  useEffect(() => {
    if (!enabled) return;
    let sampleIndex = 0;
    let lastTrendSecond = -1;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      const now = Date.now();
      const elapsed = (now - startedAt) / 1000;
      const bpm = 76 + Math.sin(elapsed / 8) * 4;
      const batch = Array.from({ length: 50 }, () => demoEcgValue(sampleIndex++, bpm));
      setWaveform((current) => appendEcgBatch(current, batch, [], MAX_SAMPLES));
      const motionScore = Math.max(0, Math.min(100, 30 + Math.sin(elapsed / 4) * 22));
      const nextTelemetry: LiveTelemetry = {
        type: "telemetry",
        session_id: sessionId,
        timestamp_ms: now,
        heart: { bpm, sqi: 0.94, lead_off: false },
        imu: {
          online: true,
          roll_deg: Math.sin(elapsed / 2.7) * 18,
          pitch_deg: Math.cos(elapsed / 3.4) * 12,
          yaw_deg: Math.sin(elapsed / 6) * 28,
          motion_score: motionScore,
          still_ratio_percent: Math.max(0, 100 - motionScore * 1.35),
          level: motionScore < 10 ? "still" : motionScore < 35 ? "light" : motionScore < 65 ? "moderate" : "vigorous"
        },
        uwb: {
          distance_m: 3.2 + Math.sin(elapsed / 5) * 1.1,
          quality: 0.88,
          source: "demo"
        },
        device: {
          transport: "demo",
          ecg_sample_rate_hz: ECG_SAMPLE_RATE_HZ,
          imu_sample_rate_hz: 50,
          packet_loss: 0,
          crc_errors: 0,
          data_age_ms: 0
        }
      };
      setTelemetry(nextTelemetry);
      setStatus({
        type: "status",
        session_id: sessionId,
        state: "live",
        timestamp_ms: now,
        last_ingest_at_ms: now,
        hr_bpm: bpm,
        sqi: 0.94,
        lead_off: false,
        packet_loss: 0,
        crc_errors: 0,
        note: "DEMO DATA"
      });
      const second = Math.floor(elapsed);
      if (second !== lastTrendSecond) {
        lastTrendSecond = second;
        setHeartTrend((current) => appendHeartTrend(current, nextTelemetry));
      }
    }, 100);
    return () => window.clearInterval(timer);
  }, [enabled]);

  return { demo: true, connection: "demo", waveform, telemetry, status, events, heartTrend, lastSeq: null };
}

function useLiveData(enabled: boolean): DashboardData {
  const [connection, setConnection] = useState<DashboardConnection>("connecting");
  const [waveform, setWaveform] = useState<EcgPointBuffer>({ samples: [], rPeaks: [] });
  const [telemetry, setTelemetry] = useState<LiveTelemetry | null>(null);
  const [status, setStatus] = useState<PublicStatus | null>(null);
  const [events, setEvents] = useState<LiveHealthEvent[]>([]);
  const [heartTrend, setHeartTrend] = useState<HeartTrendPoint[]>([]);
  const [lastSeq, setLastSeq] = useState<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let stopped = false;
    let attempt = 0;
    let sessionId = "";
    let viewerToken = "";

    const scheduleReconnect = () => {
      if (stopped) return;
      setConnection("reconnecting");
      const delay = Math.min(30_000, 500 * 2 ** attempt++);
      reconnectTimer = window.setTimeout(connect, delay);
    };

    const applyTelemetry = (next: LiveTelemetry) => {
      setTelemetry(next);
      setHeartTrend((current) => appendHeartTrend(current, next));
    };

    const connect = () => {
      if (!sessionId || !viewerToken || stopped) return;
      socket = new WebSocket(`${websocketUrl()}?session_id=${encodeURIComponent(sessionId)}`);
      socket.addEventListener("open", () => {
        socket?.send(JSON.stringify({ type: "auth", role: "viewer", viewer_token: viewerToken }));
      });
      socket.addEventListener("message", (event) => {
        let raw: unknown;
        try {
          raw = JSON.parse(String(event.data));
        } catch {
          return;
        }
        const parsed = ServerEventSchema.safeParse(raw);
        if (!parsed.success) return;
        const message = parsed.data;
        if (message.type === "auth_ok") {
          attempt = 0;
          setConnection("live");
        } else if (message.type === "snapshot") {
          setWaveform(waveformFromSnapshot(message));
          setStatus(message.status);
          setEvents(message.recent_events);
          if (message.telemetry) applyTelemetry(message.telemetry);
          setLastSeq(message.batches.at(-1)?.seq ?? null);
          attempt = 0;
          setConnection("live");
        } else if (message.type === "ecg_batch") {
          setWaveform((current) => appendEcgBatch(current, message.samples, message.r_peaks, MAX_SAMPLES));
          setLastSeq(message.seq);
        } else if (message.type === "telemetry") {
          applyTelemetry(message);
        } else if (message.type === "health_event") {
          setEvents((current) => mergeEvent(current, message));
        } else if (message.type === "status") {
          setStatus(message);
        } else if (message.type === "signal_lost") {
          setStatus((current) =>
            current
              ? { ...current, state: "signal_lost", lead_off: true, timestamp_ms: message.timestamp_ms }
              : current
          );
        } else if (message.type === "session_stopped") {
          stopped = true;
          setConnection("offline");
          socket?.close();
        }
      });
      socket.addEventListener("close", scheduleReconnect);
      socket.addEventListener("error", () => socket?.close());
    };

    void fetch("/api/live-session", { cache: "no-store" })
      .then(async (response) => {
        const body: unknown = await response.json();
        if (!response.ok || !isLiveSessionResponse(body)) throw new Error("live session unavailable");
        sessionId = body.session_id;
        viewerToken = body.viewer_token;
        connect();
      })
      .catch(() => setConnection("offline"));

    return () => {
      stopped = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [enabled]);

  return { demo: false, connection, waveform, telemetry, status, events, heartTrend, lastSeq };
}

export function useDashboardData(): DashboardData {
  const demo = new URLSearchParams(window.location.search).get("demo") === "1";
  const live = useLiveData(!demo);
  const demoData = useDemoData(demo);
  return demo ? demoData : live;
}
