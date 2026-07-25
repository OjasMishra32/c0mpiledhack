# HIVE — Shared Contracts

**This file is the spine of the project. It is the only document all five workstreams must agree on.**

If you need to change something in here, you do not change it alone. Post in the team channel,
change it here first, then change your code. Anyone who edits their own copy of a type without
editing this file has broken the build for four other people.

Backend source of truth: `backend/app/models.py` (Pydantic)
Frontend source of truth: `frontend/src/types/hive.ts` (TypeScript)

These two files must stay structurally identical. Field names are `snake_case` on **both sides**.
Do not camelCase on the frontend. We are not writing a REST API for the public; we are writing a
nervous system for a 90-second demo. Matching names means zero mapping code and zero 11pm bugs.

---

## 0. The world these types describe

A camera looks at a space. HIVE **discovers what is in it**, a human states an objective in plain
language, and HIVE binds that objective to the things it can actually see.

### Nothing about the objects is hardcoded

There is no preset list of objects, no fixed color→meaning table, no scenario manifest enumerating
what should be on the table. That would be a puppet show. Instead:

```
    camera frame
         │
         ▼
  ┌──────────────────┐   segments salient regions, no prior expectations
  │ Scene discovery  │   → N ObservedObjects with MEASURED descriptors
  └──────────────────┘      (hue, area, shape, position) and synthetic ids
         │
         ▼
  ┌──────────────────┐   optional single-frame VLM pass at compile time
  │ Semantic labeling│   → "red plastic cup", "blue folder", "yellow box"
  └──────────────────┘
         │
         ▼
  ┌──────────────────┐   "move the red container to the packing area"
  │ Grounding /      │   → resolves the phrase against the OBSERVED scene
  │ reference resolve│   → binds obj_3 to the role "the red container"
  └──────────────────┘
         │
         ▼
     task graph over real, observed object ids
```

Put a completely different set of objects on the table, type a completely different task, and it
works. That is the demo claim, and it has to be literally true — a judge will test it.

### Object ids

Assigned at discovery: `obj_1`, `obj_2`, … They are **stable across frames** via tracking (nearest-
neighbour association on position + descriptor), and stable across a re-scan when the object hasn't
moved much. They carry no meaning. Meaning arrives from grounding, and lives in `role`.

### Zones

Zones are **not** hardcoded either. Three ways they get defined, in order of preference:

1. **Auto-detected** — the host clicks *Detect Regions* and the tracker finds taped rectangles /
   high-contrast boundaries, proposing zones the host can accept or edit.
2. **Drawn** — the host drags rectangles on the live feed. Takes ~15 seconds for four zones.
3. **Named from the objective** — the planner extracts place names from the goal text
   ("the packing area", "aisle B") and asks the host to bind each one to a region.

Zone ids are `zone_1`, `zone_2`, … plus the implicit `field` (anywhere unassigned). Labels come from
whatever the host or the objective calls them.

Zones are rectangles in **normalized coordinates** (0..1, origin top-left of the camera frame).
Everything spatial in HIVE is normalized. There are no pixels in the data model. Ever.

### What a "scenario" actually is

A saved bundle of *starting conditions*, not a manifest of truth: a suggested objective string, a
zone layout, a lexicon for UI copy, and optionally a known-good graph used only as the emergency
parachute. Scenarios are conveniences for rehearsal. **The system must run correctly on a table it
has never seen with a goal nobody pre-wrote.** See `docs/SCENARIOS.md`.

### Lexicon

Each scenario carries a `lexicon: dict[str, str]` that overrides UI copy (`collective`, `worker`,
`objective`, `object`, `zone`, `complete`, `deviation`). It is delivered in `state_snapshot`.
Frontend reads it via `useLexicon()`. **No component may hardcode a scenario-specific string.**

### Comms profile

`scenario.comms_profile ∈ {voice, silent}`. `voice` is the default: TTS + vibration on the worker
phone. `silent` (campus emergency) disables speech and vibration entirely and switches the phone to
a minimum-brightness high-contrast palette. Nikki's client branches on this one field.

