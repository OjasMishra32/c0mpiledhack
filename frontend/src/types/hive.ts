// Shared types. Must stay structurally identical to backend/app/models.py —
// snake_case field names on both sides, no camelCase mapping layer.
// See docs/CONTRACTS.md. Only the slice Nikki's worker client needs is defined here;
// the rest (Action, Scene, Goal, ...) is David's/Ojas's scope.

export type WorkerStatus =
  | "disconnected" | "joining" | "ready" | "assigned" | "executing"
  | "blocked" | "paused" | "unavailable" | "emergency";

export interface WorkerIdentity {
  id: string;
  display_name: string;
  callsign: string;
  color: string;
  status: WorkerStatus;
  connected: boolean;
  available: boolean;
  current_action_id: string | null;
  role: string;
}

export type Urgency = "normal" | "high" | "critical";

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

export interface Envelope<T = unknown> {
  type: string;
  payload: T;
  ts: string;
  seq: number;
}

export type CommsProfile = "voice" | "silent";
