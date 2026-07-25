import { useState } from 'react';
import { DeviationBanner } from '../components/DeviationBanner';
import { Inspector, type Selection } from '../components/Inspector';
import { MetricsPanel } from '../components/MetricsPanel';
import { Sidebar } from '../components/Sidebar';
import { TaskGraph } from '../components/TaskGraph';
import { Timeline } from '../components/Timeline';
import { Toolbar } from '../components/Toolbar';
import { WorldView, type ActivePath } from '../components/WorldView';
import { dummyActions, dummyEvents, dummyObjects, dummyWorkers, dummyZones } from '../lib/dummyData';

type DeviationPhase = 'detected' | 'evaluating' | 'reassigning' | 'resumed';

const DUMMY_DEVIATION = {
  expected: 'Handheld scanner · Pack Station',
  observed: 'Handheld scanner · Pick Aisle A',
  message: 'Handheld scanner is outside the planned route. Packing workflow blocked.',
  action_ids: ['a3'],
};

const DUMMY_RECOVERY = { summary: 'Reassigning retrieval to Delta. Picking and restock continued uninterrupted.' };

export function Host() {
  const [selection, setSelection] = useState<Selection>(null);
  const [graphOpen, setGraphOpen] = useState(false);
  const [showMetrics] = useState(false);
  const [deviationPhase, setDeviationPhase] = useState<DeviationPhase | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);

  function triggerDeviation() {
    setDeviationPhase('detected');
    setTimeout(() => setDeviationPhase('evaluating'), 1200);
    setTimeout(() => setDeviationPhase('reassigning'), 2400);
    setTimeout(() => setDeviationPhase('resumed'), 3600);
    setTimeout(() => setDeviationPhase(null), 6000);
  }

  const goal = {
    id: 'goal_1',
    raw_text: 'Fulfill expedited order 4471 at the pack station and restock Pick Aisle B.',
    normalized_intent: 'fulfill_order',
    status: 'executing' as const,
    success_predicates: [],
    plan_source: 'llm' as const,
    planner_notes: '5 actions, 2 parallelizable, 1 resource conflict identified.',
    created_at: new Date().toISOString(),
  };

  const connectedWorkers = dummyWorkers.filter((w) => w.connected).length;

  const activePaths: ActivePath[] = dummyActions
    .filter((a) => (a.status === 'executing' || a.status === 'dispatched') && a.object_id && a.target_zone)
    .map((a) => {
      const zone = dummyZones.find((z) => z.id === a.target_zone);
      const worker = dummyWorkers.find((w) => w.id === a.assigned_worker_id);
      return {
        id: a.object_id!,
        from: [0, 0] as [number, number],
        to: zone ? [zone.bounds.x + zone.bounds.w / 2, zone.bounds.y + zone.bounds.h / 2] : [0.5, 0.5],
        color: worker?.color,
      };
    });

  function selectionFromWorker(id: string | null): void {
    setSelection(id ? { kind: 'worker', id } : null);
  }

  return (
    <div className="flex h-screen w-screen flex-col bg-background">
      <Toolbar
        goal={goal}
        connectedWorkers={connectedWorkers}
        totalWorkers={dummyWorkers.length}
        mode="live"
        executing
        onOpenGraph={() => setGraphOpen((v) => !v)}
        onOpenInspector={() => setInspectorOpen((v) => !v)}
      />

      <div className="grid min-h-0 flex-1 grid-cols-[240px_1fr] xl:grid-cols-[280px_1fr_320px]">
        <Sidebar
          goal={goal}
          actions={dummyActions}
          workers={dummyWorkers}
          selectedWorkerId={selection?.kind === 'worker' ? selection.id : null}
          onSelectWorker={selectionFromWorker}
        />

        <div className="relative min-h-0 border-r border-separator">
          <WorldView
            objects={dummyObjects}
            zones={dummyZones}
            activePaths={activePaths}
            selectedObjectId={selection?.kind === 'object' ? selection.id : null}
            onSelectObject={(id) => setSelection(id ? { kind: 'object', id } : null)}
            selectedZoneId={selection?.kind === 'zone' ? selection.id : null}
            onSelectZone={(id) => setSelection(id ? { kind: 'zone', id } : null)}
          />

          {deviationPhase && (
            <DeviationBanner deviation={DUMMY_DEVIATION} phase={deviationPhase} recovery={deviationPhase === 'resumed' ? DUMMY_RECOVERY : null} />
          )}

          {graphOpen && (
            <div className="absolute inset-0 z-10 bg-background/98">
              <TaskGraph
                actions={dummyActions}
                objects={dummyObjects}
                workers={dummyWorkers}
                selectedActionId={selection?.kind === 'action' ? selection.id : null}
                onSelectAction={(id) => setSelection(id ? { kind: 'action', id } : null)}
                onClose={() => setGraphOpen(false)}
              />
            </div>
          )}

          {showMetrics && (
            <MetricsPanel
              metrics={[
                { value: 3, label: 'Zones restored' },
                { value: 5, label: 'Responders coordinated' },
                { value: 4, label: 'Parallel peak' },
                { value: 1, label: 'Deviation detected' },
                { value: 1, label: 'Recovery completed' },
                { value: 0, label: 'Conflicts' },
              ]}
              totalTime="1:34"
              meanConfidence={0.87}
              idleReduction={0.41}
            />
          )}
        </div>

        {inspectorOpen && (
          <button
            aria-label="Close inspector"
            onClick={() => setInspectorOpen(false)}
            className="fixed inset-0 z-20 bg-background/60 xl:hidden"
          />
        )}
        <div
          className={`fixed inset-y-14 right-0 z-30 w-80 transition-transform duration-200 ease-standard xl:static xl:inset-auto xl:z-auto xl:translate-x-0 ${
            inspectorOpen ? 'translate-x-0' : 'translate-x-full'
          }`}
        >
          <Inspector
            selection={selection}
            goal={goal}
            actions={dummyActions}
            workers={dummyWorkers}
            objects={dummyObjects}
            zones={dummyZones}
            onSend={(t) => (t === 'host_inject_failure' ? triggerDeviation() : console.log('send', t))}
          />
        </div>
      </div>

      <Timeline events={dummyEvents} />
    </div>
  );
}
