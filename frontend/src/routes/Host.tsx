import { useEffect, useMemo, useState } from 'react';
import { AdvancedControls } from '../components/AdvancedControls';
import { DeviationBanner } from '../components/DeviationBanner';
import { Inspector, type Selection } from '../components/Inspector';
import { MetricsPanel } from '../components/MetricsPanel';
import { ObjectiveBar } from '../components/ObjectiveBar';
import { Sidebar } from '../components/Sidebar';
import { TaskGraph } from '../components/TaskGraph';
import { Timeline } from '../components/Timeline';
import { Toolbar } from '../components/Toolbar';
import { WorldView, type ActivePath } from '../components/WorldView';
import { useHiveState } from '../hooks/useHiveState';

export function Host() {
  const { state, derived, connected, reconnecting, send, dismissFeed } = useHiveState();
  const [selection, setSelection] = useState<Selection>(null);
  const [graphOpen, setGraphOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const executing = state.execution_status === 'executing';
  const completed = state.execution_status === 'completed';

  // The graph is the proof the plan is real, so it opens itself the moment one exists —
  // and gets out of the way once execution is under way and the table matters more.
  useEffect(() => {
    if (state.actions.length && !executing && !completed) setGraphOpen(true);
  }, [state.actions.length, executing, completed]);
  useEffect(() => {
    if (executing) setGraphOpen(false);
  }, [executing]);

  const activePaths: ActivePath[] = useMemo(
    () =>
      derived.live
        .filter((a) => a.object_id && a.target_zone)
        .map((a) => {
          const zone = state.scene.zones.find((z) => z.id === a.target_zone);
          const obj = state.scene.objects.find((o) => o.id === a.object_id);
          const worker = state.workers.find((w) => w.id === a.assigned_worker_id);
          return {
            id: a.object_id!,
            from: [obj?.position.x ?? 0.5, obj?.position.y ?? 0.5] as [number, number],
            to: zone
              ? ([zone.bounds.x + zone.bounds.w / 2, zone.bounds.y + zone.bounds.h / 2] as [
                  number,
                  number,
                ])
              : ([0.5, 0.5] as [number, number]),
            color: worker?.color,
          };
        }),
    [derived.live, state.scene, state.workers],
  );

  const metrics = state.metrics;
  const completionStats = useMemo(
    () => [
      { value: derived.verified, label: 'Actions verified' },
      { value: derived.connectedWorkers, label: 'Workers coordinated' },
      { value: metrics?.parallel_peak ?? 0, label: 'Parallel peak' },
      { value: metrics?.deviations ?? 0, label: 'Deviations detected' },
      { value: metrics?.recoveries ?? 0, label: 'Recoveries completed' },
      { value: metrics?.conflicts ?? 0, label: 'Conflicts' },
    ],
    [derived, metrics],
  );

  return (
    <div className="flex h-screen w-screen flex-col bg-background">
      <Toolbar
        goal={state.goal}
        connectedWorkers={derived.connectedWorkers}
        totalWorkers={state.workers.length || 5}
        mode={state.world.mode ?? 'simulation'}
        executing={executing}
        connected={connected}
        reconnecting={reconnecting}
        onStart={() => send('host_start_execution')}
        onPause={() => send(executing ? 'host_pause_all' : 'host_resume_all')}
        onEmergencyStop={() => send('host_emergency_stop')}
        onReset={() => send('host_reset')}
        feedAnswer={state.feedAnswer}
        feedAvailable={state.world.camera_online ?? false}
        onAsk={(q) => send('host_ask_feed', { question: q })}
        onDismissFeed={dismissFeed}
        onOpenGraph={() => setGraphOpen((v) => !v)}
        onOpenInspector={() => setInspectorOpen((v) => !v)}
      />

      <ObjectiveBar
        goal={state.goal}
        scenario={state.scenario}
        scenarios={state.scenarios}
        stats={state.planStats}
        objectCount={state.scene.objects.length}
        pending={state.pending_grounding}
        executing={executing}
        onScan={() => send('host_scan_scene')}
        onCompile={(text) => send('host_compile_goal', { text })}
        onScenario={(id) => send('host_compile_goal', { scenario_id: id, text: '' })}
        onBind={(objectId, role) => send('host_bind_object', { object_id: objectId, role })}
      />

      <div className="grid min-h-0 flex-1 grid-cols-[240px_1fr] xl:grid-cols-[280px_1fr_320px]">
        <Sidebar
          goal={state.goal}
          actions={state.actions}
          workers={state.workers}
          contributions={state.contributions}
          zones={state.scene.zones}
          selectedWorkerId={selection?.kind === 'worker' ? selection.id : null}
          onSelectWorker={(id) => setSelection(id ? { kind: 'worker', id } : null)}
        />

        <div className="relative min-h-0 border-r border-separator">
          <WorldView
            objects={state.scene.objects}
            zones={state.scene.zones}
            activePaths={activePaths}
            selectedObjectId={selection?.kind === 'object' ? selection.id : null}
            onSelectObject={(id) => setSelection(id ? { kind: 'object', id } : null)}
            selectedZoneId={selection?.kind === 'zone' ? selection.id : null}
            onSelectZone={(id) => setSelection(id ? { kind: 'zone', id } : null)}
          />

          {state.deviation && (
            <DeviationBanner
              deviation={state.deviation}
              phase={state.recovery ? 'reassigning' : 'detected'}
              recovery={state.recovery ? { summary: state.recovery } : null}
            />
          )}

          {graphOpen && state.actions.length > 0 && (
            <div className="absolute inset-0 z-10 bg-background/98">
              <TaskGraph
                actions={state.actions}
                objects={state.scene.objects}
                workers={state.workers}
                selectedActionId={selection?.kind === 'action' ? selection.id : null}
                onSelectAction={(id) => setSelection(id ? { kind: 'action', id } : null)}
                onClose={() => setGraphOpen(false)}
              />
            </div>
          )}

          {completed && metrics && (
            <MetricsPanel
              metrics={completionStats}
              totalTime={formatSeconds(metrics.elapsed_seconds)}
              meanConfidence={metrics.avg_confidence ?? 0}
              idleReduction={0}
              label={state.scenario?.lexicon?.complete ?? 'Objective complete'}
              contributions={state.contributions}
              onReset={() => send('host_reset')}
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
            goal={state.goal}
            actions={state.actions}
            workers={state.workers}
            objects={state.scene.objects}
            zones={state.scene.zones}
            onSend={(type, payload) => send(type, payload)}
          />
        </div>
      </div>

      <AdvancedControls
        open={advancedOpen}
        onToggle={() => setAdvancedOpen((v) => !v)}
        workers={state.workers}
        objects={state.scene.objects}
        zones={state.scene.zones}
        actions={state.actions}
        mode={state.world.mode ?? 'simulation'}
        onSend={send}
      />

      <Timeline events={state.events} />
    </div>
  );
}

function formatSeconds(total?: number): string {
  const s = Math.max(0, Math.round(total ?? 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
