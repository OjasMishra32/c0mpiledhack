import type { DeviationDetectedPayload, RecoveryCompletedPayload } from '../types/hive';
import { StatusIndicator } from './primitives';

type Phase = 'detected' | 'evaluating' | 'reassigning' | 'resumed';

interface DeviationBannerProps {
  deviation: DeviationDetectedPayload;
  phase: Phase;
  recovery: RecoveryCompletedPayload | null;
}

const PHASE_LABEL: Record<Phase, string> = {
  detected: 'Deviation detected',
  evaluating: 'Evaluating constraints',
  reassigning: 'Reassigning',
  resumed: 'Execution resumed',
};

/**
 * A restrained, integrated status line — not a modal. The spatial workspace keeps
 * updating behind it; nothing about it should stop the operator from reading the scene.
 */
export function DeviationBanner({ deviation, phase, recovery }: DeviationBannerProps) {
  return (
    <div className="absolute left-1/2 top-3 z-10 flex -translate-x-1/2 items-center gap-3 rounded-control border border-separator-strong bg-surface-elevated/95 px-4 py-2 shadow-sm">
      <StatusIndicator tone={phase === 'resumed' ? 'success' : 'failure'} />
      <span className="text-[13px] font-medium text-text-primary">{PHASE_LABEL[phase]}</span>
      <span className="text-[13px] text-text-secondary">
        {phase === 'resumed' && recovery ? recovery.summary : deviation.message}
      </span>
    </div>
  );
}
