import { useEffect, useRef, useState } from 'react';
import { SecondaryButton, Stat } from './primitives';

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

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Counts from wherever the number currently is to wherever it lands — never back to
 * zero — so a late-arriving metric update reads as a correction, not a restart.
 */
function useCountUp(target: number, durationMs = 900) {
  const [value, setValue] = useState(0);
  const shown = useRef(0);

  useEffect(() => {
    const from = shown.current;
    if (from === target) return;
    if (prefersReducedMotion()) {
      shown.current = target;
      setValue(target);
      return;
    }

    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      const next = Math.round(from + (target - from) * (1 - (1 - t) ** 3));
      shown.current = next;
      setValue(next);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);

  return value;
}

function CountStat({ target, label }: { target: number; label: string }) {
  const value = useCountUp(target);
  return <Stat size="hero" align="center" value={value} label={label} />;
}

export function MetricsPanel({
  metrics, totalTime, meanConfidence, idleReduction,
  label = 'Objective complete', contributions = [], onReset,
}: MetricsPanelProps) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-12 bg-[rgb(var(--background-rgb)/0.94)] px-8 backdrop-blur-xl">
      <h2 className="text-[24px] font-light tracking-tight text-text-primary">{label}</h2>

      {/*
        The grid is width-locked and the columns are equal fractions, so the six
        numbers hold their positions while they count up — a digit going from 9 to
        10 must not shove its neighbours sideways.
      */}
      <div className="grid w-full max-w-[1080px] grid-cols-3 gap-x-6 gap-y-12 sm:grid-cols-6">
        {metrics.map((m) => (
          <CountStat key={m.label} target={m.value} label={m.label} />
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-2 text-[15px] text-text-secondary">
        <span className="tabular-nums">{totalTime} total</span>
        <span className="tabular-nums">
          {Math.round(meanConfidence * 100)}% mean verification confidence
        </span>
        {idleReduction > 0 && (
          <span className="tabular-nums">
            {Math.round(idleReduction * 100)}% less worker idle time
          </span>
        )}
      </div>

      {/* Who actually carried the operation — attributed, not asserted. */}
      {contributions.length > 0 && (
        <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-2 text-[13px] text-text-tertiary">
          {contributions.map((c) => (
            <span key={c.worker_id} className="tabular-nums">
              <span className="text-text-secondary">{c.callsign}</span> {c.completed} action
              {c.completed === 1 ? '' : 's'}
              {c.mean_seconds ? ` · ${c.mean_seconds}s avg` : ''}
              {c.reliability < 1 ? ` · ${Math.round(c.reliability * 100)}% verified` : ''}
            </span>
          ))}
        </div>
      )}

      {onReset && <SecondaryButton onClick={onReset}>Reset</SecondaryButton>}
    </div>
  );
}
