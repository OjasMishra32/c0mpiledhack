import { useState } from 'react';
import type { Goal, PlanSource } from '../types/hive';
import { Chip } from './primitives';

const SOURCE_CHIP: Record<PlanSource, { label: string; tone: 'think' | 'info' | 'default' }> = {
  llm: { label: 'AI PLANNER', tone: 'think' },
  template: { label: 'TEMPLATE', tone: 'info' },
  demo_script: { label: 'KNOWN-GOOD', tone: 'default' },
  manual: { label: 'MANUAL', tone: 'default' },
};

interface GoalBarProps {
  goal: Goal | null;
  stats?: { actions: number; parallel: number; conflicts: number };
  onCompile?: (text: string) => void;
}

export function GoalBar({ goal, stats, onCompile }: GoalBarProps) {
  const [text, setText] = useState('');

  if (goal) {
    const chip = SOURCE_CHIP[goal.plan_source];
    return (
      <div className="flex flex-col gap-2 bg-bg-1 px-4 py-3">
        <div className="text-[20px] font-medium text-fg-0">{goal.raw_text}</div>
        <div className="flex items-center gap-3">
          <Chip tone={chip.tone}>{chip.label}</Chip>
          {stats && (
            <span className="text-[13px] text-fg-2">
              {stats.actions} actions · {stats.parallel} parallel · {stats.conflicts} resource conflicts
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <form
      className="flex items-center bg-bg-1 px-4 py-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) onCompile?.(text.trim());
      }}
    >
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Enter operational objective…"
        className="w-full border-none bg-transparent text-[20px] text-fg-0 outline-none placeholder:text-fg-2"
      />
    </form>
  );
}
