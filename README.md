# HIVE

**An AI operating system for physical work.**

You give HIVE an objective in one sentence. It looks at the space through a camera, works
out what needs to happen, and sends each person a single private instruction. It watches
what actually happens, and when reality stops matching the plan, it repairs the plan
around the problem instead of starting over.

---

## The problem

Physical operations don't fail because nobody knows what to do. They fail because **the
plan goes stale faster than people can re-coordinate.**

A worker sees only their own task. A supervisor can't watch everything at once. One person
missing, one item in the wrong place, one blocked aisle — and the delay cascades silently
through everything downstream until somebody notices.

HIVE is the layer that notices, continuously, and reacts in under a second.

---

## What it actually does

```
  camera  ──▶  discovers what's on the table (no preset list of objects)
                          │
  "one sentence" ─────────┤  binds your words to the objects it can actually see
                          ▼
              validated task graph  ──  what depends on what, what can run at once
                          │
                          ▼
              capability-aware scheduling  ──  who can reach it, who's free, who's proven
                          │
                          ▼
        five phones, five different private instructions
                          │
                          ▼
        verification against the camera + the worker's own report
                          │
                          ▼
        deviation detected ──▶ freeze only the affected chain ──▶ reassign ──▶ resume
```

**Nothing about the objects is hardcoded.** There is no list of "red = medical kit" in this
codebase. The camera discovers whatever is physically there, measures its colour, size and
shape, and gives it an id. Your sentence is then resolved against *that* — so you can swap
an object, rescan, and it still works. There is a test that fails if anyone reintroduces a
hardcoded object.

---

## The five things that make it more than a task list

**1. Instructions are private.**
Each phone receives only its own next action — never the objective, never the plan, never
anyone else's task. Five people execute a coordinated operation without talking to each
other. Enforced server-side and covered by a test.

**2. It verifies instead of assuming.**
A worker tapping "done" is *evidence*, not proof. It's weighted (0.30) against what the
camera sees (0.60) and what a vision-language model reports (0.55). An action verifies when
the combined confidence clears 0.70 — so a person who says "done" without doing it fails
verification. The UI shows the honest number, not a fake 100%.

**3. It repairs instead of restarting.**
When something diverges, HIVE freezes *only* the dependency chain that touches the affected
resource. Everything else keeps running. It inserts a recovery action, re-scores who should
take it, and resumes. Nothing restarts.

**4. It doesn't cry wolf.**
Before showing a deviation, it sends the last ~2.5 seconds of frames to a physical-reasoning
model and asks whether the item genuinely moved, or was briefly occluded by a hand, or was
misclassified. A refuted alarm is dismissed quietly. Critically, this **fails open**: if the
model is slow or unreachable, the deviation fires anyway. A hung endpoint can never swallow
a real one.

**5. It delegates on evidence, not just capability.**
The scheduler answers *who can do this* from reachability, workload and capability.
Attribution answers *who should* from what has actually happened this run: whether their
reports survived verification, how fast they are, whether they've worked that area before.
Every assignment carries the sentence explaining itself:

> **CHARLIE selected:** closest to the scanner, currently idle, no conflicting activity in
> Pack Station. DELTA was mid-task.

That's generated from explicit scoring factors — not a model's hidden reasoning.

---

## Architecture

**Perception is two independent sensors over one camera feed.** OpenCV runs at 10–20 Hz and
owns geometry and object identity. A vision-language model runs only on meaningful events
and owns meaning — what things are, who is holding what, what just changed. Neither is a
single point of failure.

**The model thinks occasionally; deterministic code controls continuously.** The task
compiler runs locally and instantly; a hosted model runs concurrently and only ever
*upgrades* the plan, never gates it. A 4 Hz state machine owns every status transition, with
one writer and one event sequence, so five phones responding simultaneously can't race.

**It degrades on purpose.** No API key → the local compiler runs the whole demo. No camera →
simulation mode, fully labeled. Phone drops → the work is reassigned automatically, and
that's a feature we demonstrate rather than hide.

Stack: Python · FastAPI · WebSockets · OpenCV · NetworkX · NVIDIA NIM · React · TypeScript ·
Three.js

---

## Where this goes

The tabletop is deliberately small so the architecture is visible. Nothing in the core loop
assumes humans or webcams:

- **Perception is an interface.** Today a webcam and colour tracking. Tomorrow existing CCTV,
  RFID, badge scans, WMS events — the world model consumes normalized detections, not frames.
- **Actuation is an interface.** Today a phone that shows an instruction. Tomorrow an AMR
  accepting a waypoint, a PLC, a work order in an existing system.
- **Verification is evidence-weighted by design**, so a barcode scan, a robot's odometry and
  a human tapping "done" all compose into the same confidence score.

Warehouses, hospital logistics, airport turnarounds, construction, disaster response — and
building evacuation, where forty people each need a different instruction, silently, in the
same second.

---

## Running it

```bash
make install
make dev          # or: make live   (camera on)
make ip           # prints the URL for phones
make test         # 140 tests
```

Host: `localhost:5173/host` · Phones: the URL from `make ip`
Then: **Scan scene → type an objective → Compile → Start execution.**

Runs with no API key, no camera, and no phones. See `DEMO.md` for the full runbook.

---

## Honest notes

- The demo runs in **simulation mode** when the physical scene is too visually noisy to bind
  reliably — a real, labeled mode, not a mock. The perception, planning, scheduling,
  verification and recovery paths are identical either way.
- `nvidia/cosmos-*` models return 404 on our account (a known hosted-endpoint permission
  gap), so the perception layer probes at startup and self-configures onto whatever model is
  actually reachable.
- Voice escalation via Voygr ships **disarmed** — an accidental live call during setup dials
  a real person.
