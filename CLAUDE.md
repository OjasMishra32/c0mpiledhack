# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

This is **HIVE** — "the AI operating system for physical organizations," a hackathon project. As of
this writing the repo contains only scaffolding: `README.md`, `docs/CONTRACTS.md` (the shared
data-model/protocol spec — read this first), `docs/SCENARIOS.md`, five per-person handoff docs
(`Ojas.md`, `Zechariah.md`, `David.md`, `Steven.md`, `Nikki.md`), a `Makefile`, `.env.example`, and
`backend/requirements.txt`. **`backend/app/` and `frontend/` do not exist yet.** Everything under
"Architecture" below describes the target design from the docs, not verified-working code — check
the actual directory tree before assuming a module exists.

The per-person docs are prescriptive design specs (code sketches, exact algorithms, data flows, even
UI copy) for each owner's area, not just handoff notes. Read the relevant one before implementing in
that area.

## Commands

```
make install   # backend venv (backend/.venv) + frontend npm install; copies .env.example -> .env
make dev       # backend :8000 (uvicorn --reload) + frontend :5173 (vite) via `make -j2`
make demo      # `make dev` with DEMO_MODE=true WORLD_MODE=simulation — no camera/keys/phones needed
make test      # backend/.venv/bin/pytest backend/tests -q ; cd frontend && npm run test --if-present
make ip        # prints the LAN join URL for phones (http://<lan-ip>:5173/join)
make clean     # removes backend/.venv, frontend/node_modules, frontend/dist
```

Single backend test: `backend/.venv/bin/pytest backend/tests/test_x.py::test_name -q`

Copy `.env.example` to `.env`. Every variable has a working default — nothing is required to run.
The system must work with **no API key, no camera, and no phones** (`make demo` is the proof of
this); never write a code path whose happy case assumes any of those three are present.

## Architecture (target design — full spec in `docs/CONTRACTS.md`)

Pipeline: camera → CV tracker (10Hz, Steven) + VLM over NVIDIA NIM (~1.4Hz, Ojas) → `fusion.py` →
grounding (binds language like "the scanner" to a live observed object id, Zechariah) → planner
(LLM with template-planner fallback) → validator (networkx cycle/reachability checks, auto-repair) →
capability-aware scheduler + resource locks → orchestrator tick (4Hz, single writer) → dispatch to
private worker phones (Nikki) → vision/worker-report verification (weighted evidence fusion) →
deviation detection → recovery (isolate the affected branch, never restart the whole plan) →
optional Voygr voice escalation as a last resort.

### Non-negotiable invariants (`docs/CONTRACTS.md` §5) — hold any change to these
1. No blocking calls in the event loop — camera reads, LLM calls, Voygr HTTP all go through
   `asyncio.to_thread` or an async client with a timeout.
2. Every external call has a timeout and a fallback: LLM → template planner, camera → simulation
   mode, Voygr → a logged event that still renders in the UI.
3. The full flagship demo must complete with zero API key, zero camera, zero phones.
4. The orchestrator tick never raises out of itself — wrap, log, emit a `warn` event, keep ticking.
5. `host_reset` fully rebuilds state from the scenario without dropping sockets.

### Core data model
- Backend source of truth: `backend/app/models.py` (Pydantic). Frontend source of truth:
  `frontend/src/types/hive.ts`. **These two must stay structurally identical** — snake_case field
  names on both sides, no camelCase mapping layer. Changing either (or `docs/CONTRACTS.md` itself)
  requires whole-team agreement, per the doc's own rule.
- **Nothing about objects or zones is hardcoded.** Objects are discovered generically from measured
  HSV/shape descriptors — never a preset color→meaning table (`OBJECT_COLORS = {...}` is the bug to
  watch for). Zones are auto-detected, drawn, or named from the objective at runtime, never a
  module-level constant list.
- `Action.status` is mutated **only** by `orchestrator.py`; the scheduler, verifier, and planner
  return decisions and the orchestrator applies them.
- Event `seq` is assigned in exactly one place, `state.emit()`, under a single lock — this is what
  keeps the timeline gap-free and consistently orderable across clients (sort by `seq`, never by
  timestamp).
- `Action.lock_targets` are opaque strings (`object:obj_3`, `zone:zone_2`); two actions whose
  `lock_targets` intersect may never be dispatched in the same tick. This is the entire
  concurrency-safety model, deliberately simple so it can't break on stage.

### Orchestrator tick order — fixed, do not reorder (`backend/app/orchestrator.py`)
`drain_inbox → world_model.refresh → verifier.evaluate → complete_actions → unlock_dependents →
detect_deviations → detect_timeouts → run_recovery → assign_actions → dispatch → check_goal →
flush_broadcasts`

### WebSocket protocol
One endpoint: `GET /ws?role=host|worker&token=<uuid>`. Workers receive **only their own**
`instruction_created` message — never the goal text, the action list, or another worker's
instruction (enforced on both client and server). This is the entire "private instructions" premise
of the product; a worker who can infer the plan breaks it. Full message catalog: `docs/CONTRACTS.md`
§3.

### Ownership map
| Area | Owner | Primary files |
|---|---|---|
| Backend core, state, WS, orchestrator, VLM perception | Ojas | `backend/app/{main,config,models,state,websocket_manager,orchestrator}.py`, `backend/app/perception/*` |
| Planner (LLM + template + validator), scheduler | Zechariah | `backend/app/planner/*`, `backend/app/scheduler.py` |
| Host command center UI, design system, DAG | David | `frontend/src/routes/Host.tsx`, `frontend/src/components/*` |
| Vision, world model, calibration, simulation | Steven | `backend/app/vision/*`, `backend/app/demo/simulator.py`, `frontend/src/components/WorldView.tsx` |
| Worker PWA, verification + recovery, Voygr voice escalation | Nikki | `frontend/src/routes/{Join,Worker}.tsx`, `backend/app/{verifier,recovery}.py`, `backend/app/integrations/voygr.py` |