---

## 1. Enums

Copy these exactly. They are strings, not ints. They serialize as-is.

```python
WorkerStatus  = disconnected | joining | ready | assigned | executing | blocked | paused | unavailable | emergency
ActionStatus  = queued | available | assigned | dispatched | acknowledged | executing | awaiting_verification | verified | failed | blocked | cancelled | recovery
GoalStatus    = draft | compiling | compiled | executing | paused | completed | failed | aborted
PlanSource    = llm | template | demo_script | manual
WorldMode     = live | assisted | simulation
Severity      = debug | info | warn | critical | success
ActionType    = pick_up | move_to_zone | place_in_zone | place_on | hold | release | inspect | standby
PredicateType = object_in_zone | object_near_object | object_stacked_on | object_held_by
              | worker_ready | worker_idle | object_visible | sequence_completed
              | all_objects_in_zone | worker_acknowledged | manually_verified
EvidenceKind  = vision | vlm | worker_report | host_override | simulation | timing | inference
PerceptionTier= cv | vlm_fast | vlm_reason
```

### Action status lifecycle

```
queued ──▶ available ──▶ assigned ──▶ dispatched ──▶ acknowledged ──▶ executing ──▶ awaiting_verification ──▶ verified
   │            │             │             │              │              │                   │
   └────────────┴─────────────┴─────────────┴──────────────┴──────────────┴───────────────────┴──▶ cancelled
                              │                                           │
                              └────────────── blocked ◀───────────────────┤
                                                                          ├──▶ failed ──▶ recovery ──▶ queued
                                                                          └──▶ (timeout) ──▶ recovery
```

**Only `orchestrator.py` mutates `Action.status`.** Not the scheduler, not the verifier, not a
route handler. They return decisions; the orchestrator applies them. This single rule prevents
the entire class of "two things changed the same action in the same tick" bugs.

---

## 2. Core models

### Worker

```jsonc
{
  "id": "worker_a",
  "display_name": "Worker A",
  "callsign": "ALPHA",              // shown large on the phone
  "color": "#5AC8FA",               // unique per worker, used in graph edges
  "status": "ready",
  "connected": true,
  "available": true,
  "current_action_id": null,
  "reachable_zones": ["z1", "z4", "field"],
  "role": "Picker A",               // scenario-supplied label shown on the phone and host
  "supported_actions": ["pick_up", "move_to_zone", "place_in_zone", "place_on", "hold", "release", "inspect"],
  "position": { "x": 0.10, "y": 0.50 },  // where they sit at the table, normalized
  "last_seen_at": "2026-07-24T19:41:02.113Z",
  "assignment_count": 3,            // fairness input for the scheduler
  "confidence": 1.0,                // how much we trust their self-reports (drops after a failure)
  "session_token": "…"              // never sent to the host UI, never logged
}
```

Five fixed slots exist from boot: `worker_a` … `worker_e`, callsigns ALPHA BRAVO CHARLIE DELTA ECHO.
Workers do not get *created* on join — they get **claimed**. This is why refresh does not spawn a
sixth worker.

Worker colors (fixed, do not improvise — the whole UI keys off these):

| Worker | Callsign | Color     |
| ------ | -------- | --------- |
| A      | ALPHA    | `#5AC8FA` |
| B      | BRAVO    | `#5E5CE6` |
| C      | CHARLIE  | `#30D158` |
| D      | DELTA    | `#FF9F0A` |
| E      | ECHO     | `#FF375F` |

### ObservedObject

Everything above the line is **measured**. Everything below it is **assigned by grounding**.
Nothing here is configured ahead of time.

