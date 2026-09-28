"""Android adapter around the unchanged PC analysis and motion modules.

Called serially from the Android acquisition worker. Raw counts stay in bounded
rings; cleaning never writes back into them. Device sample time drives RR/HRV.
"""
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import statistics
import time
from uuid import uuid4

from smart_neckband.analysis import analyze_recent_ecg
from smart_neckband.buffers import EcgSample, ImuSample
from smart_neckband.health_motion import analyze_motion
from smart_neckband.protocol import (
    FLAG_LO_MINUS, FLAG_LO_PLUS, FLAG_ADC_CLIPPING, FLAG_SAMPLE_LATE,
    FLAG_SAMPLE_MISSED, FLAG_SAMPLE_QUEUE_OVERFLOW, FLAG_TRANSPORT_OVERFLOW,
    FLAG_HISTORICAL_DATA,
)

# Adapter metadata only: never modify the original wire flags in raw_ecg.
MISSING_PACKET = 1 << 8
TIMESTAMP_JUMP = 1 << 9
TIMING_FLAGS = (FLAG_SAMPLE_LATE | FLAG_SAMPLE_MISSED |
                FLAG_SAMPLE_QUEUE_OVERFLOW | FLAG_TRANSPORT_OVERFLOW |
                MISSING_PACKET | TIMESTAMP_JUMP)


def trace(samples, values=None):
    """No stride decimation: retain narrow R peaks and explicit data gaps."""
    if not samples:
        return dict(values=[], seconds=[], breaks=[])
    first_us = samples[0].timestamp_us
    breaks = [i for i in range(1, len(samples))
              if samples[i].sample_index != samples[i - 1].sample_index + 1
              or abs(samples[i].timestamp_us - samples[i - 1].timestamp_us - 2000) > 6000]
    return dict(values=list(values) if values is not None else [s.raw_adc for s in samples],
                seconds=[(s.timestamp_us - first_us) / 1e6 for s in samples], breaks=breaks)


