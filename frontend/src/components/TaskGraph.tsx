import { useMemo } from 'react';
import {
  ReactFlow,
  Background,
  type Edge,
  type Node,
  type NodeProps,
  Handle,
  Position,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { Action, ActionStatus, ObservedObject, Worker } from '../types/hive';

interface ActionNodeData {
  action: Action;
  object: ObservedObject | undefined;
  worker: Worker | undefined;
  [key: string]: unknown;
}

const STATUS_BORDER: Record<ActionStatus, string> = {
  queued: 'var(--line)',
  available: 'var(--line-strong)',
  assigned: 'var(--line-strong)',
  dispatched: 'var(--line-strong)',
  acknowledged: 'var(--line-strong)',
  executing: 'var(--line-strong)',
  awaiting_verification: 'var(--info)',
  verified: 'var(--ok)',
  failed: 'var(--crit)',
  blocked: 'var(--crit)',
  cancelled: 'var(--line)',
  recovery: 'var(--think)',
};

function objectLabel(o: ObservedObject | undefined): string {
  if (!o) return '';
  return o.role ?? o.semantic_label ?? `${o.descriptor.color_name} ${o.descriptor.shape_hint} object`;
}

function ActionNode({ data }: NodeProps & { data: ActionNodeData }) {
  const { action, object, worker } = data;
  const borderColor = worker && (action.status === 'executing' || action.status === 'dispatched')
    ? worker.color
    : STATUS_BORDER[action.status];
  const dashed = action.status === 'awaiting_verification';
  const dim = action.status === 'queued';
  const settled = action.status === 'verified';

  return (
    <div
      className="flex h-16 w-[210px] flex-col justify-between rounded-md border bg-bg-2 px-2.5 py-2 transition-shadow duration-200 ease-hive"
      style={{
        borderColor,
        borderStyle: dashed ? 'dashed' : 'solid',
        opacity: dim ? 0.55 : settled ? 0.7 : 1,
        boxShadow: action.status === 'executing' ? `0 0 18px 0 ${borderColor}66` : 'none',
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.08em] text-fg-2">
        {worker && <span className="inline-block h-2 w-1 rounded-sm" style={{ backgroundColor: worker.color }} />}
        <span className="truncate">{action.type.replace(/_/g, ' ')}</span>
        {object && (
          <span className="ml-auto inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: object.descriptor.color_hex }} />
        )}
      </div>
      <div className="truncate text-[13px] text-fg-1">{objectLabel(object) || action.description}</div>
      <div className="flex items-center justify-between text-[11px] font-mono text-fg-2">
        <span>{worker?.callsign ?? '—'}</span>
        {action.status === 'verified' && <span className="text-ok">✓</span>}
        {typeof object?.confidence === 'number' && <span>{Math.round(object.confidence * 100)}%</span>}
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

interface TaskGraphProps {
  actions: Action[];
  objects: ObservedObject[];
  workers: Worker[];
}

export function TaskGraph({ actions, objects, workers }: TaskGraphProps) {
  const { nodes, edges } = useMemo(() => {
    const byId = new Map(actions.map((a) => [a.id, a]));
    const objectById = new Map(objects.map((o) => [o.id, o]));
    const workerById = new Map(workers.map((w) => [w.id, w]));
    const cols = topoLayers(actions);

    const nodes: Node[] = cols.flatMap((col, x) =>
      col.map((id, y) => ({
        id,
        type: 'action',
        position: { x: x * 240, y: y * 96 - ((col.length - 1) * 96) / 2 },
        data: {
          action: byId.get(id)!,
          object: byId.get(id)?.object_id ? objectById.get(byId.get(id)!.object_id!) : undefined,
          worker: byId.get(id)?.assigned_worker_id ? workerById.get(byId.get(id)!.assigned_worker_id!) : undefined,
        } satisfies ActionNodeData,
        draggable: false,
      })),
    );

    const edges: Edge[] = actions.flatMap((a) =>
      a.dependencies.map((dep) => ({
        id: `${dep}-${a.id}`,
        source: dep,
        target: a.id,
        style: { stroke: 'var(--line-strong)', strokeWidth: 1 },
        type: 'smoothstep',
      })),
    );

    return { nodes, edges };
  }, [actions, objects, workers]);

  return (
    <div className="h-full w-full bg-bg-0">
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
      >
        <Background color="var(--line)" gap={24} size={1} />
      </ReactFlow>
    </div>
  );
}
