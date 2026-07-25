import { useEffect, useState } from 'react';
import type { Goal } from '../types/hive';

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
    <div className="flex flex-col border-b border-separator bg-surface-primary">
      <div className="flex items-center gap-3 px-4 py-2.5">
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
          spellCheck={false}
          className="min-w-0 flex-1 bg-transparent text-[15px] text-text-primary outline-none placeholder:text-text-tertiary"
        />

        <span className="hidden shrink-0 text-[12px] text-text-tertiary sm:inline">
          {objectCount} item{objectCount === 1 ? '' : 's'} detected
        </span>

        <button
          onClick={onScan}
          className="shrink-0 rounded-[--r-control] px-2.5 py-1.5 text-[13px] text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
        >
          Scan scene
        </button>

        <button
          onClick={() => draft.trim() && onCompile(draft.trim())}
          disabled={executing || !draft.trim()}
          className="shrink-0 rounded-[--r-control] bg-hive-accent px-3 py-1.5 text-[13px] font-medium text-hive-accent-ink transition-opacity disabled:opacity-30"
        >
          Compile
        </button>

        <select
          value={scenario?.id ?? ''}
          onChange={(e) => onScenario(e.target.value)}
          aria-label="Scenario"
          className="shrink-0 rounded-[--r-control] bg-surface-secondary px-2 py-1.5 text-[12px] text-text-secondary outline-none"
        >
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title}
            </option>
          ))}
        </select>
      </div>

      {compiled && stats && (
        <div className="flex items-center gap-2 px-4 pb-2 text-[12px] text-text-tertiary">
          <span className="rounded-full border border-separator-strong px-2 py-0.5 text-[11px] uppercase tracking-wide text-text-secondary">
            {SOURCE_LABEL[goal!.plan_source] ?? goal!.plan_source}
          </span>
          <span>
            {stats.actions ?? stats.action_count} actions · {stats.parallel_peak} executable in
            parallel · {stats.depth ?? stats.layer_count} stages
          </span>
        </div>
      )}

      {pending && (
        <div className="flex items-center gap-2 border-t border-separator bg-surface-secondary px-4 py-2 text-[13px]">
          <span className="text-text-primary">{pending.message}</span>
          {pending.candidates.map((id) => (
            <button
              key={id}
              onClick={() => onBind(id, pending.phrase)}
              className="rounded-[--r-control] border border-separator-strong px-2 py-1 text-[12px] text-text-secondary transition-colors hover:border-information hover:text-text-primary"
            >
              {id}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
