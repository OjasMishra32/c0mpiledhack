# HIVE — Demo Runbook

## 1. Commands

```bash
make install     # once, if the venv or node_modules are missing
make live        # backend :8000 + frontend :5173, camera on   ← use this on stage
make dev         # same, without forcing the camera
make demo        # simulation + simulated workers, no camera/keys/phones needed
make test        # backend pytest + frontend typecheck
make ip          # prints  join → http://<lan-ip>:5173/join
```

- Host: **http://localhost:5173/host**
- Phones: the URL from `make ip`, or scan the QR on `/host`. Same URL.
- The frontend proxies `/api` and `/ws` to :8000. **Phones only ever use the :5173 URL.**
  Never hand anyone a :8000 link.
- Slots go ALPHA → BRAVO → CHARLIE → DELTA → ECHO in join order. A refresh reclaims the
  same slot via `localStorage`.

Run order on stage: **Scan scene → type objective → Compile → Start execution.**

## 2. Setup checklist

- [ ] Laptop plugged in, sleep off, Do Not Disturb on, browser at 100% zoom.
- [ ] Laptop and all five phones on the **same network**. Conference Wi-Fi with client
      isolation silently blocks phone→laptop — use a hotspot **the laptop also joins**.
      Verify by loading the `make ip` URL on one phone before anything else.
- [ ] Camera framed: all four taped zones in frame, no glare, no hands in the shot at rest.
      Capture indices shift whenever anything is replugged; the bridge auto-scans indices
      0–3 and takes the first device that produces a frame, so don't chase `CAMERA_INDEX`.
- [ ] Five phones joined, five distinct callsigns visible on `/host`. Screens at full
      brightness, phones unlocked, auto-lock off.
- [ ] `make test` green.
- [ ] One **silent full rehearsal** end to end — scan, compile, start, judge-move, recovery,
      completion. Do the recovery beat five times.
- [ ] **Reset** (toolbar) after rehearsal. Scan scene again right before you present.
- [ ] Escalation stays disarmed unless you are deliberately demoing the call.

## 3. The 90-second script

| t | On screen | You say |
| --- | --- | --- |
| 0:00 | Five worker nodes, idle | "Five people. None of them knows the full plan — each one only ever sees their next task." |
| 0:08 | **Scan scene** → objects appear | "It has never seen this table. It's looking now." |
| 0:14 | Type the objective → **Compile** | "One sentence in." |
| 0:20 | Graph expands, layers highlight | **Read the counts off the objective bar, don't memorise them** — the scene is discovered fresh each time. Typically ~9 actions, 4 parallel, 5 stages. "Nine actions. Four run in parallel. It worked out the ordering itself — and two of these need the same item, so it sequenced them." |
| 0:30 | Instructions land on phones | "Private instructions. Nobody is coordinating out loud." |
| 0:45 | Hand the judge the scanner | "You're the floor. Change something." |
| 0:50 | **FLOOR STATE DEVIATION** banner | *(say nothing — three full seconds of silence)* |
| 1:00 | REPLANNING → retrieval reassigned | "It froze only the packing chain. Picking and restock never stopped." |
| 1:20 | Completion metrics | "One deviation. One recovery. Zero conflicting assignments. Nothing restarted." |
| 1:30 | Close | "Today it coordinated five people around a table. Same code runs a warehouse, a hospital, an airport — or a campus evacuation, where forty people each need a different instruction, silently, in the same second." |

Optional +15s if they're leaning in: pick another scenario from the preset dropdown —
"Same webcam, same five phones, same code. I just told it it's a different operation."

## 4. If something breaks

| Symptom | Do this |
| --- | --- |
| A phone drops / a worker goes quiet | **Nothing.** It auto-reassigns. Say "that phone just dropped — it already moved the work." That *is* the feature. |
| Camera won't open / frame is black | Advanced controls → **Mode → simulation**. Drag objects in the world view, or Item → Place. Story is unchanged. |
| Plan looks wrong or too small | Toolbar **Reset** → Scan scene → Compile again. Don't debug live. |
| Judge hesitates to move anything | Advanced controls → **Worker → Disable**. In-flight action reassigns with a spoken reason. Same story, zero vision dependence. |
| Model endpoint slow / spinner on compile | **Nothing.** The template compiler already ran and published; the model only upgrades in the background. Keep talking. |
| Deviation banner doesn't fire | Advanced controls → **Inject → Verification regress** (or Wrong item moved). Same code path as the real thing. |
| Total meltdown | **Reset** → Mode → **simulation** → Compile → Start → drive completion with Action → **Force verify**. Present the graph and the recovery story. |

## 5. Questions judges will ask

**"Is this hardcoded?"** No. There is no object manifest in the system. *Scan scene*
discovers whatever is on the table; grounding binds the phrases in your sentence to the ids
it actually observed. Swap an object, rescan, retype the goal — offer to let them do it.

**"Is the vision real?"** Yes. Use **Ask the feed** in the toolbar and hand them the
keyboard: "Is anyone holding the scanner right now?" It answers off a live frame burst.

**"What if the AI is down?"** The demo still runs. The template compiler produces the graph
instantly and is what you actually saw compile; the hosted model runs concurrently and only
ever replaces the plan with a better one, and never after execution starts. No key, no
network — same demo.

**"Does this work at scale / with robots?"** Perception and actuation are interfaces. The
world model consumes normalized detections, not camera frames — point it at existing CCTV,
badge scans, or WMS events. A phone is one actuator; an AMR taking a waypoint is another.
Verification is evidence-weighted, so a barcode scan and a human tapping "done" compose into
the same confidence score.

**"Who's in charge?"** The operator. HIVE routes, verifies, accounts, and escalates. Every
instruction it sends is one a coordinator could have sent — it just sends forty different
ones at once.

## 6. Facts you can state

- **129 backend tests pass** (`cd backend && .venv/bin/pytest tests -q`), plus a frontend
  typecheck, under any `PYTHONHASHSEED`.
- It runs with **no API key, no camera, and no phones** — `make demo` executes the full
  flagship sequence end to end, covered by `backend/tests/test_e2e_flagship.py`.
- **Instructions are private per phone.** Events carrying other workers' instruction text
  never go to a worker socket — enforced by `test_events_never_reach_worker_sockets`. A
  worker also cannot complete another worker's action, and never holds two live actions.
- **Deviations are adjudicated by a scene model before the banner fires.** The tracker
  raises a candidate, a frame burst goes to the reasoner, and a refuted candidate is
  dismissed instead of shown. The path **fails open**: if the model is unavailable, slow, or
  errors, the deviation fires anyway — `test_adjudication_fails_open_when_unavailable` and
  `test_adjudication_suppresses_a_refuted_deviation`.
- **Camera indices are not trusted.** If the configured device won't open, the bridge tries
  the other indices and uses the first that produces a frame; if none do, it drops to
  simulation and says so in the banner.
- **The recovery isolates.** Only the dependency chain touching the affected resource is
  paused; independent branches keep executing. Nothing restarts.
