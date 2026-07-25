# HIVE

**The AI operating system for physical organizations.**

HIVE takes a high-level objective, observes a physical space through an ordinary camera, decomposes
the objective into a dependency-aware task graph, dispatches *private* instructions to individual
humans, verifies what actually happened, detects when reality diverges from the plan, and replans
in real time — without restarting the operation.

Five people around a table. None of them knows the whole plan. Each one receives only their next
physical action. HIVE holds the shared world model.

---

## The problem

Physical organizations do not fail because nobody knows what to do. They fail because **plans go
stale faster than people can re-coordinate**.

A worker only sees their local task. A manager cannot continuously observe everything. One missing
person, one misplaced resource, one blocked area — and the delay cascades through every downstream
task, silently, until someone notices.

HIVE is the layer that notices. Continuously.

## The live demo: warehouse floor

Five workers around a table. A webcam overhead. Four taped zones — **Inbound Dock**, **Pack
Station**, **Pick Aisle A**, **Pick Aisle B** — and some objects standing in for inventory.

**HIVE has never seen any of it.** The presenter clicks *Scan Scene*; the camera discovers whatever
is actually on the table and reports what it found, in the objects' real sampled colors. Then the
presenter types a task in plain language, and HIVE binds the sentence to what it can see:

> Fulfill expedited order 4471 at the pack station and restock Pick Aisle B. Order 4471 needs the
> red item and the blue item. Packing can't start until the scanner is docked and materials staged.

*"the scanner"* resolves to a specific discovered object. So does *"the blue item."* Swap an object
for a different one, rescan, and it still works — there is no object manifest anywhere in the
system. HIVE then produces ~11 actions across 5 workers, identifies which 4 can run in parallel and
which 2 contend for the same item, and dispatches private instructions — each phone gets one atomic
task and nothing else.

Then **a judge walks up and moves the scanner into the wrong aisle.**

The webcam sees it. The host screen goes red:

> **FLOOR STATE DEVIATION** — Handheld scanner detected in Pick Aisle A, expected Pack Station.
> Packing workflow blocked. 3 dependent actions paused. **REPLANNING.**

HIVE freezes *only* the packing chain, works out which worker can reach the scanner, reassigns
retrieval, lets picking and restock continue uninterrupted, then resumes packing once the scanner is
back. Nothing restarts. The operation bends and keeps going.

That is the product: **it heals and redirects people in real time when the plan goes stale.**

## The vision use case: campus emergency (video)

The same system, a different operation — and the reason the architecture matters.

A school in emergency lockdown. Every staff member needs a *different* instruction, silently, based
on where they are: Wing A evacuates north, Wing B goes west, the gym holds because its only route
crosses the affected area. A PA announcement cannot say three different things at once. HIVE can.

It routes individually, delivers silently (no audio, no vibration, minimum screen brightness),
reconciles live headcounts against the roster (`100 / 119 accounted · Group 4 unreported`), reroutes
only the affected groups when new information arrives, and places a voice call to the district
safety line with a structured situation report compiled from live state — while the coordinator
keeps both hands on the evacuation.

**HIVE assists staff executing an established protocol.** It does not replace emergency dispatch and
does not make life-safety decisions autonomously. Every instruction it sends is one a trained
coordinator could have sent — HIVE sends it individually, simultaneously, and silently, which a
human coordinator cannot.

It is the same loop, the same code, a different scenario file. That is the whole argument.

See `docs/SCENARIOS.md` for both, plus disaster response, sorting, and relay scenarios.

## Why this is an "AI operating system for companies"

An OS does three things: it maintains a model of the machine's state, it schedules work onto
limited resources, and it recovers when a process misbehaves. HIVE does exactly that — with the
physical world as the machine and people, robots, and devices as the execution units.

The tabletop is a scale model. The same loop runs a warehouse floor, a hospital's logistics, an
airport turnaround, a construction site, a campus evacuation, or a disaster response. **A scenario
is data — zones, labels, objects, goal text — not code.** Retargeting HIVE to a new operation is a
config file, and we demonstrate that live by switching scenarios mid-demo.

