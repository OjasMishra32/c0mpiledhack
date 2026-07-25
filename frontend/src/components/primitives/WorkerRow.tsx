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
  selected?: boolean;
  onSelect?: () => void;
}

export function WorkerRow({ worker, currentInstruction, selected = false, onSelect }: WorkerRowProps) {
  const dimmed = !worker.connected;

  return (
    <button
      onClick={onSelect}
      className={`flex w-full flex-col gap-1 rounded-control px-3 py-2.5 text-left transition-colors duration-150 ease-standard ${
        selected ? 'bg-surface-secondary' : 'hover:bg-surface-secondary/60'
      } ${dimmed ? 'opacity-40' : ''}`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="h-2 w-1 rounded-full" style={{ backgroundColor: worker.color }} />
          <span className={`text-[14px] font-medium text-text-primary ${worker.status === 'unavailable' ? 'line-through' : ''}`}>
            {worker.display_name}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <StatusIndicator tone={statusTone(worker.status)} outline={dimmed} />
          <span className="text-[12px] text-text-tertiary">{STATUS_TEXT[worker.status]}</span>
        </div>
      </div>
      <span className="text-[13px] text-text-secondary">{currentInstruction ?? 'Stand by'}</span>
    </button>
  );
}
