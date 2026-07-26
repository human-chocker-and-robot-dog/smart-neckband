import { useEffect, useMemo, useState } from "react";
import { AttitudeScene } from "./AttitudeScene.js";
import { EcgChart } from "./EcgChart.js";
import { HeartTrendPoint, useDashboardData } from "./use-dashboard-data.js";

const numberText = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined || !Number.isFinite(value) ? "--" : value.toFixed(digits);

function connectionText(connection: string): string {
  return {
    connecting: "CONNECTING",
    reconnecting: "RECONNECTING",
    live: "LIVE",
    offline: "OFFLINE",
    demo: "DEMO DATA"
  }[connection] ?? connection.toUpperCase();
}

function motionLevelText(level: string | null | undefined): string {
  return {
    still: "静止",
    light: "轻度活动",
    moderate: "中度活动",
    vigorous: "剧烈活动"
  }[level ?? ""] ?? "等待数据";
}

function eventTypeText(type: string): string {
  return {
    "signal.lead_off": "电极接触",
    "signal.adc_clipping": "ADC 削顶",
    "input.stale": "数据延迟",
    "input.offline": "数据离线",
    "cardio.high_hr_low_motion": "低运动高心率",
    "cardio.hrv_threshold_low_motion": "低运动 HRV 阈值",
    "cardio.extreme_hr_high_motion": "高运动高心率",
    "activity.motion_changed": "运动状态"
  }[type] ?? type;
}

function formatAge(ageMs: number | null): string {
  if (ageMs === null) return "--";
  if (ageMs < 1000) return `${ageMs} ms`;
  return `${(ageMs / 1000).toFixed(ageMs < 10_000 ? 1 : 0)} s`;
}

