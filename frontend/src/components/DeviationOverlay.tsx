import { useEffect, useState } from 'react';
import type { DeviationDetectedPayload, RecoveryCompletedPayload } from '../types/hive';

type Phase = 'deviation' | 'recovering' | 'resolved';

interface DeviationOverlayProps {
  deviation: DeviationDetectedPayload | null;
  recovery: RecoveryCompletedPayload | null;
  onDismiss?: () => void;
}

export function DeviationOverlay({ deviation, recovery, onDismiss }: DeviationOverlayProps) {
  const [phase, setPhase] = useState<Phase>('deviation');

  useEffect(() => {
    if (deviation) setPhase('deviation');
  }, [deviation]);

  useEffect(() => {
    if (recovery && phase !== 'resolved') {
      setPhase('resolved');
      const t = setTimeout(() => onDismiss?.(), 1400);
      return () => clearTimeout(t);
    }
  }, [recovery, phase, onDismiss]);

  useEffect(() => {
    if (deviation && !recovery) setPhase('recovering');
  }, [deviation, recovery]);

  if (!deviation) return null;

  return (
    <>
      <div className="pointer-events-none fixed inset-0 z-40 animate-[border-draw_300ms_ease-hive] border-2 border-crit" />
      <div className="fixed inset-0 z-40 flex items-center justify-center bg-bg-0/40 backdrop-blur-sm">
        <div className="w-[520px] animate-[overlay-in_400ms_ease-hive] rounded-lg border border-line-strong bg-bg-1 px-8 py-7 text-center">
          {phase !== 'resolved' ? (
            <>
              <div className="mb-5 text-[13px] font-semibold uppercase tracking-[0.2em] text-crit">
                World State Deviation
              </div>
              <div className="mb-2 flex justify-between text-[13px]">
                <span className="uppercase tracking-[0.1em] text-fg-2">Expected</span>
                <span className="text-fg-1">{deviation.expected}</span>
              </div>
              <div className="mb-2 flex justify-between text-[13px]">
                <span className="uppercase tracking-[0.1em] text-fg-2">Observed</span>
                <span className="text-crit">{deviation.observed}</span>
              </div>
              <div className="mb-6 flex justify-between text-[13px]">
                <span className="uppercase tracking-[0.1em] text-fg-2">Impact</span>
                <span className="text-fg-1">{deviation.message}</span>
              </div>
              <div className="relative overflow-hidden text-[13px] font-semibold uppercase tracking-[0.14em] text-think">
                ◈ Replanning Response
                <span className="absolute inset-0 -translate-x-full animate-[shimmer_1.6s_ease-hive_infinite] bg-gradient-to-r from-transparent via-think/20 to-transparent" />
              </div>
            </>
          ) : (
            <>
              <div className="mb-5 text-[13px] font-semibold uppercase tracking-[0.2em] text-ok">
                Response Replanned
              </div>
              <div className="text-[15px] text-fg-0">{recovery?.summary}</div>
            </>
          )}
        </div>
      </div>
      <style>{`
        @keyframes overlay-in { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes border-draw { from { opacity: 0; } to { opacity: 1; } }
        @keyframes shimmer { to { transform: translateX(100%); } }
      `}</style>
    </>
  );
}
