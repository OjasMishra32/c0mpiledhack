// Frontend source of truth for HIVE's shared types.
// Must stay structurally identical to backend/app/models.py (Pydantic).
// Field names are snake_case on both sides — do not camelCase. See docs/CONTRACTS.md.

// ── Enums ──────────────────────────────────────────────────────────────────

export type WorkerStatus =
  | 'disconnected' | 'joining' | 'ready' | 'assigned' | 'executing'
  | 'blocked' | 'paused' | 'unavailable' | 'emergency';

export type ActionStatus =
  | 'queued' | 'available' | 'assigned' | 'dispatched' | 'acknowledged'
  | 'executing' | 'awaiting_verification' | 'verified' | 'failed'
  | 'blocked' | 'cancelled' | 'recovery';

export type GoalStatus =
  | 'draft' | 'compiling' | 'compiled' | 'executing' | 'paused' | 'completed'
  | 'failed' | 'aborted';

export type PlanSource = 'llm' | 'template' | 'demo_script' | 'manual';

export type WorldMode = 'live' | 'assisted' | 'simulation';

export type Severity = 'debug' | 'info' | 'warn' | 'critical' | 'success';

export type ActionType =
  | 'pick_up' | 'move_to_zone' | 'place_in_zone' | 'place_on' | 'hold'
  | 'release' | 'inspect' | 'standby';

export type PredicateType =
  | 'object_in_zone' | 'object_near_object' | 'object_stacked_on' | 'object_held_by'
  | 'worker_ready' | 'worker_idle' | 'object_visible' | 'sequence_completed'
  | 'all_objects_in_zone' | 'worker_acknowledged' | 'manually_verified';

export type EvidenceKind =
  | 'vision' | 'vlm' | 'worker_report' | 'host_override' | 'simulation'
  | 'timing' | 'inference';

export type PerceptionTier = 'cv' | 'vlm_fast' | 'vlm_reason';

export type ZoneStatus = 'unknown' | 'pending' | 'active' | 'satisfied' | 'blocked';

export type WorldZoneStatus = 'unknown' | 'pending' | 'stabilizing' | 'stable' | 'critical';

export type ZoneSource = 'detected' | 'drawn' | 'inferred';

export type LabelingSource = 'vlm' | 'descriptor' | 'none';

export type ObjectSource = 'vision' | 'simulation' | 'host_override';

export type ShapeHint = 'round' | 'rectangular' | 'irregular';

export type CommsProfile = 'voice' | 'silent';

export type Urgency = 'normal' | 'high' | 'critical';

export type FailureKind =
  | 'wrong_object_move' | 'object_removed' | 'worker_timeout' | 'worker_down'
  | 'verification_regress' | 'zone_blocked';

// ── Core models ────────────────────────────────────────────────────────────

export interface Point2 {
  x: number;
  y: number;
}

export interface Rect2 {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Worker {
  id: string;
  display_name: string;
  callsign: string;
  color: string;
  status: WorkerStatus;
  connected: boolean;
  available: boolean;
  current_action_id: string | null;
  reachable_zones: string[];
  role: string;
  supported_actions: ActionType[];
  position: Point2;
  last_seen_at: string;
  assignment_count: number;
  confidence: number;
  // session_token is never sent to the host UI
}

export interface ObjectDescriptor {
  dominant_hsv: [number, number, number];
  color_name: string;
  color_hex: string;
  area_norm: number;
  aspect: number;
  circularity: number;
  shape_hint: ShapeHint;
}

export interface ObservedObject {
  id: string;

  // measured by the vision pipeline
  descriptor: ObjectDescriptor;
  position: Point2;
  zone: string;
  visible: boolean;
  confidence: number;
  first_seen_at: string;
  last_updated_at: string;
  source: ObjectSource;

  // assigned by semantic labeling + grounding
  semantic_label: string | null;
  role: string | null;
  role_confidence: number | null;

