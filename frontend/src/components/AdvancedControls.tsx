import { useState } from 'react';

interface ControlGroup {
  label: string;
  controls: { label: string; message: string }[];
}

const GROUPS: ControlGroup[] = [
  {
    label: 'Verification',
    controls: [
      { label: 'Force verify', message: 'host_manual_verify' },
      { label: 'Force fail', message: 'host_manual_verify' },
      { label: 'Skip action', message: 'host_skip_action' },
      { label: 'Replay instruction', message: 'host_replay_instruction' },
    ],
  },
  {
    label: 'World',
    controls: [
      { label: 'Set object zone', message: 'host_update_object' },
      { label: 'Move object', message: 'host_update_object' },
      { label: 'Remove object', message: 'host_inject_failure' },
      { label: 'Restore object', message: 'host_update_object' },
    ],
  },
  {
    label: 'Responders',
    controls: [
      { label: 'Set ready', message: 'host_set_worker' },
      { label: 'Set unavailable', message: 'host_set_worker' },
      { label: 'Reset worker', message: 'host_set_worker' },
      { label: 'Reassign action', message: 'host_reassign' },
    ],
  },
  {
    label: 'Failures',
    controls: [
      { label: 'Inject: wrong object', message: 'host_inject_failure' },
      { label: 'Inject: missing object', message: 'host_inject_failure' },
      { label: 'Inject: timeout', message: 'host_inject_failure' },
      { label: 'Inject: worker down', message: 'host_inject_failure' },
      { label: 'Inject: regress', message: 'host_inject_failure' },
    ],
  },
  {
    label: 'Plan',
    controls: [
      { label: 'Load known-good graph', message: 'host_reset' },
      { label: 'Override plan JSON', message: 'host_compile_goal' },
      { label: 'Recompile', message: 'host_compile_goal' },
      { label: 'Scripted recovery', message: 'host_inject_failure' },
    ],
  },
  {
    label: 'Mode',
    controls: [
      { label: 'Live', message: 'host_set_mode' },
      { label: 'Assisted', message: 'host_set_mode' },
      { label: 'Simulation', message: 'host_set_mode' },
      { label: 'Spawn simulated workers', message: 'host_set_mode' },
    ],
  },
];

interface AdvancedControlsProps {
  onSend?: (type: string) => void;
}

export function AdvancedControls({ onSend }: AdvancedControlsProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="px-0 py-1.5 text-[11px] uppercase tracking-[0.1em] text-fg-2 transition-colors duration-200 ease-hive hover:text-fg-1"
      >
        {open ? '▾' : '▸'} Advanced Controls
      </button>
      {open && (
        <div
          className="absolute bottom-full left-0 z-30 mb-2 flex w-[90vw] gap-6 overflow-x-auto rounded-md border border-line-strong bg-bg-1 px-4 py-3 shadow-2xl"
          style={{ height: 200 }}
        >
          {GROUPS.map((group) => (
            <div key={group.label} className="flex min-w-[160px] flex-col gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-fg-2">
                {group.label}
              </span>
              {group.controls.map((c) => (
                <button
                  key={c.label}
                  onClick={() => onSend?.(c.message)}
                  className="rounded-sm border border-line-strong px-2 py-1 text-left font-mono text-[11px] text-fg-1 transition-colors duration-200 ease-hive hover:border-info hover:text-info"
                >
                  {c.label}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
