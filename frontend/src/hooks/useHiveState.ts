/**
 * Live host state, accumulated from the socket.
 *
 * The server sends one full `state_snapshot` on connect and narrow deltas afterwards.
 * This hook folds those into a single immutable object the whole host tree renders from,
 * so no component ever has to know which message carried which field.
 *
 * Ordering is by `seq`, never by timestamp — clocks are not the ordering authority.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  Action,
  Event as HiveEvent,
  Goal,
  ObservedObject,
  RunMetrics,
  Scene,
  Worker,
  WorldState,
  Zone,
} from '../types/hive';
import { useHiveSocket } from './useHiveSocket';

export interface Deviation {
  expected: string;
  observed: string;
  message: string;
  action_ids: string[];
  at: number;
}

export interface Contribution {
  worker_id: string;
  callsign: string;
  completed: number;
  failed: number;
  reliability: number;
  mean_seconds: number | null;
  zones: Record<string, number>;
}

export interface HostState {
  execution_status: string;
  scenario: {
    id: string;
    title: string;
    subtitle: string;
    suggested_goal: string;
    lexicon: Record<string, string>;
    recommended_failure?: string;
  } | null;
  scenarios: { id: string; title: string; subtitle: string; suggested_goal: string }[];
  goal: Goal | null;
  actions: Action[];
  workers: Worker[];
  scene: Scene;
  world: WorldState;
  locks: Record<string, string>;
  metrics: RunMetrics | null;
  events: HiveEvent[];
  contributions: Contribution[];
  layers: string[][];
  planStats: Record<string, number | string> | null;
  deviation: Deviation | null;
  recovery: string | null;
  pending_grounding: { phrase: string; candidates: string[]; message: string } | null;
  feedAnswer: { question: string; answer: string } | null;
  demo_mode: boolean;
}

const EMPTY_SCENE: Scene = {
  objects: [] as ObservedObject[],
  zones: [] as Zone[],
  scanned_at: '',
  labeling_source: 'none',
  stable: true,
} as unknown as Scene;

const INITIAL: HostState = {
  execution_status: 'idle',
  scenario: null,
  scenarios: [],
  goal: null,
  actions: [],
  workers: [],
  scene: EMPTY_SCENE,
  world: { mode: 'simulation', camera_online: false } as WorldState,
  locks: {},
  metrics: null,
  events: [],
  contributions: [],
  layers: [],
  planStats: null,
  deviation: null,
  recovery: null,
  pending_grounding: null,
  feedAnswer: null,
  demo_mode: true,
};

const MAX_EVENTS = 200;

export function useHiveState() {
  const { connected, reconnecting, lastMessage, send } = useHiveSocket('host');
  const [state, setState] = useState<HostState>(INITIAL);
  const deviationTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!lastMessage) return;
    const { type, payload } = lastMessage as { type: string; payload: any };

    setState((prev) => {
      switch (type) {
        case 'state_snapshot':
          return {
            ...prev,
            ...payload,
            scene: payload.scene ?? prev.scene,
            world: payload.world ?? prev.world,
            events: (payload.events ?? []).slice(-MAX_EVENTS),
            contributions: payload.contributions ?? [],
          };

        case 'plan_compiled':
          return {
            ...prev,
            goal: payload.goal ?? prev.goal,
            actions: payload.actions ?? prev.actions,
            layers: payload.layers ?? [],
            planStats: payload.stats ?? null,
            pending_grounding: null,
          };

        case 'actions_changed':
          return {
            ...prev,
            actions: payload.actions ?? prev.actions,
            locks: payload.locks ?? prev.locks,
            metrics: payload.metrics ?? prev.metrics,
            execution_status: payload.execution_status ?? prev.execution_status,
          };

        case 'workers_changed':
          return { ...prev, workers: payload ?? prev.workers };

        case 'world_state_changed':
          return {
            ...prev,
            world: payload.world ?? prev.world,
            scene: payload.scene ?? prev.scene,
          };

        case 'scene_discovered':
          return { ...prev, scene: payload ?? prev.scene };

        case 'grounding_ambiguous':
          return { ...prev, pending_grounding: payload };

        case 'deviation_detected':
          return {
            ...prev,
            deviation: {
              expected: payload.expected || '—',
              observed: payload.observed || '—',
              message: payload.message ?? 'World state deviation detected.',
              action_ids: payload.action_ids ?? [],
              at: Date.now(),
            },
            recovery: null,
          };

        case 'recovery_started':
          return { ...prev, recovery: payload?.narration ?? payload?.message ?? null };

        case 'execution_started':
          return { ...prev, execution_status: 'executing' };
        case 'execution_paused':
          return { ...prev, execution_status: 'paused' };
        case 'execution_resumed':
          return { ...prev, execution_status: 'executing' };

        case 'goal_completed':
          return {
            ...prev,
            execution_status: 'completed',
            metrics: payload.report ?? prev.metrics,
            contributions: payload.contributions ?? prev.contributions,
          };

        case 'feed_answer':
          return { ...prev, feedAnswer: { question: payload.question, answer: payload.answer } };

        case 'event': {
          const ev = payload as HiveEvent;
          if (prev.events.some((e) => e.seq === ev.seq)) return prev;
          const events = [...prev.events, ev].slice(-MAX_EVENTS);
          return { ...prev, events };
        }

        default:
          return prev;
      }
    });
  }, [lastMessage]);

  // The deviation overlay is a beat, not a mode: it reads for a few seconds and clears
  // itself so the operator never has to dismiss anything mid-demo.
  useEffect(() => {
    if (!state.deviation) return;
    if (deviationTimer.current) window.clearTimeout(deviationTimer.current);
    deviationTimer.current = window.setTimeout(
      () => setState((p) => ({ ...p, deviation: null, recovery: null })),
      6500,
    );
    return () => {
      if (deviationTimer.current) window.clearTimeout(deviationTimer.current);
    };
  }, [state.deviation]);

  const derived = useMemo(() => {
    const verified = state.actions.filter((a) => a.status === 'verified').length;
    const live = state.actions.filter((a) =>
      ['dispatched', 'acknowledged', 'executing'].includes(a.status),
    );
    return {
      verified,
      live,
      progress: state.actions.length ? verified / state.actions.length : 0,
      connectedWorkers: state.workers.filter((w) => w.connected).length,
      parallelNow: live.length,
    };
  }, [state.actions, state.workers]);

  const dismissFeed = useCallback(() => setState((p) => ({ ...p, feedAnswer: null })), []);

  return { state, derived, connected, reconnecting, send, dismissFeed };
}