```jsonc
{
  "id": "obj_3",                    // synthetic, assigned at discovery, stable via tracking

  // ── MEASURED by the vision pipeline ──────────────────────────────
  "descriptor": {
    "dominant_hsv": [8, 214, 190],  // median HSV of the region
    "color_name": "red",            // derived from hue at runtime by a naming function
    "color_hex": "#C43A2E",         // for the UI — the object's ACTUAL sampled color
    "area_norm": 0.021,             // fraction of frame
    "aspect": 1.08,
    "circularity": 0.86,
    "shape_hint": "round"           // round | rectangular | irregular
  },
  "position": { "x": 0.24, "y": 0.61 },
  "zone": "zone_4",                 // classified from position, or "field"
  "visible": true,
  "confidence": 0.91,               // detection quality; 1.0 in simulation
  "first_seen_at": "…",
  "last_updated_at": "…",
  "source": "vision",               // vision | simulation | host_override

  // ── ASSIGNED by semantic labeling + grounding ────────────────────
  "semantic_label": "red plastic cup",  // optional VLM pass; falls back to "red round object"
  "role": "the priority item",      // bound from the objective text, null until grounded
  "role_confidence": 0.88,

  // ── RUNTIME state ────────────────────────────────────────────────
  "held_by": null,                  // worker_id or null
  "stacked_on": null,               // object_id or null
  "locked_by": null                 // action_id holding the resource lock
}
```

**The UI renders `descriptor.color_hex`**, sampled from the actual frame — not a palette constant.
When someone puts a teal object on the table, the node on the graph is teal. That detail does more
to sell "it's really seeing this" than any amount of copy.

`label` for display = `role` ?? `semantic_label` ?? `"{color_name} {shape_hint} object"`.

### Zone

```jsonc
{
  "id": "zone_2",
  "label": "Pack Station",          // from the host or extracted from the objective
  "bounds": { "x": 0.74, "y": 0.28, "w": 0.24, "h": 0.44 },
  "occupancy": ["obj_1", "obj_4"],
  "status": "pending",              // unknown | pending | active | satisfied | blocked
  "source": "detected"              // detected | drawn | inferred
}
```

### Scene

The discovery result, produced on *Scan Scene* and refreshed continuously.

```jsonc
{
  "objects": [ … ObservedObject … ],
  "zones": [ … Zone … ],
  "scanned_at": "…",
  "object_count": 5,
  "labeling_source": "vlm",         // vlm | descriptor | none
  "stable": true                    // discovery has settled; safe to compile a plan against
}
```

`stable` matters: **do not compile a plan while the scene is still settling.** The host's Compile
button stays disabled until `stable` is true, which takes ~1.5s after a scan.

### Action

```jsonc
{
  "id": "a3",
  "type": "place_in_zone",
  "description": "Place the handheld scanner in the Pack Station.",
  "object_id": "yellow",
  "target_object_id": null,
  "target_zone": "z2",
  "assigned_worker_id": "worker_c",
  "assignment_reason": "CHARLIE selected: closest to the scanner, currently idle, no conflicting activity in that zone.",
  "dependencies": [],
  "status": "dispatched",
  "priority": 85,                   // higher runs first when both are available
  "timeout_seconds": 25,
  "expected_predicates": [
    { "type": "object_in_zone", "subject": "yellow", "object": "z2" }
  ],
  "instruction": { … Instruction … },
  "retry_count": 0,
  "max_retries": 2,
  "is_recovery": false,
  "created_at": "…",
  "dispatched_at": "…",
  "completed_at": null,
  "lock_targets": ["object:yellow", "zone:z2"]
}
```

`lock_targets` are opaque strings. The orchestrator holds a `dict[str, action_id]` of live locks.
Two actions whose `lock_targets` intersect **may never be dispatched in the same tick**. This is
the entire concurrency-safety model and it is deliberately dumb so it cannot break on stage.

### Instruction

What actually lands on a phone. Generated at dispatch time, never before.

