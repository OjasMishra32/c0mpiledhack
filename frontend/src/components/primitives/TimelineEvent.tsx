import type { Event, Severity } from '../../types/hive';
import { StatusIndicator, type StatusTone } from './StatusIndicator';

const SEVERITY_TONE: Record<Severity, StatusTone> = {
  debug: 'neutral', info: 'information', warn: 'warning', critical: 'failure', success: 'success',
};

function formatTime(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '--:--:--';
  return d.toTimeString().slice(0, 8);
}

export function TimelineEvent({ event }: { event: Event }) {
  return (
    <div className="flex shrink-0 items-center gap-3 border-r border-separator px-4 py-2">
      <span className="font-technical text-[12px] tabular-nums text-text-tertiary">{formatTime(event.timestamp)}</span>
      <StatusIndicator tone={SEVERITY_TONE[event.severity]} />
      <span className="whitespace-nowrap text-[13px] text-text-secondary">{event.message}</span>
    </div>
  );
}