Point it at existing CCTV instead of a webcam and the perception layer needs no redesign: the world
model consumes normalized detections, not camera-specific frames.

---

## Architecture

```
   ┌───────────────────────────────────┐   High-level objective, plain language
   │  PERCEPTION (two independent      │   ("fulfill order 4471, restock aisle B")
   │  sensors over one camera feed)    │                  │
   │                                   │                  │
   │  OpenCV tracker      10 Hz        │                  │
   │    → position, zone, identity     │                  │
   │  VLM (NVIDIA NIM)    ~1.4 Hz      │                  │
   │    → what things ARE, who is      │                  │
   │      holding what, what stacked   │                  │
   │      on what, what just happened  │                  │
   │                                   │                  │
   │  fused → obj_1..obj_N, zones      │                  │
   └───────────────────────────────────┘                  │
                     │                                    │
                     └────────────┬───────────────────────┘
                                  ▼
                    ┌────────────────────────┐
                    │  Grounding / reference │   "the scanner" → obj_3
                    │  resolution            │   ambiguity → ask the operator
                    └────────────────────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │   AI task compiler     │   LLM planner ─┐
                    │   (planner/)           │                ├─▶ falls back silently
                    └────────────────────────┘   Template ────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │  Graph validator       │   cycles · unknown ids · unreachable
                    │  (planner/validator)   │   actions · unsafe parallelism
                    └────────────────────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │ Capability-aware       │   distance · workload · reachability
                    │ scheduler + locks      │   capability · collision · fairness
                    └────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
         ┌────────────────────┐   ┌────────────────────────┐
         │ Private worker     │   │ Orchestrator loop      │
         │ clients (phones)   │◀─▶│ 4 Hz state machine     │
         └────────────────────┘   └────────────────────────┘
                    │                         ▲
                    ▼                         │
            Physical execution                │
                    │                         │
                    ▼                         │
         ┌────────────────────────┐           │
         │ Vision (OpenCV, 10fps) │───────────┤
         │ + worker feedback      │           │
         └────────────────────────┘           │
                    │                         │
                    ▼                         │
         ┌────────────────────────┐           │
         │ Weighted verification  │           │
         │ Deviation detection    │───────────┘
         │ Recovery engine        │      ↺ replan, don't restart
         └────────────────────────┘
                    │
                    ▼  (zone critical + no responder reassignable)
         ┌────────────────────────┐
         │ Voygr Callwright       │   HIVE places a real phone call
         │ voice escalation       │   to a human supervisor
         └────────────────────────┘
```

**The design principle:** the LLM thinks *occasionally* (goal → plan, and hard recovery decisions).
Deterministic code controls *continuously* (scheduling, verification, state machine, dispatch).
An LLM in the inner loop is a demo that hangs on stage.

---

## Quick start

```bash
make install     # backend venv + frontend node_modules
make dev         # backend :8000 + frontend :5173, prints your LAN join URL
make test        # pytest + vitest
make demo        # dev environment with DEMO_MODE=true and simulated workers
```

Open **http://localhost:5173/host** on the laptop. Point phones at the QR code.

Works with no API key. Works with no camera. Works with no phones.

### Connecting phones

1. Laptop and phones on the **same Wi-Fi**. (Conference Wi-Fi with client isolation will block this
   — use a phone hotspot that the laptop *also* joins. Test this before you present.)
2. `make dev` prints e.g. `join → http://192.168.1.42:5173/join`
3. The QR code on `/host` encodes exactly that URL.
4. Each phone that scans gets the next free slot: ALPHA, BRAVO, CHARLIE, DELTA, ECHO.
5. Refreshing a phone reclaims the same slot via a `localStorage` token.

### Environment

Copy `.env.example` → `.env`. Every variable has a working default; none are required.

