import type { Event, Severity } from '../types/hive';

const SEVERITY_COLOR: Record<Severity, string> = {
  debug: 'var(--fg-2)',
  info: 'var(--info)',
  warn: 'var(--warn)',
  critical: 'var(--crit)',
  success: 'var(--ok)',
};

function formatTime(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '--:--:--';
  return d.toTimeString().slice(0, 8);
}

function EventRow({ event }: { event: Event }) {
  const flash = event.severity === 'critical';
  return (
    <div
      className="flex items-start gap-3 border-l-[3px] py-1.5 pl-3 pr-4"
      style={{
        borderColor: SEVERITY_COLOR[event.severity],
        animation: flash ? 'evt-flash 900ms ease-out' : undefined,
      }}
    >
      <span className="font-mono text-[11px] text-fg-2">{formatTime(event.timestamp)}</span>
      <span className="text-[13px] text-fg-1">{event.message}</span>
    </div>
  );
}

export function EventTimeline({ events }: { events: Event[] }) {
  const sorted = [...events].sort((a, b) => b.seq - a.seq).slice(0, 120);

  return (
    <div className="flex flex-col">
      <style>{`
        @keyframes evt-flash {
          0% { background-color: rgb(255 55 95 / 0.12); }
          100% { background-color: transparent; }
        }
      `}</style>
      {sorted.map((e) => (
        <EventRow key={e.id} event={e} />
      ))}
    </div>
  );
}
