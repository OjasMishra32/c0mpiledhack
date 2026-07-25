import { useState } from 'react';
import type { Action, Goal, ObservedObject, Worker, Zone } from '../types/hive';
import { AdvancedControls } from './AdvancedControls';
import { EmptyState, InspectorRow, Panel, Rule, SectionHeading, StatusIndicator } from './primitives';

export type Selection =
  | { kind: 'worker'; id: string }
  | { kind: 'object'; id: string }
  | { kind: 'zone'; id: string }
  | { kind: 'action'; id: string }
  | null;

interface InspectorProps {
  selection: Selection;
  goal: Goal | null;
  actions: Action[];
  workers: Worker[];
  objects: ObservedObject[];
  zones: Zone[];
  onSend?: (type: string) => void;
}

function objectLabel(o: ObservedObject): string {
  return o.role ?? o.semantic_label ?? `${o.descriptor.color_name} ${o.descriptor.shape_hint} object`;
}

export function Inspector({ selection, goal, actions, workers, objects, zones, onSend }: InspectorProps) {
  const [tab, setTab] = useState<'context' | 'controls'>('context');

  return (
    <Panel className="border-l border-separator">
      <div className="flex items-center gap-1 border-b border-separator px-4 pb-2 pt-3.5">
        {(['context', 'controls'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-control px-2.5 py-1 text-[13px] font-medium transition-colors duration-150 ease-standard ${
              tab === t ? 'bg-surface-secondary text-text-primary' : 'text-text-tertiary hover:text-text-secondary'
            }`}
          >
            {t === 'context' ? 'Inspector' : 'Controls'}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {tab === 'controls' ? (
          <AdvancedControls onSend={onSend} />
        ) : (
          <InspectorContent selection={selection} goal={goal} actions={actions} workers={workers} objects={objects} zones={zones} />
        )}
      </div>
    </Panel>
  );
}

function InspectorContent({ selection, goal, actions, workers, objects, zones }: Omit<InspectorProps, 'onSend'>) {
  if (!selection) {
    if (!goal) {
      return <EmptyState title="Nothing selected" detail="Compile a plan to begin." />;
    }
    const executing = actions.find((a) => a.status === 'executing');
    const nextUp = actions.find((a) => a.status === 'available');
    const verifiedCount = actions.filter((a) => a.status === 'verified').length;
    const meanConfidence = objects.length
      ? objects.reduce((sum, o) => sum + o.confidence, 0) / objects.length
      : 0;

    return (
      <div className="flex flex-col gap-1">
        <SectionHeading>Execution</SectionHeading>
        <InspectorRow label="Phase" value={goal.status} />
        <InspectorRow label="Current action" value={executing?.description ?? '—'} />
        <InspectorRow label="Next dependency" value={nextUp?.description ?? '—'} />
        <InspectorRow label="Verified" value={`${verifiedCount} / ${actions.length}`} />
        <InspectorRow label="Mean confidence" value={`${Math.round(meanConfidence * 100)}%`} />
        <InspectorRow label="Plan source" value={goal.plan_source} />
        <Rule className="my-2" />
        <p className="text-[13px] leading-relaxed text-text-secondary">{goal.planner_notes}</p>
      </div>
    );
  }

  if (selection.kind === 'worker') {
    const worker = workers.find((w) => w.id === selection.id);
    if (!worker) return null;
    const action = actions.find((a) => a.id === worker.current_action_id);
    return (
      <div className="flex flex-col gap-1">
        <SectionHeading right={<StatusIndicator tone={worker.connected ? 'success' : 'neutral'} />}>
          {worker.display_name}
        </SectionHeading>
        <InspectorRow label="State" value={worker.status} />
        <InspectorRow label="Current action" value={action?.description ?? 'Stand by'} />
        <InspectorRow label="Reachable zones" value={worker.reachable_zones.join(', ') || '—'} />
        <InspectorRow label="Assignment reason" value={action?.assignment_reason ?? '—'} />
        <InspectorRow label="Confidence" value={`${Math.round(worker.confidence * 100)}%`} />
      </div>
    );
  }

  if (selection.kind === 'object') {
    const object = objects.find((o) => o.id === selection.id);
    if (!object) return null;
    const action = actions.find((a) => a.object_id === object.id && a.status !== 'verified' && a.status !== 'cancelled');
    return (
      <div className="flex flex-col gap-1">
        <SectionHeading right={<StatusIndicator color={object.descriptor.color_hex} />}>
          {objectLabel(object)}
        </SectionHeading>
        <InspectorRow label="Zone" value={object.zone} />
        <InspectorRow label="Confidence" value={`${Math.round(object.confidence * 100)}%`} />
        <InspectorRow label="Locked by" value={object.locked_by ?? '—'} />
        <InspectorRow label="Held by" value={object.held_by ?? '—'} />
        <InspectorRow label="Related action" value={action?.description ?? '—'} />
      </div>
    );
  }

  if (selection.kind === 'zone') {
    const zone = zones.find((z) => z.id === selection.id);
    if (!zone) return null;
    return (
      <div className="flex flex-col gap-1">
        <SectionHeading>{zone.label}</SectionHeading>
        <InspectorRow label="Status" value={zone.status} />
        <InspectorRow label="Occupants" value={zone.occupancy.length || '—'} />
        <InspectorRow label="Source" value={zone.source} />
      </div>
    );
  }

  const action = actions.find((a) => a.id === selection.id);
  if (!action) return null;
  return (
    <div className="flex flex-col gap-1">
      <SectionHeading>{action.description}</SectionHeading>
      <InspectorRow label="Status" value={action.status} />
      <InspectorRow label="Assigned worker" value={action.assigned_worker_id ?? '—'} />
      <InspectorRow label="Dependencies" value={action.dependencies.join(', ') || '—'} />
      <InspectorRow label="Retries" value={`${action.retry_count} / ${action.max_retries}`} />
    </div>
  );
}