```jsonc
{
  "id": "instr_a3_1",               // MUST be unique per (action, attempt). The phone speaks once per id.
  "action_id": "a3",
  "worker_id": "worker_c",
  "display_text": "MOVE THE YELLOW ITEM TO PACK STATION",
  "spoken_text": "Move the yellow item to the pack station.",
  "detail_text": "Set it down inside the taped square. Then step back.",
  "urgency": "normal",              // normal | high | critical
  "expected_duration_seconds": 12,
  "requires_verification": true,
  "correction_text": null,          // filled on a recovery re-issue
  "issued_at": "…"
}
```

**The `id` uniqueness rule is a hard requirement.** Nikki's client keys speech synthesis off it.
If the backend re-sends the same `instruction.id`, the phone stays silent (correct — it's a
re-render). If it sends a new id, the phone speaks. A repeat button bumps nothing; it just re-speaks
the current one locally.

### Predicate

```jsonc
{ "type": "object_in_zone", "subject": "yellow", "object": "z2", "tolerance": 0.08 }
```

`subject` and `object` are ids (object id, zone id, or worker id depending on `type`).
`tolerance` only matters for proximity predicates.

### Evidence & Verification

```jsonc
{
  "kind": "vision",
  "confidence": 0.81,
  "weight": 0.6,
  "detail": "yellow centroid inside z2 bounds for 5 consecutive frames",
  "at": "…"
}
```

Verification is a **weighted sum, clamped to 1.0**:

```
score = Σ (evidence.confidence × evidence.weight)
```

| Evidence kind | Weight | Source |
| ------------- | ------ | ------ |
| `vision`      | 0.60   | Steven's CV tracker — position/zone facts |
| `vlm`         | 0.55   | Ojas's VLM layer — relations (`held_by`, `stacked_on`), activity |
| `worker_report` | 0.30 | a human tapped Completed |
| `simulation`  | 0.95   | simulation mode |
| `host_override` | 1.00 | operator confirmed |
| `timing`      | 0.10   | elapsed-time inference |
| `inference`   | 0.15   | derived from other verified predicates |

`vision` and `vlm` are **independent sensors** and both may contribute to the same predicate — CV
says the centroid is inside the zone (0.6 × 0.85 = 0.51), the VLM says "the yellow box is sitting in
the right-hand taped square" (0.55 × 0.9 = 0.50), and together they clear the bar without a human.
That is genuine sensor fusion and the UI should name both sources when it happens.

Verified when `score >= 0.70`. Displayed honestly in the UI as a percentage. We do not claim
certainty we do not have — that honesty is itself a selling point to judges.

In `simulation` mode a single simulation evidence (0.95 × 0.95 = 0.90) clears the bar alone.
In `live` mode vision alone gives 0.6×~0.85 = 0.51 — **not enough**. It needs the worker's
"Completed" tap (+0.30×1.0) to reach 0.81. That is intentional: HIVE fuses machine perception with
human confirmation, and the UI should say so.

### Goal

```jsonc
{
  "id": "goal_1",
  "raw_text": "Stabilize all three emergency zones…",
  "normalized_intent": "stabilize_incident",
  "status": "executing",
  "success_predicates": [ … ],
  "plan_source": "llm",
  "planner_notes": "11 actions, 4 parallelizable, 2 resource conflicts identified.",
  "created_at": "…"
}
```

### Event

Everything important emits one. This stream *is* the demo's narration.

```jsonc
{
  "id": "evt_000123",
  "seq": 123,                       // monotonic, gap-free, assigned by the event bus only
  "timestamp": "…",
  "type": "action_verified",
  "severity": "success",
  "actor": "hive",                  // hive | worker_a | host | vision | voygr
  "message": "Water supply confirmed at Medical Station. Confidence 84%.",
  "metadata": { "action_id": "a3", "confidence": 0.84 }
}
```

Event `seq` is assigned in one place (`state.emit()`) under a single asyncio lock. Ordering is
stable across all clients. Late-joining clients get the last 200 events in `state_snapshot`.

### WorldState (the thing the host renders)

