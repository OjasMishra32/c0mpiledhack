import type { Goal, WorldMode } from '../types/hive';
import { PrimaryButton, DangerButton, ToolbarButton, StatusIndicator } from './primitives';

interface ToolbarProps {
  goal: Goal | null;
  connectedWorkers: number;
  totalWorkers: number;
  mode: WorldMode;
  executing: boolean;
  onStart?: () => void;
  onPause?: () => void;
  onEmergencyStop?: () => void;
  onOpenGraph?: () => void;
  onOpenInspector?: () => void;
}

const MODE_LABEL: Record<WorldMode, string> = { live: 'Live', assisted: 'Assisted', simulation: 'Simulation' };

export function Toolbar({
  goal, connectedWorkers, totalWorkers, mode, executing, onStart, onPause, onEmergencyStop, onOpenGraph, onOpenInspector,
}: ToolbarProps) {
  return (
    <header className="flex h-14 items-center gap-4 border-b border-separator bg-surface-primary px-4">
      <span className="text-[15px] font-semibold tracking-tight text-text-primary">HIVE</span>

      <div className="flex items-center gap-1.5 text-[13px] text-text-secondary">
        <StatusIndicator tone={connectedWorkers > 0 ? 'success' : 'neutral'} />
        <span>{connectedWorkers}/{totalWorkers} workers connected</span>
      </div>

      {goal && (
        <span className="max-w-[420px] truncate text-[14px] text-text-secondary" title={goal.raw_text}>
          {goal.raw_text}
        </span>
      )}

      <span className="text-[13px] text-text-tertiary">{MODE_LABEL[mode]}</span>

      <div className="ml-auto flex items-center gap-2">
        <ToolbarButton onClick={onOpenInspector} className="xl:hidden">Inspector</ToolbarButton>
        <ToolbarButton onClick={onOpenGraph}>Task graph</ToolbarButton>
        {executing ? (
          <ToolbarButton onClick={onPause}>Pause</ToolbarButton>
        ) : (
          <PrimaryButton onClick={onStart} disabled={!goal}>Start execution</PrimaryButton>
        )}
        <DangerButton onClick={onEmergencyStop}>Emergency stop</DangerButton>
      </div>
    </header>
  );
}
