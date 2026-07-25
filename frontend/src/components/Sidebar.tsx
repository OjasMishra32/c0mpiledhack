import type { Action, Goal, Worker, Zone } from '../types/hive';
import { Panel, Rule, SectionLabel, Stat, StatusIndicator, WorkerRow } from './primitives';

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
      {/* One vertical rhythm the whole column obeys: 24px between sections, and an
          optical 12px between a label and what it labels — which means a smaller
          margin where the rows underneath carry their own padding. */}
      <div className="flex flex-col gap-6 px-4 py-5">
        <section>
          <SectionLabel>Objective</SectionLabel>
          {goal ? (
            <div className="mt-3 flex flex-col gap-2">
              <span className="text-[15px] leading-snug text-text-primary">{goal.raw_text}</span>
              {goal.planner_notes && (
                <span className="text-[13px] leading-relaxed text-text-tertiary">
                  {goal.planner_notes}
                </span>
              )}
            </div>
          ) : (
            <p className="mt-3 text-[13px] leading-relaxed text-text-tertiary">
              Scan the scene, then state an objective. HIVE binds it to the items it can
              actually see.
            </p>
          )}
        </section>

        {goal && actions.length > 0 && (
          <section>
            <div className="flex items-start gap-8">
              <Stat value={actions.length} label="Actions" />
              <Stat value={verified} label="Verified" />
            </div>
            <div
              className="mt-4 h-1 w-full overflow-hidden rounded-control bg-surface-elevated"
              role="progressbar"
              aria-label="Actions verified"
              aria-valuenow={verified}
              aria-valuemin={0}
              aria-valuemax={actions.length}
            >
              <div
                className="h-full bg-success transition-[width] duration-500 ease-standard"
                style={{ width: `${actions.length ? (verified / actions.length) * 100 : 0}%` }}
              />
            </div>
          </section>
        )}

        <Rule />

        <section>
          <SectionLabel>Workers</SectionLabel>
          {/* Rows bleed 8px past the column padding so their hover fill looks
              deliberate while the text stays on the same left edge as the labels. */}
          <div className="-mx-2 mt-1 flex flex-col gap-0.5">
            {workers.map((w) => (
              <WorkerRow
                key={w.id}
                note={noteFor(byWorker.get(w.id))}
                worker={w}
                selected={w.id === selectedWorkerId}
                onSelect={() => onSelectWorker(w.id === selectedWorkerId ? null : w.id)}
                currentInstruction={instructionFor(w, actions)}
              />
            ))}
          </div>
        </section>

        {actions.length > 0 && (
          <>
            <Rule />
            <section>
              <SectionLabel>Tasks</SectionLabel>
              <div className="mt-2 flex flex-col gap-1">
                {actions.map((a) => {
                  const w = workers.find((x) => x.id === a.assigned_worker_id);
                  const done = a.status === 'verified';
                  const live = ['dispatched', 'acknowledged', 'executing'].includes(a.status);
                  return (
                    <div key={a.id} className="flex items-start gap-2">
                      <span
                        className="mt-1 h-1.5 w-1.5 shrink-0 rounded-control"
                        style={{ backgroundColor: done ? 'var(--success)' : live && w ? w.color : 'var(--text-tertiary)' }}
                      />
                      <span className={`flex-1 text-[13px] leading-snug ${done ? 'text-text-tertiary line-through' : 'text-text-secondary'}`}>
                        {a.description}
                      </span>
                      {w && <span className="shrink-0 text-[11px] text-text-tertiary">{w.callsign}</span>}
                    </div>
                  );
                })}
              </div>
            </section>
          </>
        )}

        {zones.length > 0 && (
          <>
            <Rule />
            <section>
              <SectionLabel>Areas</SectionLabel>
              <div className="mt-2 flex flex-col">
                {zones.map((z) => (
                  <div key={z.id} className="flex h-7 items-center justify-between gap-3">
                    <span className="truncate text-[13px] text-text-secondary">{z.label}</span>
                    <div className="flex shrink-0 items-center gap-1">
                      {z.occupancy.map((oid) => (
                        <StatusIndicator key={oid} size={6} />
                      ))}
                      {z.occupancy.length === 0 && (
                        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-tertiary">
                          Empty
                        </span>
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

/** The exact words on that person's phone, for any status where they hold the action.
 *  The host is the commander and may see all five; the phones still see only their own. */
function instructionFor(w: Worker, actions: Action[]): string | undefined {
  if (!w.current_action_id) return undefined;
  const a = actions.find((x) => x.id === w.current_action_id);
  if (!a) return undefined;
  return a.instruction?.display_text ?? a.description;
}
