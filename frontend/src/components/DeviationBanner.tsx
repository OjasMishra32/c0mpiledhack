import { useEffect, useState } from 'react';
import type { Deviation } from '../hooks/useHiveState';

/**
 * The deviation beat.
 *
 * Reality diverged from the plan and the screen has to land that in about two
 * seconds, from ten feet away, without stopping anything. So: it frames the
 * viewport, dims and desaturates the workspace *behind a blur the live scene
 * keeps updating through* — the whole claim is that everything else kept
 * running — states the divergence, then morphs in place into the resolution
 * and dissolves.
 *
 * It never unmounts and remounts between those two states, and it is
 * `pointer-events-none` throughout. This is an overlay, never a modal; the
 * operator can keep working under it the entire time.
 */

type Phase = 'detected' | 'resolved' | 'dissolving';

const FALLBACK_RESOLVE_MS = 2400; // the beat completes even if no narration arrives
const MORPH_MS = 400;
const HOLD_MS = 1400;

interface DeviationBannerProps {
  deviation: Deviation;
  recovery: string | null;
  /** Scenario lexicon term — never hardcode a scenario's wording. */
  label?: string;
}

export function DeviationBanner({
  deviation,
  recovery,
  label = 'World state deviation',
}: DeviationBannerProps) {
  const [phase, setPhase] = useState<Phase>('detected');

  useEffect(() => {
    if (phase !== 'detected') return;
    if (recovery) {
      setPhase('resolved');
      return;
    }
    const t = window.setTimeout(() => setPhase('resolved'), FALLBACK_RESOLVE_MS);
    return () => window.clearTimeout(t);
  }, [recovery, phase]);

  useEffect(() => {
    if (phase !== 'resolved') return;
    const t = window.setTimeout(() => setPhase('dissolving'), MORPH_MS + HOLD_MS);
    return () => window.clearTimeout(t);
  }, [phase]);

  const resolved = phase !== 'detected';
  const dissolving = phase === 'dissolving';
  const paused = deviation.action_ids.length;

  return (
    <>
      {/* Frames the whole screen, not just the workspace — this is a system-level event. */}
      <div
        className={`pointer-events-none fixed inset-0 z-40 ${dissolving ? 'dev-dissolving' : ''}`}
        aria-hidden
      >
        <span className="dev-edge dev-edge-t" />
        <span className="dev-edge dev-edge-r" />
        <span className="dev-edge dev-edge-b" />
        <span className="dev-edge dev-edge-l" />
      </div>

      <div
        role="status"
        aria-live="assertive"
        className={`pointer-events-none absolute inset-0 z-20 flex items-center justify-center
          bg-background/45 backdrop-blur-[2px] backdrop-saturate-50
          ${dissolving ? 'dev-dissolving' : 'dev-scrim'}`}
      >
        <div
          className="dev-panel w-[min(30rem,88%)] rounded-surface border bg-surface-primary/95 px-6 py-5
            shadow-2xl transition-colors duration-300 ease-standard"
          style={{
            borderColor: resolved ? 'var(--success)' : 'var(--failure)',
          }}
        >
          <div
            className="text-[11px] font-medium uppercase tracking-[0.14em] transition-colors duration-300 ease-standard"
            style={{ color: resolved ? 'var(--success)' : 'var(--failure)' }}
          >
            {label}
          </div>

          <dl className="mt-4 flex flex-col gap-2">
            <Row term="Expected" value={deviation.expected} />
            <Row term="Observed" value={deviation.observed} tone={resolved ? undefined : 'failure'} />
            <Row
              term="Impact"
              value={paused ? `${paused} action${paused === 1 ? '' : 's'} paused` : deviation.message}
            />
          </dl>

          {/* The morph. Same element, same position — only its contents cross over. */}
          <div className="mt-5 grid min-h-[1.25rem] grid-cols-1 grid-rows-1 items-center">
            <div
              className={`col-start-1 row-start-1 flex items-center gap-2 transition-opacity duration-300 ease-standard ${
                resolved ? 'opacity-0' : 'opacity-100'
              }`}
            >
              <span className="dev-working text-[13px]" style={{ color: 'var(--failure)' }}>
                ◈
              </span>
              <span className="text-[13px] font-medium uppercase tracking-[0.1em] text-text-primary">
                Replanning
              </span>
            </div>

            <div
              className={`col-start-1 row-start-1 flex items-center gap-2 transition-opacity duration-300 ease-standard ${
                resolved ? 'opacity-100' : 'opacity-0'
              }`}
            >
              <span className="text-[13px]" style={{ color: 'var(--success)' }}>
                ◆
              </span>
              <span className="text-[13px] text-text-secondary">
                {recovery ?? 'Plan adjusted. Unaffected work never stopped.'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function Row({ term, value, tone }: { term: string; value: string; tone?: 'failure' }) {
  return (
    <div className="flex items-baseline gap-3">
      <dt className="w-[4.5rem] shrink-0 text-[11px] uppercase tracking-[0.1em] text-text-tertiary">
        {term}
      </dt>
      <dd
        className="text-[15px] leading-snug transition-colors duration-300 ease-standard"
        style={{ color: tone === 'failure' ? 'var(--failure)' : 'var(--text-primary)' }}
      >
        {value}
      </dd>
    </div>
  );
}