class Engine:
    def __init__(self):
        # Resolve native libraries before accepting live samples. First import
        # must not make an already-buffered ECG look newly received.
        import neurokit2  # noqa: F401
        self.ecg = deque(maxlen=5000)
        self.raw_ecg = deque(maxlen=5000)
        self.imu = deque(maxlen=1500)
        self.rr = deque(maxlen=256)
        self.source = str(uuid4())
        self.next_index = None
        self.last_batch_us = None
        self.next_imu_index = None
        self.last_rr_end = -1
        self.valid_since_us = None
        self.last_ecg_ns = 0
        self.last_imu_ns = 0
        self.lead_off = False
        self.discontinuities = 0

    def append(self, encoded):
        batch = json.loads(str(encoded))
        now_ns = time.monotonic_ns() - int(batch.get("received_age_ms", 0) * 1e6)
        utc = datetime.now(timezone.utc).isoformat()
        index, timestamp = batch["first"], batch["timestamp"]
        flags = batch["flags"]
        if batch["kind"] == "ecg":
            if flags & FLAG_HISTORICAL_DATA:
                self.ecg.clear()
                self._reset_rr()
                self.next_index = None
                self.last_batch_us = None
                return
            index_gap = self.next_index is not None and index != self.next_index
            time_gap = bool(self.ecg and abs(timestamp - self.ecg[-1].timestamp_us - 2000) > 6000)
            restarted = (self.next_index is not None and index < self.next_index) or (
                self.last_batch_us is not None and timestamp <= self.last_batch_us)
            long_gap = bool(self.ecg and timestamp - self.ecg[-1].timestamp_us > 1_000_000)
            if restarted or long_gap:
                self.ecg.clear()
            if restarted:
                # A reboot begins a new device timeline, not a noisy-data reset.
                self.raw_ecg.clear()
            if index_gap or time_gap or flags:
                self._reset_rr()
            self.discontinuities += int(index_gap or time_gap)
            self.next_index = (index + len(batch["samples"])) & 0xFFFFFFFF
            self.last_batch_us = timestamp
            self.last_ecg_ns = now_ns
            self.lead_off = bool(flags & 3)
            for offset, raw in enumerate(batch["samples"]):
                sample = EcgSample(index + offset, timestamp + offset * 2000,
                    raw, flags, self.source, (index + offset) & 0xFFFFFFFF, now_ns, utc)
                self.raw_ecg.append(sample)
                extra = ((MISSING_PACKET if index_gap else 0) | (TIMESTAMP_JUMP if time_gap else 0)) if offset == 0 else 0
                self.ecg.append(replace(sample, flags=flags | extra) if extra else sample)
        else:
            if flags & 0x80:
                return
            if self.next_imu_index is not None and index != self.next_imu_index:
                self.imu.clear()
            if self.imu and abs(timestamp - self.imu[-1].timestamp_us - 20000) > 60000:
                self.imu.clear()
            self.next_imu_index = (index + len(batch["samples"])) & 0xFFFFFFFF
            self.last_imu_ns = now_ns
            for offset, axes in enumerate(batch["samples"]):
                self.imu.append(ImuSample(index + offset, timestamp + offset * 20000,
                    *axes, flags, self.source, index + offset, now_ns, utc))

    def _reset_rr(self):
        self.rr.clear()
        self.last_rr_end = -1
        self.valid_since_us = None

    def analyze(self):
        now_ns = time.monotonic_ns()
        ecg_age_ms = (now_ns - self.last_ecg_ns) / 1e6 if self.last_ecg_ns else None
        imu_fresh = self.last_imu_ns and now_ns - self.last_imu_ns < 3_000_000_000
        motion = analyze_motion(tuple(self.imu) if imu_fresh else (), source_instance_id=self.source)
        raw_samples = tuple(self.raw_ecg)
        samples = tuple(self.ecg)
        window_flags = 0
        for sample in samples:
            window_flags |= sample.flags
        span_us = raw_samples[-1].timestamp_us - raw_samples[0].timestamp_us if len(raw_samples) > 1 else 0
        output = dict(bpm=None, rmssd=None, quality=None, lead_off=self.lead_off,
            motion=motion.score, still=motion.still_ratio_percent, motion_level=motion.level,
            imu_online=bool(imu_fresh), age_ms=ecg_age_ms,
            raw=trace(raw_samples), cleaned=trace(()),
            sample_count=len(raw_samples), analysis_sample_count=len(samples),
            effective_rate_hz=(len(raw_samples) - 1) * 1e6 / span_us if span_us > 0 else None,
            timing_warning=bool(window_flags & TIMING_FLAGS),
            message=f"ECG 分析窗口 {len(samples)}/5000 点", rr_count=0, hrv_window_s=0,
            discontinuities=self.discontinuities, analysis="NeuroKit2 / PC shared implementation")
        if ecg_age_ms is None or ecg_age_ms > 3000:
            self.ecg.clear()
            self._reset_rr()
            output.update(raw=trace(()), sample_count=0, analysis_sample_count=0,
                          effective_rate_hz=None, message="等待新数据")
            return json.dumps(output, allow_nan=False)
        if len(self.ecg) < 5000:
            return json.dumps(output, allow_nan=False)
        try:
            result = analyze_recent_ecg(samples)
        except Exception as error:
            self._reset_rr()
            output["message"] = f"分析不可用：{type(error).__name__}"
            return json.dumps(output, allow_nan=False)
        output.update(quality=result.signal_quality, cleaned=trace(samples, result.cleaned),
            message=result.message)
        if result.message == "ECG clipped":
            self._reset_rr()
            output.update(cleaned=trace(()), message="ADC 持续削顶；已保留原始波形")
            return json.dumps(output, allow_nan=False)
        output["age_ms"] = (time.monotonic_ns() - self.last_ecg_ns) / 1e6
        if output["age_ms"] >= 3000:
            self._reset_rr()
            output["message"] = "分析期间数据已过期，等待新数据"
            return json.dumps(output, allow_nan=False)
        if window_flags & (FLAG_LO_MINUS | FLAG_LO_PLUS | FLAG_SAMPLE_QUEUE_OVERFLOW |
                           FLAG_TRANSPORT_OVERFLOW | MISSING_PACKET):
            self._reset_rr()
            output["message"] = "电极脱落或数据缺失；保留波形，暂停心率与 HRV"
            return json.dumps(output, allow_nan=False)
        quality_ok = result.signal_quality is not None and result.signal_quality >= 0.5
        # Keep cleaned waveform visible with its quality; never promote a poor
        # quality HR estimate into a live number or an Insight.
        if not quality_ok or result.heart_rate_bpm is None:
            self._reset_rr()
            output["message"] = "信号质量不足，暂不显示心率与 HRV"
            return json.dumps(output, allow_nan=False)
        output["bpm"] = result.heart_rate_bpm
        if window_flags:
            self._reset_rr()
            output["message"] = "采样存在时序或削顶告警；心率供参考，HRV 暂停"
            return json.dumps(output, allow_nan=False)
        if motion.score is None or motion.score >= 10:
            self._reset_rr()
            output["message"] = "心率已更新；HRV 等待稳定静息数据"
            return json.dumps(output, allow_nan=False)
        newest = self.ecg[-1]
        if self.valid_since_us is None:
            self.valid_since_us = newest.timestamp_us
        # Leave half a second at the trailing edge for overlap to settle.
        peaks = result.r_peak_indices
        for before, after in zip(peaks, peaks[1:]):
            sample = self.ecg[after]
            if after > len(self.ecg) - 250 or sample.sample_index <= self.last_rr_end:
                continue
            rr_ms = (after - before) * 2.0
            self.last_rr_end = sample.sample_index
            if not 300 <= rr_ms <= 2000:
                self.rr.clear()
                self.valid_since_us = newest.timestamp_us
                continue
            if self.rr and abs(rr_ms - self.rr[-1][1]) > self.rr[-1][1] * .25:
                self.rr.clear()
                self.valid_since_us = newest.timestamp_us
            self.rr.append((sample.timestamp_us, rr_ms))
        while self.rr and self.rr[0][0] < newest.timestamp_us - 60_000_000:
            self.rr.popleft()
        duration = max(0, (newest.timestamp_us - self.valid_since_us) / 1e6)
        output.update(rr_count=len(self.rr), hrv_window_s=min(60, duration))
        if duration >= 60 and len(self.rr) >= 30:
            values = [r[1] for r in self.rr]
            output["rmssd"] = math.sqrt(statistics.fmean((b - a) ** 2 for a, b in zip(values, values[1:])))
            output["message"] = "实时数据 · HRV 为最近 60 秒静息 RMSSD"
        else:
            output["message"] = f"心率已更新；HRV 静息窗口 {int(duration)}/60 秒"
        return json.dumps(output, allow_nan=False)
