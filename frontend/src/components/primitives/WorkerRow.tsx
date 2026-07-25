import type { Worker } from '../../types/hive';
import { StatusIndicator, type StatusTone } from './StatusIndicator';

const STATUS_TEXT: Record<Worker['status'], string> = {
  disconnected: 'Offline', joining: 'Joining', ready: 'Stand by', assigned: 'Assigned',
  executing: 'Executing', blocked: 'Blocked', paused: 'Paused',
  unavailable: 'Unavailable', emergency: 'Emergency',
};

function statusTone(status: Worker['status']): StatusTone {
  switch (status) {
    case 'executing': return 'accent';
    case 'blocked': return 'warning';
    case 'unavailable': case 'emergency': return 'failure';
    case 'disconnected': return 'neutral';
    default: return 'neutral';
  }
}

interface WorkerRowProps {
  worker: Worker;
  currentInstruction?: string;
  /** What this person has actually done this run — evidence, not decoration. */
  note?: string;
  selected?: boolean;
  onSelect?: () => void;
}

export function WorkerRow({ worker, currentInstruction, note, selected = false, onSelect }: WorkerRowProps) {
  const dimmed = !worker.connected;
  const name = worker.callsign || worker.display_name;

  return (
    <button
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${name} — ${STATUS_TEXT[worker.status]}`}
      className={`flex w-full flex-col gap-1.5 rounded-control px-2 py-2.5 text-left transition-colors duration-150 ease-standard ${
        selected ? 'bg-surface-secondary' : 'hover:bg-[rgb(var(--surface-secondary-rgb)/0.6)]'
      } ${dimmed ? 'opacity-40' : ''}`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="h-3 w-1 shrink-0 rounded-full" style={{ backgroundColor: worker.color }} />
          <span
            className={`truncate text-[14px] font-medium leading-none text-text-primary ${
              worker.status === 'unavailable' ? 'line-through' : ''
            }`}
          >
            {name}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <StatusIndicator tone={statusTone(worker.status)} outline={dimmed} />
          <span className="text-[11px] font-semibold uppercase leading-none tracking-[0.1em] text-text-tertiary">
            {STATUS_TEXT[worker.status]}
          </span>
        </div>
      </div>
      <span className="truncate pl-3 text-[13px] leading-snug text-text-secondary">
        {currentInstruction ?? 'Stand by'}
      </span>
      {note && <span className="truncate pl-3 text-[11px] leading-none text-text-tertiary">{note}</span>}
    </button>
  );
}
