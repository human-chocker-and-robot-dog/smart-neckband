import { useEffect, useRef } from "react";
import { EcgPointBuffer } from "../live-rendering.js";

type EcgChartProps = {
  waveform: EcgPointBuffer;
};

export function EcgChart({ waveform }: EcgChartProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const width = Math.max(1, rect.width);
      const height = Math.max(1, rect.height);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, width, height);

      context.strokeStyle = "rgba(255,255,255,0.055)";
      context.lineWidth = 1;
      for (let x = 0; x <= width; x += 32) {
        context.beginPath();
        context.moveTo(x, 0);
        context.lineTo(x, height);
        context.stroke();
      }
      for (let y = 0; y <= height; y += 32) {
        context.beginPath();
        context.moveTo(0, y);
        context.lineTo(width, y);
        context.stroke();
      }

      const samples = waveform.samples;
      if (samples.length < 2) return;
      const min = Math.min(...samples);
      const max = Math.max(...samples);
      const span = Math.max(0.001, max - min);
      const xFor = (index: number) => (index / (samples.length - 1)) * width;
      const yFor = (value: number) => height * 0.82 - ((value - min) / span) * height * 0.64;

      context.save();
      context.strokeStyle = "#f7f7f3";
      context.shadowColor = "rgba(255,107,0,0.34)";
      context.shadowBlur = 8;
      context.lineWidth = 1.8;
      context.lineJoin = "round";
      context.beginPath();
      samples.forEach((sample, index) => {
        const x = xFor(index);
        const y = yFor(sample);
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();
      context.restore();

      context.fillStyle = "#ff6b00";
      for (const peak of waveform.rPeaks) {
        if (peak < 0 || peak >= samples.length) continue;
        context.beginPath();
        context.arc(xFor(peak), yFor(samples[peak]), 2.8, 0, Math.PI * 2);
        context.fill();
      }
    };
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    draw();
    return () => observer.disconnect();
  }, [waveform]);

  return <canvas className="ecg-chart" ref={canvasRef} aria-label="实时 PC-cleaned ECG 波形" />;
}
