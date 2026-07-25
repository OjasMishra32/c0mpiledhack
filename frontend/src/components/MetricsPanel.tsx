import { useEffect, useState } from 'react';

interface Metric {
  value: number;
  label: string;
  sublabel: string;
}

interface MetricsPanelProps {
  metrics: Metric[];
  totalTime: string;
  meanConfidence: number;
  idleReduction: number;
}

function useCountUp(target: number, durationMs = 600) {
  const [value, setValue] = useState(0);
  useEffect(() => {
    let raf: number;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      setValue(Math.round(target * (1 - (1 - t) ** 3)));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);
  return value;
}

function CountStat({ target, label, sublabel }: { target: number; label: string; sublabel: string }) {
  const value = useCountUp(target);
  return (
    <div className="flex flex-col items-center gap-1">
      <span className="font-mono text-[64px] font-light leading-none tracking-[-0.03em] text-fg-0">{value}</span>
      <span className="text-center text-[11px] font-semibold uppercase tracking-[0.14em] text-fg-2">
        {label}
        <br />
        {sublabel}
      </span>
    </div>
  );
}

export function MetricsPanel({ metrics, totalTime, meanConfidence, idleReduction }: MetricsPanelProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-10 bg-bg-0">
      <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-fg-2">
        Incident Stabilized
      </span>
      <div className="grid grid-cols-3 gap-x-12 gap-y-8 sm:grid-cols-6">
        {metrics.map((m) => (
          <CountStat key={m.label} target={m.value} label={m.label} sublabel={m.sublabel} />
        ))}
      </div>
      <div className="flex items-center gap-6 text-[13px] text-fg-1">
        <span className="font-mono">⏱ {totalTime} total</span>
        <span>◈ {Math.round(meanConfidence * 100)}% mean verification confidence</span>
        <span>↓ {Math.round(idleReduction * 100)}% responder idle time</span>
      </div>
    </div>
  );
}