```jsonc
{
  "mode": "live",
  "objects": [ … ],
  "zones": [ { "id": "z2", "label": "Pack Station", "bounds": {"x":0.74,"y":0.28,"w":0.24,"h":0.44}, "occupancy": ["red","green"], "status": "pending" } ],
  "camera_online": true,
  "vision_fps": 11.4,
  "last_frame_at": "…"
}
```

Zone `status`: `unknown | pending | stabilizing | stable | critical`.

---

## 3. WebSocket protocol

**One endpoint.** `GET /ws?role=host|worker&token=<uuid>`

Token is generated client-side on first visit, stored in `localStorage['hive_token']`, and sent on
every connect. That is the entire "auth" system. It exists solely so a phone refresh reclaims the
same worker slot.

### Envelope — every message, both directions

```jsonc
{ "type": "action_status_changed", "payload": { … }, "ts": "2026-07-24T19:41:02.113Z", "seq": 412 }
```

`seq` is server-assigned on server→client messages and ignored on client→server.

### Client → Server

| type | payload | who |
|---|---|---|
| `worker_join` | `{ token, requested_id? }` | worker |
| `worker_ready` | `{}` | worker |
| `worker_acknowledged` | `{ action_id, instruction_id }` | worker |
| `worker_completed` | `{ action_id, confidence? }` | worker |
| `worker_blocked` | `{ action_id, reason }` | worker |
| `worker_help` | `{ action_id?, note? }` | worker |
| `worker_pause` | `{}` | worker |
| `worker_resume` | `{}` | worker |
| `worker_emergency` | `{ note? }` | worker |
| `worker_heartbeat` | `{}` | worker (every 3s) |
| `host_scan_scene` | `{ relabel?: bool }` | host — rediscover objects & zones |
| `host_ask_feed` | `{ question }` | host — VLM Q&A over the live frame |
| `host_set_perception` | `{ vlm_enabled?: bool, vlm_hz?: float }` | host |
| `host_bind_object` | `{ object_id, role, label? }` | host — correct a grounding |
| `host_define_zone` | `{ zone_id?, bounds, label }` | host — draw/rename a zone |
| `host_detect_zones` | `{}` | host — auto-propose zone rectangles |
| `host_compile_goal` | `{ text, scenario_id? }` | host |
| `host_start_execution` | `{}` | host |
| `host_pause_all` / `host_resume_all` | `{}` | host |
| `host_reset` | `{ scenario_id? }` | host |
| `host_manual_verify` | `{ action_id, verified: bool }` | host |
| `host_inject_failure` | `{ kind, target_id? }` | host |
| `host_update_object` | `{ object_id, position?, zone? }` | host |
| `host_set_mode` | `{ mode }` | host |
| `host_set_worker` | `{ worker_id, available?, status? }` | host |
| `host_reassign` | `{ action_id, worker_id? }` | host |
| `host_skip_action` | `{ action_id }` | host |
| `host_escalate_call` | `{ zone_id?, reason }` | host — fires Voygr |

`host_inject_failure.kind` ∈ `wrong_object_move | object_removed | worker_timeout | worker_down | verification_regress | zone_blocked`.

### Server → Client

| type | payload | to |
|---|---|---|
| `state_snapshot` | full `HiveState` | on connect + after reset |
| `worker_assigned` | `{ worker_id, identity }` | the joining worker only |
| `instruction_created` | `Instruction` | one worker only |
| `instruction_cancelled` | `{ instruction_id, action_id, reason }` | one worker |
| `action_status_changed` | `{ action_id, status, previous, reason? }` | all |
| `world_state_changed` | `WorldState` (throttled, ≤5/s) | all |
| `workers_changed` | `Worker[]` | all |
| `scene_discovered` | `Scene` | all — after a scan, or when object count changes |
| `scene_narration` | `{ activity, people, anomalies, at }` | host — VLM fast-tier read |
| `perception_status` | `{ cv: bool, vlm_fast: bool, vlm_reason: bool, fps, vlm_hz }` | host |
| `adjudication` | `{ agrees, confidence, what_actually_happened, recommended }` | host |
| `grounding_resolved` | `{ bindings: [{ phrase, object_id, confidence, alternatives }] }` | host |
| `grounding_ambiguous` | `{ phrase, candidates: [object_id], message }` | host — needs a click |
| `plan_compiled` | `{ goal, actions, stats }` | all |
| `execution_started` / `execution_paused` / `execution_resumed` | `{}` | all |
| `deviation_detected` | `{ expected, observed, message, action_ids }` | all |
| `recovery_started` | `{ plan: RecoveryPlan }` | all |
| `recovery_completed` | `{ summary }` | all |
| `goal_completed` | `{ report: AfterActionReport }` | all |
| `emergency_stop` | `{ actor, reason }` | all |
| `call_initiated` | `{ call_id, to, reason }` | host |
| `call_updated` | `{ call_id, status, transcript_excerpt? }` | host |
| `event` | `Event` | all |
| `error_event` | `{ code, message, detail? }` | originator |