  // runtime state
  held_by: string | null;
  stacked_on: string | null;
  locked_by: string | null;
}

export interface Zone {
  id: string;
  label: string;
  bounds: Rect2;
  occupancy: string[];
  status: ZoneStatus;
  source: ZoneSource;
}

export interface Scene {
  objects: ObservedObject[];
  zones: Zone[];
  scanned_at: string;
  object_count: number;
  labeling_source: LabelingSource;
  stable: boolean;
}

export interface Predicate {
  type: PredicateType;
  subject: string;
  object: string;
  tolerance?: number;
}

export interface Instruction {
  id: string;
  action_id: string;
  worker_id: string;
  display_text: string;
  spoken_text: string;
  detail_text: string;
  urgency: Urgency;
  expected_duration_seconds: number;
  requires_verification: boolean;
  correction_text: string | null;
  issued_at: string;
}

export interface Action {
  id: string;
  type: ActionType;
  description: string;
  object_id: string | null;
  target_object_id: string | null;
  target_zone: string | null;
  assigned_worker_id: string | null;
  assignment_reason: string | null;
  dependencies: string[];
  status: ActionStatus;
  priority: number;
  timeout_seconds: number;
  expected_predicates: Predicate[];
  instruction: Instruction | null;
  retry_count: number;
  max_retries: number;
  is_recovery: boolean;
  created_at: string;
  dispatched_at: string | null;
  completed_at: string | null;
  lock_targets: string[];
}

export interface Evidence {
  kind: EvidenceKind;
  confidence: number;
  weight: number;
  detail: string;
  at: string;
}

export interface Goal {
  id: string;
  raw_text: string;
  normalized_intent: string;
  status: GoalStatus;
  success_predicates: Predicate[];
  plan_source: PlanSource;
  planner_notes: string;
  created_at: string;
}

export interface Event {
  id: string;
  seq: number;
  timestamp: string;
  type: string;
  severity: Severity;
  actor: string; // hive | worker_a | host | vision | voygr
  message: string;
  metadata: Record<string, unknown>;
}

export interface WorldZone {
  id: string;
  label: string;
  bounds: Rect2;
  occupancy: string[];
  status: WorldZoneStatus;
}

export interface WorldState {
  mode: WorldMode;
  objects: ObservedObject[];
  zones: WorldZone[];
  camera_online: boolean;
  vision_fps: number;
  last_frame_at: string;
}

export interface RecoveryPlan {
  [key: string]: unknown;
}

export interface AfterActionReport {
  [key: string]: unknown;
}

export interface Scenario {
  id: string;
  lexicon: Record<string, string>;
  comms_profile: CommsProfile;
  [key: string]: unknown;
}

// ── Aggregate state (what the host renders) ────────────────────────────────

export interface HiveState {
  mode: WorldMode;
  goal: Goal | null;
  actions: Action[];
  workers: Worker[];
  scene: Scene | null;
  world: WorldState | null;
  events: Event[];
  scenario: Scenario | null;
}

// ── WebSocket envelope ──────────────────────────────────────────────────────

export interface WsEnvelope<T = unknown> {
  type: string;
  payload: T;
  ts: string;
  seq: number;
}

// Server → Client payloads (selected, expand as needed)

export interface GroundingBinding {
  phrase: string;
  object_id: string;
  confidence: number;
  alternatives: string[];
}

export interface DeviationDetectedPayload {
  expected: string;
  observed: string;
  message: string;
  action_ids: string[];
}

export interface RecoveryCompletedPayload {
  summary: string;
}

export interface ActionStatusChangedPayload {
  action_id: string;
  status: ActionStatus;
  previous: ActionStatus;
  reason?: string;
}

export interface GroundingAmbiguousPayload {
  phrase: string;
  candidates: string[];
  message: string;
}

export interface PlanCompiledPayload {
  goal: Goal;
  actions: Action[];
  stats: {
    action_count: number;
    parallel_count: number;
    conflict_count: number;
    [key: string]: unknown;
  };
}


/** Run metrics — mirrors backend/app/models.py RunMetrics. */
export interface RunMetrics {
  actions_total: number;
  actions_verified: number;
  parallel_peak: number;
  recoveries: number;
  reassignments: number;
  deviations: number;
  conflicts: number;
  avg_confidence: number;
  worker_idle_seconds: number;
  started_at: string | null;
  completed_at: string | null;
  elapsed_seconds: number;
}

/** Per-worker contribution — mirrors backend/app/attribution.py. */
export interface Contribution {
  worker_id: string;
  callsign: string;
  completed: number;
  failed: number;
  reliability: number;
  mean_seconds: number | null;
  zones: Record<string, number>;
}