function Sparkline({ points }: { points: HeartTrendPoint[] }) {
  const path = useMemo(() => {
    if (points.length < 2) return "";
    const values = points.map((point) => point.bpm);
    const min = Math.min(...values) - 2;
    const max = Math.max(...values) + 2;
    const span = Math.max(1, max - min);
    return points
      .map((point, index) => {
        const x = (index / (points.length - 1)) * 100;
        const y = 42 - ((point.bpm - min) / span) * 38;
        return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
  }, [points]);
  return (
    <svg className="sparkline" viewBox="0 0 100 44" preserveAspectRatio="none" aria-label="最近一分钟心率趋势">
      <path d={path} />
    </svg>
  );
}

function StatusCell({ label, value, state = "neutral" }: { label: string; value: string; state?: string }) {
  return (
    <div className={`status-cell status-${state}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function App() {
  const data = useDashboardData();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);

  const telemetry = data.telemetry;
  const status = data.status;
  const bpm = telemetry?.heart.bpm ?? status?.hr_bpm ?? null;
  const sqi = telemetry?.heart.sqi ?? status?.sqi ?? null;
  const leadOff = telemetry?.heart.lead_off ?? status?.lead_off ?? false;
  const ageMs = telemetry ? Math.max(0, now - telemetry.timestamp_ms) : null;
  const motionScore = telemetry?.imu.motion_score ?? null;
  const motionProgress = Math.min(1, Math.max(0, (motionScore ?? 0) / 100));
  const uwbDistance = telemetry?.uwb.distance_m ?? null;
  const uwbAvailable = uwbDistance !== null;
  const displayConnection =
    data.demo
      ? "demo"
      : data.connection === "live" && (telemetry?.device.data_age_ms === null || telemetry?.device.data_age_ms === undefined || telemetry.device.data_age_ms > 3000)
        ? "offline"
        : data.connection;
  const signalState = leadOff ? "danger" : sqi !== null && sqi < 0.6 ? "warning" : "ok";
  const connectionState = displayConnection === "live" || displayConnection === "demo" ? "ok" : "warning";

  return (
    <main className="dashboard-shell">
      <section className="dashboard-frame">
        <div className="ambient ambient-orange" />
        <div className="ambient ambient-blue" />
        {data.demo && <div className="demo-ribbon">演示数据 · 非真实采集</div>}

        <header className="dashboard-header">
          <div>
            <p className="eyebrow">AI SMART COLLAR / HEALTH DASHBOARD</p>
            <h1>现场健康遥测</h1>
          </div>
          <div className="header-status" aria-live="polite">
            <span className={`signal-dot signal-${connectionState}`} />
            <strong>{connectionText(displayConnection)}</strong>
            <span>更新 {formatAge(ageMs)}</span>
          </div>
        </header>

        <section className="dashboard-grid">
          <article className="panel heart-panel">
            <div className="panel-heading">
              <div>
                <span className="panel-index">01</span>
                <h2>心率</h2>
              </div>
              <span className={`quality-label quality-${signalState}`}>
                {leadOff ? "LEAD OFF" : sqi === null ? "SQI --" : `SQI ${sqi.toFixed(2)}`}
              </span>
            </div>
            <div className="heart-readout">
              <strong>{numberText(bpm)}</strong>
              <span>BPM</span>
            </div>
            <Sparkline points={data.heartTrend} />
            <p className="panel-note">工程观测值，不构成医疗诊断。</p>
          </article>

          <article className="panel ecg-panel">
            <div className="panel-heading">
              <div>
                <span className="panel-index">02</span>
                <h2>实时 ECG</h2>
              </div>
              <span className="mono-label">PC-CLEANED / 500 HZ SOURCE / SEQ {data.lastSeq ?? "--"}</span>
            </div>
            <EcgChart waveform={data.waveform} />
          </article>

          <article className="panel attitude-panel">
            <div className="panel-heading">
              <div>
                <span className="panel-index">03</span>
                <h2>IMU 姿态</h2>
              </div>
              <span className={`quality-label quality-${telemetry?.imu.online ? "ok" : "warning"}`}>
                {telemetry?.imu.online ? "ONLINE" : "WAITING"}
              </span>
            </div>
            <AttitudeScene
              roll={telemetry?.imu.roll_deg ?? null}
              pitch={telemetry?.imu.pitch_deg ?? null}
              yaw={telemetry?.imu.yaw_deg ?? null}
            />
            <div className="axis-values">
              <div><span>ROLL</span><strong>{numberText(telemetry?.imu.roll_deg, 1)}°</strong></div>
              <div><span>PITCH</span><strong>{numberText(telemetry?.imu.pitch_deg, 1)}°</strong></div>
              <div><span>YAW</span><strong>{numberText(telemetry?.imu.yaw_deg, 1)}°</strong></div>
            </div>
            <p className="panel-note">Yaw 由陀螺仪积分，长时间运行可能漂移。</p>
          </article>

          <article className="panel motion-panel">
            <div className="panel-heading">
              <div>
                <span className="panel-index">04</span>
                <h2>运动强度</h2>
              </div>
              <span className="mono-label">MOTION SCORE</span>
            </div>
            <div className="motion-readout">
              <strong>{numberText(motionScore)}</strong>
              <span>/ 100</span>
            </div>
            <h3>{motionLevelText(telemetry?.imu.level)}</h3>
            <div className="metric-row">
              <span>静止时间占比</span>
              <strong>{numberText(telemetry?.imu.still_ratio_percent, 0)}%</strong>
            </div>
            <div className="bottom-signal"><span style={{ transform: `scaleX(${motionProgress})` }} /></div>
          </article>

          <article className="panel uwb-panel">
            <div className="panel-heading">
              <div>
                <span className="panel-index">05</span>
                <h2>人狗距离</h2>
              </div>
              <span className={`quality-label quality-${uwbAvailable ? "ok" : "warning"}`}>
                UWB {telemetry?.uwb.source?.toUpperCase() ?? "UNAVAILABLE"}
              </span>
            </div>
            <div className="distance-readout">
              <strong>{numberText(uwbDistance, 2)}</strong>
              <span>METERS</span>
            </div>
            <div className="metric-row">
              <span>测距质量</span>
              <strong>{telemetry?.uwb.quality === null || telemetry?.uwb.quality === undefined ? "--" : `${Math.round(telemetry.uwb.quality * 100)}%`}</strong>
            </div>
            <p className="panel-note">
              {uwbAvailable ? "实时距离只用于现场可视化。" : "真实 UWB provider 尚未接入，当前不生成伪造距离。"}
            </p>
            <div className="bottom-signal"><span style={{ transform: `scaleX(${uwbAvailable ? telemetry?.uwb.quality ?? 0.5 : 0})` }} /></div>
          </article>

          <article className="panel events-panel">
            <div className="panel-heading">
              <div>
                <span className="panel-index">06</span>
                <h2>实时事件</h2>
              </div>
              <span className="mono-label">LATEST {Math.min(6, data.events.length)} / {data.events.length}</span>
            </div>
            <div className="event-list">
              {data.events.length === 0 ? (
                <div className="empty-state">暂无健康或系统事件</div>
              ) : (
                data.events.slice(0, 6).map((event) => (
                  <div className={`event-row severity-${event.severity}`} key={`${event.event_id}:${event.event_revision}`}>
                    <span className="event-mark" />
                    <div>
                      <div className="event-title">
                        <strong>{eventTypeText(event.event_type)}</strong>
                        <span>{event.transition === "opened" ? "OPEN" : "RESOLVED"}</span>
                      </div>
                      <p>{event.summary}</p>
                    </div>
                    <time>{new Date(event.timestamp_ms).toLocaleTimeString("zh-CN", { hour12: false })}</time>
                  </div>
                ))
              )}
            </div>
          </article>

          <article className="panel status-panel">
            <div className="panel-heading compact-heading">
              <div>
                <span className="panel-index">07</span>
                <h2>链路状态</h2>
              </div>
            </div>
            <div className="status-grid">
              <StatusCell label="连接" value={connectionText(displayConnection)} state={connectionState} />
              <StatusCell label="电极" value={leadOff ? "脱落" : "正常"} state={leadOff ? "danger" : "ok"} />
              <StatusCell label="ECG" value="500 Hz" state="ok" />
              <StatusCell label="IMU" value={telemetry?.imu.online ? "ONLINE" : "--"} state={telemetry?.imu.online ? "ok" : "warning"} />
              <StatusCell label="丢包" value={String(telemetry?.device.packet_loss ?? status?.packet_loss ?? 0)} />
              <StatusCell label="CRC" value={String(telemetry?.device.crc_errors ?? status?.crc_errors ?? 0)} />
              <StatusCell label="传输" value={(telemetry?.device.transport ?? "--").toUpperCase()} />
              <StatusCell label="数据年龄" value={formatAge(telemetry?.device.data_age_ms ?? ageMs)} state={ageMs !== null && ageMs > 3000 ? "warning" : "neutral"} />
            </div>
          </article>
        </section>

        <footer className="dashboard-footer">
          <span>AI SMART COLLAR V0</span>
          <span>READ-ONLY PUBLIC VIEW</span>
          <span>NO DIAGNOSIS / NO DOG-STATE INFERENCE</span>
        </footer>
      </section>
    </main>
  );
}