**Workers receive only their own `instruction_created`.** A worker socket must never receive the
action list, the goal text, or another worker's instruction. Nikki enforces this on the client;
Ojas enforces it on the server. Both. If a judge opens devtools on a phone and sees the whole plan,
the entire premise of the demo collapses.

### HTTP endpoints (non-realtime)

| method | path | purpose |
|---|---|---|
| `GET` | `/api/health` | `{ ok, mode, workers_connected, uptime }` |
| `GET` | `/api/join-info` | `{ url, lan_ip, port }` — powers the QR code |
| `GET` | `/api/scenarios` | preset list |
| `GET` | `/api/state` | full snapshot (debug / test harness) |
| `GET` | `/api/vision/frame.mjpg` | MJPEG stream of the annotated camera feed |
| `POST` | `/api/vision/calibrate` | `{ color, hsv_lo, hsv_hi, min_area }` |
| `POST` | `/api/vision/zones` | zone rectangles |
| `POST` | `/api/voygr/call` | manual escalation trigger |

---

## 4. Ownership map

| Area | Owner | Primary files |
|---|---|---|
| Backend core, state, WS, orchestrator loop, integration | **Ojas** | `backend/app/{main,config,models,state,websocket_manager,orchestrator}.py` |
| Planner (LLM + template + validator), scheduler | **Zechariah** | `backend/app/planner/*`, `backend/app/scheduler.py` |
| Host command center UI, design system, DAG, timeline | **David** | `frontend/src/routes/Host.tsx`, `frontend/src/components/*` |
| Vision, world model, AR overlay, calibration, simulation | **Steven** | `backend/app/vision/*`, `backend/app/demo/simulator.py`, `frontend/src/components/WorldView.tsx` |
| Worker PWA, verification + recovery engine, Voygr calls | **Nikki** | `frontend/src/routes/{Join,Worker}.tsx`, `backend/app/{verifier,recovery}.py`, `backend/app/integrations/voygr.py` |

**Integration rule:** you own your files. If you need a change in someone else's file, ask them —
except for `models.py` / `hive.ts` / this document, where you ask the whole team.

---

## 5. Non-negotiables

1. **No blocking calls in the event loop.** OpenCV capture, LLM requests, and Voygr HTTP all run in
   `asyncio.to_thread` or an async client with a timeout. One blocking `requests.post` freezes every
   phone in the room.
2. **Every external call has a timeout and a fallback.** LLM → template planner. Camera → simulation.
   Voygr → logged event that still renders in the UI.
3. **The demo must survive with no API key, no camera, and no phones.** `DEMO_MODE=true` +
   simulation mode + simulated workers must complete the entire flagship run.
4. **Never `raise` out of the orchestrator tick.** Wrap it; log it; emit a `warn` event; keep ticking.
   A crashed loop is a dead demo. A logged exception is a shrug.
5. **Reset must be total.** `host_reset` rebuilds state from the scenario, keeps sockets alive,
   re-pushes snapshots, clears speech queues. We will press it a dozen times tomorrow.
