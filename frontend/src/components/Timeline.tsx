import { useState } from 'react';
import type { Event } from '../types/hive';
import { TimelineEvent } from './primitives';

export function Timeline({ events }: { events: Event[] }) {
  const [expanded, setExpanded] = useState(false);
  const sorted = [...events].sort((a, b) => b.seq - a.seq).slice(0, 120);

  return (
    <footer className={`relative flex flex-col border-t border-separator bg-surface-primary ${expanded ? 'h-48' : 'h-11'}`}>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="absolute right-3 top-1.5 z-10 text-[12px] text-text-tertiary hover:text-text-secondary"
      >
        {expanded ? 'Collapse' : 'Expand'}
      </button>

      {expanded ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto py-1">
          {sorted.map((e) => (
            <div key={e.id} className="border-b border-separator px-4 py-1.5">
              <TimelineEvent event={e} />
            </div>
          ))}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 items-stretch overflow-x-auto overflow-y-hidden">
          {sorted.map((e) => (
            <TimelineEvent key={e.id} event={e} />
          ))}
        </div>
      )}
    </footer>
  );
}
