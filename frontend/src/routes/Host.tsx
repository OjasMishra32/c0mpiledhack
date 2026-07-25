import { useState } from 'react';
import { AdvancedControls } from '../components/AdvancedControls';
import { EventTimeline } from '../components/EventTimeline';
import { GoalBar } from '../components/GoalBar';
import { MetricsPanel } from '../components/MetricsPanel';
import { TaskGraph } from '../components/TaskGraph';
import { WorkerGrid } from '../components/WorkerGrid';
import { WorldView } from '../components/WorldView';
import { ZonePanel } from '../components/ZonePanel';
import { Chip, Panel, Pulse, Rule } from '../components/primitives';
import { dummyActions, dummyEvents, dummyObjects, dummyWorkers, dummyZones } from '../lib/dummyData';

export function Host() {
  const [showMetrics] = useState(false);
  const connectedCount = dummyWorkers.filter((w) => w.connected).length;

  return (
    <div className="flex h-screen w-screen flex-col bg-line">
      {/* header */}
      <header className="flex h-14 items-center gap-4 bg-bg-0 px-4">
        <span className="text-[13px] font-semibold uppercase tracking-[0.2em] text-fg-0">HIVE</span>
        <div className="flex items-center gap-2">
          <Pulse color="var(--ok)" size={8} />
          <span className="text-[11px] uppercase tracking-[0.1em] text-fg-2">
            Collective Online · {connectedCount} Nodes
          </span>
        </div>
        <Chip tone="info" className="ml-auto">Mode: Live</Chip>
        <span className="font-mono text-[13px] text-fg-2">00:47</span>
      </header>

      {/* body — three columns, 1px hairline gap via bg-line background */}
      <div className="grid min-h-0 flex-1 grid-cols-[320px_1fr_380px] gap-px overflow-hidden">
        {/* left: workers + scene */}
        <div className="flex min-h-0 flex-col gap-px overflow-hidden bg-line">
          <Panel label="Workers" className="flex-none" scroll>
            <WorkerGrid workers={dummyWorkers} />
          </Panel>
          <Rule />
          <Panel className="min-h-0 flex-1" scroll>
            <ZonePanel objects={dummyObjects} zones={dummyZones} />
          </Panel>
        </div>

        {/* center: objective + graph + world view (or metrics on completion) */}
        <div className="flex min-h-0 flex-col gap-px overflow-hidden bg-line">
          {showMetrics ? (
            <MetricsPanel
              metrics={[
                { value: 3, label: 'Zones', sublabel: 'Restored' },
                { value: 5, label: 'Responders', sublabel: 'Coordinated' },
                { value: 4, label: 'Parallel', sublabel: 'Peak' },
                { value: 1, label: 'Deviation', sublabel: 'Detected' },
                { value: 1, label: 'Recovery', sublabel: 'Completed' },
                { value: 0, label: 'Conflicts', sublabel: '' },
              ]}
              totalTime="01:34"
              meanConfidence={0.87}
              idleReduction={0.41}
            />
          ) : (
            <>
              <div className="flex-none bg-bg-1">
                <GoalBar
                  goal={{
                    id: 'goal_1',
                    raw_text: 'Fulfill expedited order 4471 at the pack station and restock Pick Aisle B.',
                    normalized_intent: 'fulfill_order',
                    status: 'executing',
                    success_predicates: [],
                    plan_source: 'llm',
                    planner_notes: '',
                    created_at: new Date().toISOString(),
                  }}
                  stats={{ actions: dummyActions.length, parallel: 2, conflicts: 1 }}
                />
              </div>
              <div className="min-h-0 flex-[3] bg-bg-0">
                <TaskGraph actions={dummyActions} objects={dummyObjects} workers={dummyWorkers} />
              </div>
              <div className="min-h-0 flex-[2] bg-bg-0">
                <WorldView />
              </div>
            </>
          )}
        </div>

        {/* right: event timeline */}
        <Panel label="Event Timeline" className="min-h-0 overflow-hidden" scroll>
          <EventTimeline events={dummyEvents} />
        </Panel>
      </div>

      {/* footer: advanced controls + primary actions */}
      <footer className="flex h-16 items-center gap-3 bg-bg-1 px-4">
        <div className="flex-1">
          <AdvancedControls onSend={(t) => console.log('send', t)} />
        </div>
        <div className="flex items-center gap-2">
          {['Compile', 'Start', 'Pause', 'Reset'].map((label) => (
            <button
              key={label}
              className="rounded-sm border border-line-strong px-3 py-1.5 text-[11px] uppercase tracking-[0.1em] text-fg-1 transition-colors duration-200 ease-hive hover:border-info hover:text-info"
            >
              {label}
            </button>
          ))}
          <button className="rounded-sm border border-crit px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-crit transition-colors duration-200 ease-hive hover:bg-crit hover:text-bg-0">
            E-Stop
          </button>
        </div>
      </footer>
    </div>
  );
}