| Variable | Default | Effect if absent |
| --- | --- | --- |
| `NVIDIA_API_KEY` | — | **the only key you need.** Without it: template planner + CV-only perception. Everything still runs. |
| `PLANNER_MODEL` | `nvidia/nemotron-3-super-120b-a12b` | task-graph compilation |
| `VLM_FAST_MODEL` | `nvidia/nemotron-nano-12b-v2-vl` | continuous structured scene reads |
| `VLM_REASON_MODEL` | `nvidia/cosmos3-nano-reasoner` | deviation adjudication, physical reasoning |
| `VLM_FAST_HZ` | `1.4` | raise cautiously — cost and latency scale linearly |
| `DEMO_MODE` | `true` | shorter timeouts, simulated workers available, polished copy |
| `WORLD_MODE` | `simulation` | `live` \| `assisted` \| `simulation` |
| `CAMERA_INDEX` | `0` | falls back to simulation if the device cannot be opened |
| `CALLWRIGHT_API_KEY` | — | escalation logs an event instead of dialing |
| `CALLWRIGHT_BASE_URL` | `https://api.voygr.tech` | — |
| `ESCALATION_PHONE` | — | required for a live call |

---

## Running degraded (read this before the demo)

| What broke | What HIVE does | What you do |
| --- | --- | --- |
| No API key / LLM down | Template planner, identical graph | Nothing. Say "plan compiled from the operations template library." |
| Camera missing/denied | Auto-switch to simulation, banner shows mode | Nothing. Drag objects in the world view. |
| VLM endpoint down/slow | One `warn` event, then silence. CV tracker carries verification. | Nothing. Semantics degrade, coordination doesn't. |
| Vision misreads an object | Assisted mode: click the true position on the feed | Click it. Logs "host-assisted observation." |
| Wi-Fi drops a phone | Worker marked disconnected, action auto-reassigned | Nothing — *this is a feature, let it happen* |
| Everything is on fire | Advanced Controls → Load known-good graph → step actions manually | Present the graph and the recovery story |

`make demo` with zero network, zero camera, zero phones still runs the entire flagship sequence
end to end. Verified by `backend/tests/test_e2e_flagship.py`.

---

## Repository layout

```
hive/
├── backend/app/
│   ├── main.py config.py models.py state.py websocket_manager.py
│   ├── orchestrator.py scheduler.py verifier.py recovery.py
│   ├── planner/     base.py llm_planner.py template_planner.py validator.py prompts.py
│   ├── vision/      camera.py color_tracker.py world_model.py calibration.py
│   ├── integrations/voygr.py
│   └── demo/        scenarios.py simulator.py
├── frontend/src/
│   ├── routes/      Host.tsx Join.tsx Worker.tsx
│   ├── components/  TaskGraph WorldView WorkerGrid EventTimeline AdvancedControls …
│   ├── hooks/       useHiveSocket useSpeech useHaptics
│   └── types/hive.ts
├── docs/CONTRACTS.md      ← the shared spine, read this first
├── Ojas.md Zechariah.md David.md Steven.md Nikki.md   ← per-person handoffs
└── Makefile .env.example
```

## Team

| Person | Owns | Handoff |
| --- | --- | --- |
| **Ojas** | Intelligence, orchestration loop, perception, demo direction | `OJAS.md` |
| **Steven** | Interface — host command center, deviation choreography, phone client | `STEVEN.md` |
| **Zechariah** | Planning, grounding, validation, capability-aware scheduling | `ZECHARIAH.md` |

Start with `docs/CONTRACTS.md`, then your own handoff. **`git pull --rebase origin main` before every session.**

---

## Where this goes

The tabletop demo is deliberately small so the architecture is visible. Nothing in the core loop
assumes humans:

- **Perception** is an interface. Today: one webcam and HSV color tracking. Tomorrow: existing CCTV,
  RTSP fleets, badge scans, RFID, WMS events, telematics. The world model does not care.
- **Actuators** are an interface. Today: a phone that speaks an instruction. Tomorrow: an AMR
  accepting a waypoint, a PLC, a Zapier webhook, a work-order in an existing system.
- **Verification** is evidence-weighted by design, so a barcode scan, a robot's own odometry, and a
  human tapping "done" all compose into the same confidence score.

The execution layer for physical companies is the same loop at every scale: observe, plan, dispatch,
verify, recover.
