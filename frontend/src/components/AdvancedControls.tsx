import { Rule, SectionHeading } from './primitives';

interface ControlGroup {
  label: string;
  danger?: boolean;
  controls: { label: string; message: string }[];
}

const GROUPS: ControlGroup[] = [
  {
    label: 'Worker overrides',
    controls: [
      { label: 'Set ready', message: 'host_set_worker' },
      { label: 'Set unavailable', message: 'host_set_worker' },
      { label: 'Reset worker', message: 'host_set_worker' },
      { label: 'Reassign action', message: 'host_reassign' },
    ],
  },
  {
    label: 'Object overrides',
    controls: [
      { label: 'Set object zone', message: 'host_update_object' },
      { label: 'Move object', message: 'host_update_object' },
      { label: 'Restore object', message: 'host_update_object' },
    ],
  },
  {
    label: 'Action overrides',
    controls: [
      { label: 'Force verify', message: 'host_manual_verify' },
      { label: 'Force fail', message: 'host_manual_verify' },
      { label: 'Skip action', message: 'host_skip_action' },
      { label: 'Replay instruction', message: 'host_replay_instruction' },
    ],
  },
  {
    label: 'Demo failure injection',
    danger: true,
    controls: [
      { label: 'Wrong object moved', message: 'host_inject_failure' },
      { label: 'Object removed', message: 'host_inject_failure' },
      { label: 'Worker timeout', message: 'host_inject_failure' },
      { label: 'Worker down', message: 'host_inject_failure' },
      { label: 'Verification regress', message: 'host_inject_failure' },
    ],
  },
  {
    label: 'Camera calibration',
    controls: [
      { label: 'Recalibrate workspace', message: 'host_recalibrate' },
      { label: 'Rescan scene', message: 'host_scan_scene' },
    ],
  },
  {
    label: 'System reset',
    danger: true,
    controls: [{ label: 'Reset to scenario', message: 'host_reset' }],
  },
];

interface AdvancedControlsProps {
  onSend?: (type: string) => void;
}

export function AdvancedControls({ onSend }: AdvancedControlsProps) {
  return (
    <div className="flex flex-col gap-4">
      {GROUPS.map((group, i) => (
        <div key={group.label}>
          {i > 0 && <Rule className="mb-4" />}
          <SectionHeading className="mb-1">{group.label}</SectionHeading>
          <div className="flex flex-col">
            {group.controls.map((c) => (
              <button
                key={c.label}
                onClick={() => onSend?.(c.message)}
                className={`flex items-center justify-between rounded-control px-2 py-1.5 text-left text-[13px] transition-colors duration-150 ease-standard hover:bg-surface-secondary ${
                  group.danger ? 'text-failure' : 'text-text-secondary'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
