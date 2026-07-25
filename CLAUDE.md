# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

HIVE — "the AI operating system for physical organizations." It observes a physical space through
a camera, decomposes a high-level objective into a dependency-aware task graph, dispatches private
instructions to individual humans (phones), verifies what actually happened, detects when reality
diverges from the plan, and replans in real time without restarting the operation. Built for a
hackathon demo (live tabletop warehouse scenario + a campus-emergency video scenario).

**Current state: pre-implementation.** Only docs, `Makefile`, `.env.example`, and
`backend/requirements.txt` exist. `backend/app/` and `frontend/` have not been created yet — the
`Makefile` targets and repository layout described below are the *spec*, not yet reality. Check
what actually exists before assuming a file is there.

## Read these first, in order

1. `docs/CONTRACTS.md` — **the spine.** Every Pydantic model, every enum, the full WebSocket
   protocol, and the five non-negotiable rules. Backend (`backend/app/models.py`) and frontend
   (`frontend/src/types/hive.ts`) must stay structurally identical to it — `snake_case` on both
   sides, no camelCase on the frontend. If you change a shared type, this file changes first.
2. `docs/SCENARIOS.md` — what a scenario is (starting conditions + framing, never a hardcoded
   object list) and the specific scenarios (`warehouse_fulfillment` is the flagship live demo).
3. Your workstream's own handoff doc (`Ojas.md`, `Zechariah.md`, `David.md`, `Steven.md`,
   `Nikki.md`) — hour-by-hour build plan and file-level implementation guidance for that area.

## Commands

```bash
make install   # backend venv (backend/.venv) + pip install -r backend/requirements.txt + frontend npm install
make dev       # backend :8000 (uvicorn --reload) + frontend :5173, prints LAN join URL
make demo      # make dev with DEMO_MODE=true WORLD_MODE=simulation (no camera/keys/phones needed)
make test      # pytest backend/tests -q  +  npm run test --if-present (frontend)
make ip        # prints the phone join URL for the QR code
```

Single test: `backend/.venv/bin/pytest backend/tests/test_e2e_flagship.py -q` (this end-to-end test
is the canonical "does the whole flagship demo still work headless" check, once it exists).

`cp .env.example .env` before running — every env var has a working default; none are required to
boot. `NVIDIA_API_KEY` is the only one that unlocks the LLM planner and VLM perception; without it
the system still runs on the template planner and CV-only perception.

## Architecture (target shape, per README)

```
camera → Perception (OpenCV tracker @10Hz for position/zone/identity,
                      NVIDIA NIM VLM @~1.4Hz for semantics: held_by, stacked_on, activity)
       → Grounding (binds plain-language phrases like "the scanner" to discovered object ids)
       → AI task compiler (planner/: LLM planner, falls back silently to template_planner)
       → Graph validator (cycles, unknown ids, unreachable actions, unsafe parallelism)
       → Capability-aware scheduler + locks (distance, workload, reachability, fairness)
       → Orchestrator loop (4Hz state machine) ⇄ private worker clients (phones)
       → Weighted verification + deviation detection + recovery engine (isolate, don't restart)
       → Voygr voice escalation (only when a zone is critical and no responder is reassignable)
```

Planned repository layout:

```
backend/app/
├── main.py config.py models.py state.py websocket_manager.py
├── orchestrator.py scheduler.py verifier.py recovery.py
├── planner/     base.py llm_planner.py template_planner.py validator.py prompts.py
├── vision/      camera.py color_tracker.py world_model.py calibration.py
├── integrations/voygr.py
└── demo/        scenarios.py simulator.py
frontend/src/
├── routes/      Host.tsx Join.tsx Worker.tsx
├── components/  TaskGraph WorldView WorkerGrid EventTimeline AdvancedControls …
├── hooks/       useHiveSocket useSpeech useHaptics
└── types/hive.ts
```

### Design principles that shape every file

- **Nothing about objects/zones is hardcoded.** Vision discovers what's on the table; grounding
  binds task language to observed ids at runtime. No object manifest, no color→meaning table.
- **The LLM thinks occasionally** (goal → plan, hard recovery calls). **Deterministic code controls
  continuously** (scheduling, verification, the state machine, dispatch). No LLM in the inner loop.
- **Only `orchestrator.py` mutates `Action.status`.** Scheduler/verifier/route handlers return
  decisions; the orchestrator applies them. Prevents concurrent mutation bugs.
- **A scenario is data, not code** (`backend/app/demo/scenarios.py`) — a suggested objective
  string, zone labels, worker roles, a lexicon for UI copy, and an optional "known-good graph"
  parachute built over live ids at load time, never stored with literal ids.
- **Verification is evidence-weighted**, `score = Σ(evidence.confidence × evidence.weight)`,
  threshold `0.70` (see `docs/CONTRACTS.md` §Evidence for the weight table). `vision` and `vlm` are
  independent sensors that can co-verify a predicate without a human.
- **Workers receive only their own `instruction_created`** — never the full action list, goal
  text, or another worker's instruction. Enforced on both client and server.

### Non-negotiables (docs/CONTRACTS.md §5)

1. No blocking calls in the event loop — OpenCV, LLM requests, Voygr HTTP all go through
   `asyncio.to_thread` or an async client with a timeout.
2. Every external call has a timeout and a fallback (LLM→template planner, camera→simulation,
   Voygr→logged event).
3. The full flagship demo must complete with no API key, no camera, and no phones (`DEMO_MODE=true`
   + simulation mode + simulated workers).
4. Never `raise` out of the orchestrator tick — wrap it, log it, emit a `warn` event, keep ticking.
5. `host_reset` must rebuild state from the scenario completely while keeping sockets alive.

### Ownership

| Area | Owner | Files |
|---|---|---|
| Backend core, state, WS, orchestrator, integration | Ojas | `backend/app/{main,config,models,state,websocket_manager,orchestrator}.py` |
| Planner (LLM+template+validator), scheduler | Zechariah | `backend/app/planner/*`, `backend/app/scheduler.py` |
| Host command center UI, design system, DAG, timeline | David | `frontend/src/routes/Host.tsx`, `frontend/src/components/*` |
| Vision, world model, AR overlay, calibration, simulation | Steven | `backend/app/vision/*`, `backend/app/demo/simulator.py`, `frontend/src/components/WorldView.tsx` |
| Worker PWA, verification + recovery, Voygr calls | Nikki | `frontend/src/routes/{Join,Worker}.tsx`, `backend/app/{verifier,recovery}.py`, `backend/app/integrations/voygr.py` |

You own your files; changes to `models.py` / `hive.ts` / `docs/CONTRACTS.md` need the whole team's
sign-off since four other people's code depends on them staying in sync.
