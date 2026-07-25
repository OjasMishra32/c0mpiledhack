import { useState } from 'react';
import type { Action, ObservedObject, Worker, WorldMode, Zone } from '../types/hive';

/**
 * The rescue drawer.
 *
 * Every control here sends a documented host message and goes through the SAME code path
 * as the real thing — nothing is bespoke UI logic, so these cannot silently diverge from
 * the behaviour they exist to rescue. Collapsed by default and visually quiet: reachable
 * in one click, invisible from ten feet.
 */

interface AdvancedControlsProps {
  open: boolean;
  onToggle: () => void;
  workers: Worker[];
  objects: ObservedObject[];
  zones: Zone[];
  actions: Action[];
  mode: WorldMode;
  onSend: (type: string, payload?: Record<string, unknown>) => void;
}

const INJECTIONS: { label: string; kind: string }[] = [
  { label: 'Wrong item moved', kind: 'wrong_object_move' },
  { label: 'Item removed', kind: 'object_removed' },
  { label: 'Worker timeout', kind: 'worker_timeout' },
  { label: 'Verification regress', kind: 'verification_regress' },
  { label: 'Zone blocked', kind: 'zone_blocked' },
];

const MODES: WorldMode[] = ['live', 'assisted', 'simulation'];

export function AdvancedControls({
  open,
  onToggle,
  workers,
  objects,
  zones,
  actions,
  mode,
  onSend,
}: AdvancedControlsProps) {
  const [worker, setWorker] = useState('');
  const [object, setObject] = useState('');
  const [zone, setZone] = useState('');
  const [action, setAction] = useState('');

  const activeWorker = worker || workers[0]?.id || '';
  const activeObject = object || objects[0]?.id || '';
  const activeZone = zone || zones[0]?.id || '';
  const activeAction = action || actions.find((a) => a.status !== 'verified')?.id || '';
  const selectedWorker = workers.find((w) => w.id === activeWorker);

  if (!open) {
    return (
      <button
        onClick={onToggle}
        className="flex h-7 shrink-0 items-center gap-1.5 border-t border-separator bg-surface-primary px-4 text-[11px] uppercase tracking-wide text-text-tertiary transition-colors hover:text-text-secondary"
      >
        <span aria-hidden>▸</span> Advanced controls
      </button>
    );
  }

  return (
    <div className="flex shrink-0 flex-col gap-2 border-t border-separator bg-surface-primary px-4 py-2.5">
      <button
        onClick={onToggle}
        className="flex items-center gap-1.5 self-start text-[11px] uppercase tracking-wide text-text-tertiary hover:text-text-secondary"
      >
        <span aria-hidden>▾</span> Advanced controls
      </button>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Group label="Worker">
          <Picker value={activeWorker} onChange={setWorker}
            options={workers.map((w) => ({ value: w.id, label: w.callsign }))} />
          <Btn onClick={() => onSend('host_set_worker', { worker_id: activeWorker, available: false })} danger>
            Disable
          </Btn>
          <Btn onClick={() => onSend('host_set_worker', { worker_id: activeWorker, available: true })}>
            Enable
          </Btn>
          {selectedWorker && (
            <span className="text-[11px] text-text-tertiary">
              {selectedWorker.available ? 'available' : 'unavailable'}
            </span>
          )}
        </Group>

        <Group label="Item">
          <Picker value={activeObject} onChange={setObject}
            options={objects.map((o) => ({ value: o.id, label: o.role || o.semantic_label || o.id }))} />
          <Picker value={activeZone} onChange={setZone}
            options={zones.map((z) => ({ value: z.id, label: z.label }))} />
          <Btn onClick={() => onSend('host_update_object', { object_id: activeObject, zone: activeZone })}>
            Place
          </Btn>
        </Group>

        <Group label="Action">
          <Picker value={activeAction} onChange={setAction}
            options={actions.map((a) => ({ value: a.id, label: `${a.id} · ${a.status}` }))} />
          <Btn onClick={() => onSend('host_manual_verify', { action_id: activeAction, verified: true })}>
            Force verify
          </Btn>
          <Btn onClick={() => onSend('host_reassign', { action_id: activeAction })}>Reassign</Btn>
          <Btn onClick={() => onSend('host_skip_action', { action_id: activeAction })} danger>
            Skip
          </Btn>
        </Group>

        <Group label="Inject">
          {INJECTIONS.map((i) => (
            <Btn key={i.kind} danger onClick={() => onSend('host_inject_failure', { kind: i.kind })}>
              {i.label}
            </Btn>
          ))}
        </Group>

        <Group label="Mode">
          {MODES.map((m) => (
            <Btn key={m} active={mode === m} onClick={() => onSend('host_set_mode', { mode: m })}>
              {m}
            </Btn>
          ))}
          <Btn onClick={() => onSend('host_scan_scene')}>Rescan</Btn>
        </Group>
      </div>
    </div>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[11px] uppercase tracking-wide text-text-tertiary">{label}</span>
      {children}
    </div>
  );
}

function Btn({
  children,
  onClick,
  danger,
  active,
}: {
  children: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-[--r-control] border px-2 py-1 text-[12px] transition-colors ${
        active
          ? 'border-information text-text-primary'
          : danger
            ? 'border-separator text-failure hover:border-failure'
            : 'border-separator text-text-secondary hover:border-separator-strong hover:text-text-primary'
      }`}
    >
      {children}
    </button>
  );
}

function Picker({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="max-w-[140px] rounded-[--r-control] bg-surface-secondary px-1.5 py-1 text-[12px] text-text-secondary outline-none"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
