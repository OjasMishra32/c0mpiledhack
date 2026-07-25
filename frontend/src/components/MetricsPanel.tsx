import { useEffect, useState } from 'react';

interface Metric {
  value: number;
  label: string;
}

interface Contribution {
  worker_id: string;
  callsign: string;
  completed: number;
  reliability: number;
  mean_seconds: number | null;
}

interface MetricsPanelProps {
  metrics: Metric[];
  totalTime: string;
  meanConfidence: number;
  idleReduction: number;
  label?: string;
  contributions?: Contribution[];
  onReset?: () => void;
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

function CountStat({ target, label }: { target: number; label: string }) {
  const value = useCountUp(target);
  return (
    <div className="flex flex-col items-center gap-0.5">
      <span className="tabular-nums text-[32px] font-semibold text-text-primary">{value}</span>
      <span className="text-[12px] text-text-tertiary">{label}</span>
    </div>
  );
}

export function MetricsPanel({
  metrics, totalTime, meanConfidence, idleReduction,
  label = 'Objective complete', contributions = [], onReset,
}: MetricsPanelProps) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-8 bg-background/95">
      <span className="text-[17px] font-semibold tracking-tight text-text-primary">{label}</span>
      <div className="grid grid-cols-3 gap-x-10 gap-y-6 sm:grid-cols-6">
        {metrics.map((m) => (
          <CountStat key={m.label} target={m.value} label={m.label} />
        ))}
      </div>
      <div className="flex items-center gap-6 text-[13px] text-text-secondary">
        <span className="tabular-nums">{totalTime} total</span>
        <span>{Math.round(meanConfidence * 100)}% mean verification confidence</span>
        {idleReduction > 0 && <span>{Math.round(idleReduction * 100)}% less worker idle time</span>}
      </div>

      {/* Who actually carried the operation — attributed, not asserted. */}
      {contributions.length > 0 && (
        <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-[12px] text-text-tertiary">
          {contributions.map((c) => (
            <span key={c.worker_id}>
              <span className="text-text-secondary">{c.callsign}</span> {c.completed} action
              {c.completed === 1 ? '' : 's'}
              {c.mean_seconds ? ` · ${c.mean_seconds}s avg` : ''}
              {c.reliability < 1 ? ` · ${Math.round(c.reliability * 100)}% verified` : ''}
            </span>
          ))}
        </div>
      )}

      {onReset && (
        <button
          onClick={onReset}
          className="rounded-[--r-control] border border-separator-strong px-3 py-1.5 text-[13px] text-text-secondary transition-colors hover:text-text-primary"
        >
          Reset
        </button>
      )}
    </div>
  );
}
