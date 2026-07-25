import { useMemo, useState } from 'react';
import {
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  Handle,
  Position,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { Action, ActionStatus, ObservedObject, Worker } from '../types/hive';
import { StatusIndicator, ToolbarButton, type StatusTone } from './primitives';

interface ActionNodeData {
  action: Action;
  object: ObservedObject | undefined;
  worker: Worker | undefined;
  selected: boolean;
  [key: string]: unknown;
}

const STATUS_TONE: Record<ActionStatus, StatusTone> = {
  queued: 'neutral', available: 'neutral', assigned: 'neutral', dispatched: 'information',
  acknowledged: 'information', executing: 'accent', awaiting_verification: 'information',
  verified: 'success', failed: 'failure', blocked: 'failure', cancelled: 'neutral', recovery: 'accent',
};

function objectLabel(o: ObservedObject | undefined): string {
  if (!o) return '';
  return o.role ?? o.semantic_label ?? `${o.descriptor.color_name} ${o.descriptor.shape_hint} object`;
}

function ActionNode({ data }: NodeProps & { data: ActionNodeData }) {
  const { action, object, worker, selected } = data;
  const dim = action.status === 'queued';

  return (
    <div
      className={`flex w-[200px] flex-col gap-1.5 rounded-control border bg-surface-secondary px-2.5 py-2.5 transition-colors duration-150 ease-standard ${
        selected ? 'border-accent' : 'border-separator'
      }`}
      style={{ opacity: dim ? 0.5 : 1 }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <div className="flex items-center gap-1.5">
        <StatusIndicator tone={STATUS_TONE[action.status]} size={6} />
        <span className="truncate text-[12px] text-text-tertiary">{action.type.replace(/_/g, ' ')}</span>
      </div>
      <div className="truncate text-[13px] text-text-primary">{objectLabel(object) || action.description}</div>
      <div className="flex items-center justify-between text-[12px] text-text-tertiary">
        <span>{worker?.display_name ?? 'Unassigned'}</span>
        {action.status === 'verified' && <span>✓</span>}
      </div>
    </div>
  );
}

const nodeTypes = { action: ActionNode };

function topoLayers(actions: Action[]): string[][] {
  const byId = new Map(actions.map((a) => [a.id, a]));
  const depth = new Map<string, number>();

  function resolve(id: string, seen: Set<string>): number {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0;
    seen.add(id);
    const action = byId.get(id);
    if (!action || action.dependencies.length === 0) {
      depth.set(id, 0);
      return 0;
    }
    const d = 1 + Math.max(...action.dependencies.map((dep) => resolve(dep, seen)));
    depth.set(id, d);
    return d;
  }

  actions.forEach((a) => resolve(a.id, new Set()));

  const maxDepth = Math.max(0, ...Array.from(depth.values()));
  const cols: string[][] = Array.from({ length: maxDepth + 1 }, () => []);
  actions.forEach((a) => cols[depth.get(a.id) ?? 0].push(a.id));
  return cols;
}

/** current action, its immediate dependencies, next unlocked actions, and anything blocked */
function compactSubset(actions: Action[]): Action[] {
  const byId = new Map(actions.map((a) => [a.id, a]));
  const current = actions.filter((a) => a.status === 'executing' || a.status === 'dispatched');
  const deps = current.flatMap((a) => a.dependencies.map((id) => byId.get(id)).filter(Boolean) as Action[]);
  const unlocked = actions.filter((a) => a.status === 'available');
  const blocked = actions.filter((a) => a.status === 'blocked' || a.status === 'failed');
  const set = new Map<string, Action>();
  [...current, ...deps, ...unlocked, ...blocked].forEach((a) => set.set(a.id, a));
  return set.size ? Array.from(set.values()) : actions;
}

interface TaskGraphProps {
  actions: Action[];
  objects: ObservedObject[];
  workers: Worker[];
  selectedActionId?: string | null;
  onSelectAction?: (id: string | null) => void;
  onClose?: () => void;
}

export function TaskGraph({ actions, objects, workers, selectedActionId, onSelectAction, onClose }: TaskGraphProps) {
  const [fullPlan, setFullPlan] = useState(false);

  const visibleActions = useMemo(
    () => (fullPlan ? actions : compactSubset(actions)),
    [actions, fullPlan],
  );

  const { nodes, edges } = useMemo(() => {
    const byId = new Map(visibleActions.map((a) => [a.id, a]));
    const objectById = new Map(objects.map((o) => [o.id, o]));
    const workerById = new Map(workers.map((w) => [w.id, w]));
    const cols = topoLayers(visibleActions);

    const nodes: Node[] = cols.flatMap((col, x) =>
      col.map((id, y) => ({
        id,
        type: 'action',
        position: { x: x * 230, y: y * 104 - ((col.length - 1) * 104) / 2 },
        data: {
          action: byId.get(id)!,
          object: byId.get(id)?.object_id ? objectById.get(byId.get(id)!.object_id!) : undefined,
          worker: byId.get(id)?.assigned_worker_id ? workerById.get(byId.get(id)!.assigned_worker_id!) : undefined,
          selected: id === selectedActionId,
        } satisfies ActionNodeData,
        draggable: false,
      })),
    );

    const visibleIds = new Set(visibleActions.map((a) => a.id));
    const edges: Edge[] = visibleActions.flatMap((a) =>
      a.dependencies
        .filter((dep) => visibleIds.has(dep))
        .map((dep) => {
          const broken = byId.get(dep)?.status === 'failed';
          const active = a.status === 'executing' || a.status === 'available';
          return {
            id: `${dep}-${a.id}`,
            source: dep,
            target: a.id,
            style: {
              stroke: broken ? 'var(--failure)' : 'rgba(255,255,255,0.16)',
              strokeWidth: active ? 1.5 : 1,
            },
            type: 'smoothstep',
          };
        }),
    );

    return { nodes, edges };
  }, [visibleActions, objects, workers, selectedActionId]);

  return (
    <div className="relative h-full w-full bg-background">
      <div className="absolute left-3 top-3 z-10 flex items-center gap-2">
        <ToolbarButton onClick={() => setFullPlan((v) => !v)} className="bg-surface-elevated/90">
          {fullPlan ? 'Show current step' : 'Show full plan'}
        </ToolbarButton>
        {onClose && (
          <ToolbarButton onClick={onClose} className="bg-surface-elevated/90">Close</ToolbarButton>
        )}
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnScroll
        zoomOnScroll={false}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => onSelectAction?.(node.id === selectedActionId ? null : node.id)}
        onPaneClick={() => onSelectAction?.(null)}
      />
    </div>
  );
}
