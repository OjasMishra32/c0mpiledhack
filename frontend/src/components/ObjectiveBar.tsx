import { useEffect, useState } from 'react';
import type { Goal } from '../types/hive';
import { PrimaryButton, ToolbarButton } from './primitives';

interface ScenarioSummary {
  id: string;
  title: string;
  subtitle: string;
  suggested_goal: string;
}

interface ObjectiveBarProps {
  goal: Goal | null;
  scenario: { id: string; title: string; suggested_goal: string } | null;
  scenarios: ScenarioSummary[];
  stats: Record<string, number | string> | null;
  objectCount: number;
  pending: { phrase: string; candidates: string[]; message: string } | null;
  executing: boolean;
  onScan: () => void;
  onCompile: (text: string) => void;
  onScenario: (id: string) => void;
  onBind: (objectId: string, role: string) => void;
}

const SOURCE_LABEL: Record<string, string> = {
  llm: 'AI planner',
  template: 'Template',
  demo_script: 'Known-good',
  manual: 'Manual',
};

export function ObjectiveBar({
  goal,
  scenario,
  scenarios,
  stats,
  objectCount,
  pending,
  executing,
  onScan,
  onCompile,
  onScenario,
  onBind,
}: ObjectiveBarProps) {
  const [draft, setDraft] = useState('');
  const [touched, setTouched] = useState(false);

  // Prefill with the objective HIVE proposes for what it can currently see, but never
  // overwrite something the operator has started typing.
  useEffect(() => {
    if (!touched && scenario?.suggested_goal) setDraft(scenario.suggested_goal);
  }, [scenario?.suggested_goal, touched]);

  const compiled = Boolean(goal);

  return (
    <div className="flex shrink-0 flex-col border-b border-separator bg-surface-primary">
      {/* Same 36px control height as the toolbar above it, same 16px gutters. */}
      <div className="flex items-center gap-2 px-4 py-2.5">
        <input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setTouched(true);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && draft.trim()) onCompile(draft.trim());
          }}
          placeholder="Enter an operational objective…"
          aria-label="Operational objective"
          spellCheck={false}
          className="h-9 min-w-0 flex-1 rounded-control bg-surface-secondary px-3 text-[15px] leading-none text-text-primary placeholder:text-text-tertiary"
        />

        <span className="hidden shrink-0 px-1 text-[11px] font-semibold uppercase tabular-nums tracking-[0.14em] text-text-tertiary sm:inline">
          {objectCount} item{objectCount === 1 ? '' : 's'} detected
        </span>

        <ToolbarButton onClick={onScan}>Scan scene</ToolbarButton>

        {/* The one filled accent control in the header — compiling is the act the
            whole screen is waiting on. */}
        <PrimaryButton
          onClick={() => draft.trim() && onCompile(draft.trim())}
          disabled={executing || !draft.trim()}
        >
          Compile
        </PrimaryButton>

        <select
          value={scenario?.id ?? ''}
          onChange={(e) => onScenario(e.target.value)}
          aria-label="Scenario"
          className="h-9 shrink-0 rounded-control bg-surface-secondary px-3 text-[13px] text-text-secondary"
        >
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title}
            </option>
          ))}
        </select>
      </div>

      {compiled && stats && (
        <div className="flex items-center gap-3 px-4 pb-2.5 text-[13px] text-text-secondary">
          <span className="rounded-control border border-separator-strong px-2 py-1 text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-text-secondary">
            {SOURCE_LABEL[goal!.plan_source] ?? goal!.plan_source}
          </span>
          <span className="tabular-nums">
            {stats.actions ?? stats.action_count} actions · {stats.parallel_peak} executable in
            parallel · {stats.depth ?? stats.layer_count} stages
          </span>
        </div>
      )}

      {pending && (
        <div className="flex items-center gap-2 border-t border-separator bg-surface-secondary px-4 py-2.5">
          <span className="mr-1 text-[13px] text-text-primary">{pending.message}</span>
          {pending.candidates.map((id) => (
            <button
              key={id}
              onClick={() => onBind(id, pending.phrase)}
              className="h-8 shrink-0 rounded-control border border-separator-strong px-3 text-[13px] font-medium text-text-secondary transition-colors duration-150 ease-standard hover:border-information hover:text-text-primary"
            >
              {id}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
