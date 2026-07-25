import { useState } from 'react';
import type { Action, Goal, Worker } from '../types/hive';
import { Panel, Rule, SectionHeading, Stat, WorkerRow } from './primitives';

interface SidebarProps {
  goal: Goal | null;
  actions: Action[];
  workers: Worker[];
  selectedWorkerId: string | null;
  onSelectWorker: (id: string | null) => void;
  onCompile?: (text: string) => void;
}

export function Sidebar({ goal, actions, workers, selectedWorkerId, onSelectWorker, onCompile }: SidebarProps) {
  const [draft, setDraft] = useState('');
  const verified = actions.filter((a) => a.status === 'verified').length;

  return (
    <Panel className="border-r border-separator" scroll>
      <div className="flex flex-col gap-5 px-4 py-4">
        <section>
          <SectionHeading>Objective</SectionHeading>
          {goal ? (
            <div className="mt-2 flex flex-col gap-1">
              <span className="text-[14px] text-text-primary">{goal.raw_text}</span>
              <span className="text-[12px] text-text-tertiary">
                {actions.length} actions · {goal.plan_source === 'llm' ? 'AI planner' : goal.plan_source === 'template' ? 'Template' : 'Known-good plan'}
              </span>
            </div>
          ) : (
            <form
              className="mt-2 flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (draft.trim()) onCompile?.(draft.trim());
              }}
            >
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Describe the objective…"
                rows={3}
                className="resize-none rounded-control border border-separator-strong bg-surface-secondary px-3 py-2 text-[14px] text-text-primary outline-none placeholder:text-text-tertiary focus:border-information"
              />
              <button
                type="submit"
                disabled={!draft.trim()}
                className="self-start rounded-control bg-accent px-3 py-1.5 text-[13px] font-medium text-accent-ink disabled:opacity-40"
              >
                Compile plan
              </button>
            </form>
          )}
        </section>

        {goal && actions.length > 0 && (
          <section className="flex items-center gap-6">
            <Stat value={actions.length} label="Actions" />
            <Stat value={verified} label="Verified" />
          </section>
        )}

        <Rule />

        <section>
          <SectionHeading>Workers</SectionHeading>
          <div className="mt-2 flex flex-col gap-0.5">
            {workers.map((w) => (
              <WorkerRow
                key={w.id}
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
      </div>
    </Panel>
  );
}
