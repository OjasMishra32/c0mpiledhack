import type { Goal, WorldMode } from '../types/hive';
import { AskFeed } from './AskFeed';
import { SecondaryButton, DangerButton, ToolbarButton, StatusIndicator } from './primitives';

interface ToolbarProps {
  goal: Goal | null;
  connectedWorkers: number;
  totalWorkers: number;
  mode: WorldMode;
  executing: boolean;
  connected?: boolean;
  reconnecting?: boolean;
  onStart?: () => void;
  onPause?: () => void;
  onReset?: () => void;
  onEmergencyStop?: () => void;
  feedAnswer?: { question: string; answer: string } | null;
  feedAvailable?: boolean;
  onAsk?: (question: string) => void;
  onDismissFeed?: () => void;
  onOpenGraph?: () => void;
  onOpenInspector?: () => void;
}

const MODE_LABEL: Record<WorldMode, string> = { live: 'Live', assisted: 'Assisted', simulation: 'Simulation' };

export function Toolbar({
  goal, connectedWorkers, totalWorkers, mode, executing, connected = true, reconnecting = false,
  onStart, onPause, onReset, onEmergencyStop, onOpenGraph, onOpenInspector,
  feedAnswer = null, feedAvailable = false, onAsk, onDismissFeed,
}: ToolbarProps) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-separator bg-surface-primary px-4">
      <span className="shrink-0 text-[15px] font-semibold uppercase tracking-[0.16em] text-text-primary">
        HIVE
      </span>

      <div className="flex shrink-0 items-center gap-2 text-[13px] text-text-secondary">
        <StatusIndicator tone={connectedWorkers > 0 ? 'success' : 'neutral'} />
        <span className="tabular-nums">
          {connectedWorkers}/{totalWorkers} workers connected
        </span>
      </div>

      {/* A dropped socket is a chip, never a modal — the demo keeps going. */}
      {!connected && (
        <span
          role="status"
          className="shrink-0 rounded-control border border-separator-strong px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-warning"
        >
          {reconnecting ? 'Reconnecting' : 'Offline'}
        </span>
      )}

      {/* The objective itself lives in the objective bar and the sidebar; echoing it
          here only ate the room the controls need. */}

      <span className="shrink-0 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-tertiary">
        {MODE_LABEL[mode]}
      </span>

      <div className="ml-auto flex items-center gap-2">
        {onAsk && (
          <AskFeed
            answer={feedAnswer}
            available={feedAvailable}
            onAsk={onAsk}
            onDismiss={() => onDismissFeed?.()}
          />
        )}
        <ToolbarButton onClick={onOpenInspector} className="xl:hidden">Inspector</ToolbarButton>
        <ToolbarButton onClick={onOpenGraph}>Task graph</ToolbarButton>
        {executing ? (
          <SecondaryButton onClick={onPause}>Pause</SecondaryButton>
        ) : (
          <SecondaryButton onClick={onStart} disabled={!goal}>Start execution</SecondaryButton>
        )}
        <ToolbarButton onClick={onReset}>Reset</ToolbarButton>
        <DangerButton onClick={onEmergencyStop}>Emergency stop</DangerButton>
      </div>
    </header>
  );
}
