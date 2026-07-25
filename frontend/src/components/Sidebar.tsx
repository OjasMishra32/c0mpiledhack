import type { Action, Goal, Worker, Zone } from '../types/hive';
import { Panel, Rule, SectionHeading, Stat, WorkerRow } from './primitives';

interface Contribution {
  worker_id: string;
  callsign: string;
  completed: number;
  reliability: number;
}

interface SidebarProps {
  goal: Goal | null;
  actions: Action[];
  workers: Worker[];
  contributions?: Contribution[];
  zones?: Zone[];
  selectedWorkerId: string | null;
  onSelectWorker: (id: string | null) => void;
}

export function Sidebar({ goal, actions, workers, contributions = [], zones = [], selectedWorkerId, onSelectWorker }: SidebarProps) {
  const byWorker = new Map(contributions.map((c) => [c.worker_id, c]));
  const verified = actions.filter((a) => a.status === 'verified').length;

  return (
    <Panel className="border-r border-separator" scroll>
      <div className="flex flex-col gap-5 px-4 py-4">
        <section>
          <SectionHeading>Objective</SectionHeading>
          {goal ? (
            <div className="mt-2 flex flex-col gap-1">
              <span className="text-[14px] leading-snug text-text-primary">{goal.raw_text}</span>
              {goal.planner_notes && (
                <span className="text-[12px] text-text-tertiary">{goal.planner_notes}</span>
              )}
            </div>
          ) : (
            <p className="mt-2 text-[13px] leading-relaxed text-text-tertiary">
              Scan the scene, then state an objective. HIVE binds it to the items it can
              actually see.
            </p>
          )}
        </section>

        {goal && actions.length > 0 && (
          <section className="flex flex-col gap-2">
            <div className="flex items-center gap-6">
              <Stat value={actions.length} label="Actions" />
              <Stat value={verified} label="Verified" />
            </div>
            <div className="h-0.5 w-full overflow-hidden rounded-full bg-surface-secondary">
              <div
                className="h-full bg-success transition-[width] duration-500 ease-standard"
                style={{ width: `${actions.length ? (verified / actions.length) * 100 : 0}%` }}
              />
            </div>
          </section>
        )}

        <Rule />

        <section>
          <SectionHeading>Workers</SectionHeading>
          <div className="mt-2 flex flex-col gap-0.5">
            {workers.map((w) => (
              <WorkerRow
                key={w.id}
                note={noteFor(byWorker.get(w.id))}
                worker={w}
                selected={w.id === selectedWorkerId}
                onSelect={() => onSelectWorker(w.id === selectedWorkerId ? null : w.id)}
                currentInstruction={
                  w.status === 'executing'
                    ? actions.find((a) => a.id === w.current_action_id)?.description
                    : undefined
                }
              />
            ))}
          </div>
        </section>

        {zones.length > 0 && (
          <>
            <Rule />
            <section>
              <SectionHeading>Areas</SectionHeading>
              <div className="mt-2 flex flex-col gap-1.5">
                {zones.map((z) => (
                  <div key={z.id} className="flex items-center justify-between">
                    <span className="text-[13px] text-text-secondary">{z.label}</span>
                    <div className="flex items-center gap-1">
                      {z.occupancy.map((oid) => (
                        <span key={oid} className="h-1.5 w-1.5 rounded-full bg-text-tertiary" />
                      ))}
                      {z.occupancy.length === 0 && (
                        <span className="text-[11px] text-text-tertiary">empty</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </Panel>
  );
}

/** One line of evidence per worker — what they have actually done this run. */
function noteFor(c?: { completed: number; reliability: number }): string | undefined {
  if (!c || !c.completed) return undefined;
  const base = `${c.completed} done`;
  return c.reliability < 1 ? `${base} · ${Math.round(c.reliability * 100)}% verified` : base;
}