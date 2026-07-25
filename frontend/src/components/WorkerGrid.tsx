import type { Worker } from '../types/hive';
import { Pulse } from './primitives';

const STATUS_LABEL: Record<Worker['status'], string> = {
  disconnected: 'OFFLINE', joining: 'JOINING', ready: 'READY', assigned: 'ASSIGNED',
  executing: 'EXECUTING', blocked: 'BLOCKED', paused: 'PAUSED',
  unavailable: 'UNAVAILABLE', emergency: 'EMERGENCY',
};

function statusColor(w: Worker): string {
  switch (w.status) {
    case 'executing': return w.color;
    case 'blocked': return 'var(--warn)';
    case 'unavailable': case 'emergency': return 'var(--crit)';
    case 'disconnected': return 'var(--fg-2)';
    default: return 'var(--fg-1)';
  }
}

function WorkerRow({ worker }: { worker: Worker }) {
  const dimmed = !worker.connected;
  const currentActionText = worker.status === 'executing'
    ? 'Move priority item to Pack'
    : worker.status === 'blocked'
      ? 'Awaiting cleared path'
      : null;

  return (
    <div className={`flex flex-col gap-1.5 border-b border-line px-4 py-3 ${dimmed ? 'opacity-40' : ''}`}>
      <div className="flex items-center gap-2">
        <span
          className={`inline-block h-2.5 w-2.5 rounded-full ${dimmed ? 'border border-dashed border-fg-2 bg-transparent' : ''}`}
          style={dimmed ? undefined : { backgroundColor: statusColor(worker) }}
        >
          {!dimmed && worker.status === 'executing' && (
            <Pulse color={statusColor(worker)} size={10} />
          )}
        </span>
        <span className={`text-[13px] font-semibold tracking-wide text-fg-0 ${worker.status === 'unavailable' ? 'line-through' : ''}`}>
          {worker.callsign}
        </span>
      </div>
      <div className="pl-[18px] text-[11px] font-medium uppercase tracking-[0.1em]" style={{ color: statusColor(worker) }}>
        {STATUS_LABEL[worker.status]}
      </div>
      {currentActionText && (
        <div className="pl-[18px] text-[13px] text-fg-1">{currentActionText}</div>
      )}
    </div>
  );
}

export function WorkerGrid({ workers }: { workers: Worker[] }) {
  return (
    <div>
      {workers.map((w) => (
        <WorkerRow key={w.id} worker={w} />
      ))}
    </div>
  );
}
