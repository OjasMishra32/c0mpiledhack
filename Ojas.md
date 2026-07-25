# Ojas — VLM Perception Intelligence, Backend Core, Orchestration & Demo Direction

> You own the spine **and the eyes that actually understand**. Four people build organs; you build
> the nervous system they plug into and the visual cortex on top of it. You're also holding the
> clicker tomorrow, so when something breaks at 2am it's your call what gets cut.
> Read `docs/CONTRACTS.md` first — you are also its maintainer.

**Files you own**

```
backend/app/perception/          ← THE VLM LAYER. Your headline work.
  ├── nim_client.py
  ├── frame_bus.py
  ├── scene_reader.py            fast tier — continuous structured scene state
  ├── event_watcher.py           semantic change detection ("someone moved X")
  ├── adjudicator.py             reasoning tier — deviations, ambiguity, hard calls
  └── fusion.py                  merges VLM semantics with Steven's CV geometry
backend/app/main.py  config.py  state.py  websocket_manager.py  orchestrator.py
backend/app/models.py            ← shared, but you are the maintainer
backend/app/demo/scenarios.py
Makefile  .env.example  scripts/dev.sh
frontend/src/hooks/useHiveSocket.ts
frontend/src/types/hive.ts       ← shared, but you are the maintainer
```

**Load-balancing note:** this is more than one person's work. Ship the orchestration core first
(H0–H4) — everyone is blocked on it — then move to perception. If you're behind at H5, hand
`websocket_manager.py` hardening to Nikki and `scenarios.py` to Zechariah. Say so early, not at 1am.

---

## 1. Why your part is the hard part

Everyone else's code is a pure function of state. The planner turns a goal into actions. The
scheduler turns actions into assignments. The verifier turns evidence into a boolean. The UI turns
state into pixels.

You own the thing that is *stateful, concurrent, and live in front of judges*. Five phones, a camera
thread, an LLM call, and a 4 Hz loop all touching the same objects. Almost every way this demo can
die is a race in your files.

So build it defensively and boringly:

- **One writer.** Only the orchestrator tick mutates `Action.status`, `Worker.status`, and locks.
  Everything else appends to an inbox queue and waits its turn.
- **One clock.** One `asyncio.Task` at 4 Hz. Not a task per action. Not a timer per timeout.
- **One event bus.** One place that assigns `seq`. Gap-free ordering is what makes the timeline
  look credible on a projector.
- **Never raise.** The tick body is wrapped in try/except. A traceback logs and the loop continues.

---

## 2. Hour-by-hour

| Block | Deliverable | Done when |
| --- | --- | --- |
| **H0–H1** | `models.py`, `hive.ts`, `config.py`, `state.py` skeleton, `make install` works | `python -c "from app.models import Action"` succeeds; `npm run build` typechecks |
| **H1–H2** | `websocket_manager.py`, `/ws`, `main.py`, `useHiveSocket.ts` | two browser tabs connect; one gets a worker identity; both see `state_snapshot` |
| **H2–H4** | Orchestrator tick, action state machine, dispatch, locks | a hardcoded 3-action graph runs to completion by tapping "Completed" on phones |
| **H4–H5** | `scenarios.py`, reset, pause, emergency stop | reset returns to a clean bootable state 10× in a row |
| **H5–H6** | **`perception/`: `nim_client` + `frame_bus` + `scene_reader`** | one JSON scene read per second off the live camera, printed to console |
| **H6–H7** | `fusion.py` + `event_watcher.py`; integrate Zechariah's planner, Nikki's recovery, Steven's tracker | semantic events appear in the timeline; deviation → replan works untouched |
| **H7–H8** | `adjudicator.py`, "Ask the feed", `test_e2e_flagship.py`, `make demo`, rehearse 5× | you can run the 90-second script without looking at the terminal |

**Status: H0–H6 are built and green.** `models · config · state · websocket_manager ·
orchestrator · host_commands · main · key_pool · attribution · perception/{nim_client,
analyzer} · vision/bridge · integrations/voygr` all exist and are integrated with the
planner, scheduler and vision workstreams. 109 tests pass with no key, no camera and no
network, under every `PYTHONHASHSEED`. What is left on this file: `adjudicator.py` wiring
into the deviation path, and "Ask the feed" on the host UI.

Get to end-of-H2 fast — everyone is blocked on the socket contract being real. **Do not start the
VLM layer before H5.** It is the most impressive part and the most tempting to start early, and it
is worth exactly nothing if the orchestration loop underneath it doesn't run.

---

## 3. `config.py`

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # ONE key for everything. NVIDIA NIM free endpoints, OpenAI-compatible API.
    # No Anthropic, no OpenAI, no second provider anywhere in this project.
    nvidia_api_key: str | None = None
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    demo_mode: bool = True
    world_mode: str = "simulation"          # live | assisted | simulation
    camera_index: int = 0
    tick_hz: float = 4.0
    default_timeout_seconds: int = 25
    demo_timeout_seconds: int = 14           # used when demo_mode
    verification_threshold: float = 0.70
    # Models — all NIM (see §8b for perception, Zechariah.md §7 for planning)
    vlm_fast_model:   str = "nvidia/nemotron-nano-12b-v2-vl"      # continuous scene reads
    vlm_reason_model: str = "nvidia/cosmos3-nano-reasoner"        # physical-world reasoning
    planner_model:    str = "nvidia/nemotron-3-super-120b-a12b"   # task-graph compilation
    vlm_fast_hz: float = 1.4
    vlm_enabled: bool = True
    callwright_api_key: str = "pk_live_7f130e9d22a7480b8816ec0033cf4de7"
    callwright_base_url: str = "https://api.voygr.tech"
    escalation_phone: str | None = None
    port: int = 8000
    class Config:
        env_file = ".env"
        env_prefix = ""

settings = Settings()
```

Add a `lan_ip()` helper here — everyone needs it and nobody should reimplement it:

```python
def lan_ip() -> str:
    """Best-effort local network IP. Does not actually send a packet."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))       # no traffic; just picks the default route
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()
```

That IP goes into `/api/join-info` and therefore into David's QR code.

---

## 4. `state.py` — the single source of truth

```python
@dataclass
class HiveState:
    mode: WorldMode
    goal: Goal | None
    actions: dict[str, Action]
    workers: dict[str, Worker]          # always 5 keys, worker_a..worker_e
    world: WorldState
    locks: dict[str, str]               # "object:yellow" -> action_id
    events: deque[Event]                # maxlen=500
    execution_status: str               # idle | planning | executing | paused | completed | emergency
    metrics: RunMetrics
    _seq: int
    _lock: asyncio.Lock
```

### Rules

**`emit()` is the only place `seq` increments.**

```python
async def emit(self, type: str, message: str, *, severity="info", actor="hive", **meta) -> Event:
    async with self._lock:
        self._seq += 1
        ev = Event(id=f"evt_{self._seq:06d}", seq=self._seq, timestamp=now_iso(),
                   type=type, severity=severity, actor=actor, message=message, metadata=meta)
        self.events.append(ev)
    await ws.broadcast("event", ev.model_dump())
    return ev
```

**Write the event copy like a mission-control announcement, not a log line.** The timeline is
projected on a screen. Judges read it. Compare:

- ✗ `action a4 status -> verified (conf 0.84)`
- ✓ `Handheld scanner confirmed at Pack Station. Vision + worker agreement: 84%.`

Keep the structured data in `metadata`; keep the prose in `message`.

**Reset** rebuilds `HiveState` from a scenario without dropping sockets:

```python
async def reset(self, scenario_id: str = "incident_stabilization"):
    scenario = SCENARIOS[scenario_id]
    self.goal = None
    self.actions = {}
    self.locks = {}
    self.world = scenario.build_world()
    self.execution_status = "idle"
    self.metrics = RunMetrics()
    for w in self.workers.values():
        w.status = "ready" if w.connected else "disconnected"
        w.available = True; w.current_action_id = None; w.assignment_count = 0
    self.events.clear(); self._seq = 0
    await ws.broadcast_snapshot()
    await self.emit("system_reset", "HIVE reset. Collective standing by.", severity="info")
```

Note it keeps `connected`. A reset must never make five people re-scan a QR code on stage.

### RunMetrics — the closing slide writes itself

Track from the first dispatch: `actions_total`, `actions_verified`, `parallel_peak`,
`recoveries`, `reassignments`, `deviations`, `avg_confidence`, `worker_idle_seconds`,
`started_at`, `completed_at`. David renders these in the completion panel. Compute
`parallel_peak` as the max number of simultaneously `executing` actions seen in any tick — it is
the single most impressive number on the screen and it costs you one line.

---

## 5. `websocket_manager.py`

```python
class WSManager:
    host_sockets: set[WebSocket]
    worker_sockets: dict[str, WebSocket]     # worker_id -> socket
    token_map: dict[str, str]                # token -> worker_id  (survives disconnect)

    async def connect_host(ws) -> None
    async def connect_worker(ws, token) -> Worker      # claims or reclaims a slot
    async def disconnect(ws) -> None
    async def send(worker_id, type, payload) -> None   # ONE worker
    async def broadcast(type, payload) -> None         # host + all workers
    async def broadcast_host(type, payload) -> None    # host only
    async def broadcast_snapshot() -> None
```

### Slot claiming (this is the reliability requirement everyone forgets)

```python
async def connect_worker(self, ws, token: str | None) -> Worker:
    # 1. Known token → same slot back. Refresh-safe.
    if token and token in self.token_map:
        wid = self.token_map[token]
    else:
        # 2. First free slot: prefer never-claimed, then disconnected.
        wid = next((w.id for w in state.workers.values() if not w.connected and w.session_token is None), None) \
           or next((w.id for w in state.workers.values() if not w.connected), None)
        if wid is None:
            await ws.send_json(envelope("error_event", {"code": "hive_full",
                  "message": "All five responder slots are occupied."}))
            await ws.close(); raise SlotsFull()
        token = token or str(uuid4())
        self.token_map[token] = wid

    worker = state.workers[wid]
    # 3. Evict a stale socket on the same slot (phone reconnected before the old one timed out)
    if wid in self.worker_sockets:
        with suppress(Exception): await self.worker_sockets[wid].close()
    self.worker_sockets[wid] = ws
    worker.connected = True
    worker.session_token = token
    worker.status = "ready" if worker.status in ("disconnected", "joining") else worker.status
    await self.send(wid, "worker_assigned", {"identity": worker.public_dict(), "token": token})
    await self.broadcast_host("workers_changed", [w.public_dict() for w in state.workers.values()])
    # 4. Re-deliver a live instruction so a refresh mid-action doesn't strand them.
    if worker.current_action_id:
        a = state.actions.get(worker.current_action_id)
        if a and a.instruction: await self.send(wid, "instruction_created", a.instruction.model_dump())
    return worker
```

`Worker.public_dict()` strips `session_token`. Use it everywhere you serialize a worker outward.
The only place the token is sent is the private `worker_assigned` message on that worker's socket.

### Broadcast throttle

`world_state_changed` fires at Steven's vision rate (~10 Hz). Do not push 10 full world payloads a
second to six clients. Coalesce: set a dirty flag, and let the 4 Hz tick flush it.

```python
def mark_world_dirty(self): self._world_dirty = True
# in tick: if self._world_dirty: await broadcast("world_state_changed", world); self._world_dirty = False
```

### Disconnect

Do **not** immediately mark unavailable — Wi-Fi blips constantly at hackathons. Mark
`connected=False`, record `last_seen_at`, and let the tick decide: if a disconnected worker holds an
action for >8 seconds, *then* trigger reassignment through Nikki's recovery engine.

---

## 6. `orchestrator.py` — the loop

```python
async def run_forever():
    interval = 1.0 / settings.tick_hz
    while True:
        t0 = time.perf_counter()
        try:
            await tick()
        except Exception:
            log.exception("tick failed")
            await state.emit("system_warning", "Internal exception contained. Coordination continuing.",
                             severity="warn")
        await asyncio.sleep(max(0.0, interval - (time.perf_counter() - t0)))
```

That try/except is not optional. It is the difference between a hiccup and a dead demo.

### `tick()` — exact order, do not reorder

```python
async def tick():
    if state.execution_status in ("idle", "paused", "completed", "emergency"):
        await flush_broadcasts(); return

    drain_inbox()          # 1. apply queued worker/host messages (single-threaded, ordered)
    world_model.refresh()  # 2. pull latest observation from Steven's tracker
    verifier.evaluate()    # 3. score predicates for actions awaiting_verification
    complete_actions()     # 4. verified -> release locks, free worker, mark completed_at
    unlock_dependents()    # 5. queued -> available where all deps verified
    detect_deviations()    # 6. Nikki: regressions + wrong-object + missing-object
    detect_timeouts()      # 7. dispatched/executing past timeout_seconds
    run_recovery()         # 8. Nikki: consume deviation/timeout queue -> RecoveryPlan
    assign_actions()       # 9. Zechariah: score workers for each available action, respecting locks
    dispatch()             # 10. assigned -> build Instruction -> send to that worker only
    check_goal()           # 11. all success predicates satisfied -> goal_completed + report
    await flush_broadcasts()  # 12. one coalesced push per tick
```

Steps 3–11 are **synchronous pure-ish functions**. Only 1 and 12 touch I/O. That means you can call
`tick()` directly in a pytest without an event loop dance, which is how the E2E test works.

### The inbox

Worker and host messages land in `state.inbox: deque` from the socket handler. `drain_inbox()`
processes them at the top of the tick. This is your race-condition answer: three workers tapping
"Completed" at the same instant become three ordered inbox entries, not three concurrent mutations.

```python
def drain_inbox():
    while state.inbox:
        msg = state.inbox.popleft()
        handler = HANDLERS.get(msg.type)
        if not handler: continue
        try: handler(msg)
        except Exception: log.exception("handler %s", msg.type)
```

### Dispatch — idempotency

```python
def dispatch():
    for a in actions_with_status("assigned"):
        w = state.workers[a.assigned_worker_id]
        a.attempt += 1
        a.instruction = build_instruction(a, attempt=a.attempt)   # id = f"instr_{a.id}_{a.attempt}"
        a.status = "dispatched"; a.dispatched_at = now()
        w.status = "assigned"; w.current_action_id = a.id
        queue_send(w.id, "instruction_created", a.instruction)
        emit_soon("instruction_created", f"{w.callsign}: {a.instruction.display_text}", actor="hive")
```

The `attempt` counter is what makes instruction ids unique per re-issue, which is what makes Nikki's
speech-once logic work. Do not drop it.

### Duplicate completion guard

A worker double-taps "Completed". Or taps it, the socket blips, and the retry arrives.

```python
def on_worker_completed(msg):
    a = state.actions.get(msg.action_id)
    if not a: return
    if a.status in ("verified", "cancelled", "failed"): return          # already resolved
    if a.assigned_worker_id != msg.worker_id: return                    # not yours
    if any(e.kind == "worker_report" for e in a.evidence): return       # already counted
    a.evidence.append(Evidence(kind="worker_report", confidence=msg.confidence or 1.0, weight=0.30))
    a.status = "awaiting_verification"
```

Three guards, four lines. Every one of them will fire tomorrow.

---

## 7. `main.py`

```python
app = FastAPI(title="HIVE")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    await state.reset("incident_stabilization")
    app.state.tick = asyncio.create_task(orchestrator.run_forever())
    app.state.vision = asyncio.create_task(vision.run_forever())     # no-ops in simulation
    if settings.demo_mode: log.info("DEMO MODE — join at http://%s:5173/join", lan_ip())

@app.websocket("/ws")
async def ws_endpoint(sock: WebSocket, role: str = "host", token: str | None = None):
    await sock.accept()
    worker = None
    try:
        if role == "worker":
            worker = await ws.connect_worker(sock, token)
        else:
            await ws.connect_host(sock)
        await ws.send_snapshot(sock)
        while True:
            raw = await sock.receive_json()
            state.inbox.append(InboundMessage(
                type=raw["type"], payload=raw.get("payload", {}),
                worker_id=worker.id if worker else None,
                role=role))
    except WebSocketDisconnect:
        pass
    finally:
        await ws.disconnect(sock)
```

**Serve the built frontend from FastAPI too.** In production-ish mode, mount
`frontend/dist` at `/`. It means phones can hit `http://<ip>:8000` and you have one port to explain
instead of two. Keep Vite for dev.

```python
if Path("frontend/dist").exists():
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="ui")
```

Mount it **last**, after all `/api` and `/ws` routes, or it swallows them.

---

## 8. `demo/scenarios.py`

**Read `docs/SCENARIOS.md` first — it has the full definitions.** Your job here is the loader, the
dataclass, and keeping the flagship's `known_good_graph` correct.

A scenario is a dataclass: `id`, `title`, `subtitle`, `goal_text`, `zones`, `objects`,
`worker_roles`, `worker_reachability`, `success_predicates`, `known_good_graph`,
`recommended_failure`, `expected_recovery`, `comms_profile`, `lexicon`.

| id | Title | Point |
| --- | --- | --- |
| `warehouse_fulfillment` | **FLAGSHIP — the live demo.** Expedited order + parallel restock | scanner gates packing; SKU-2245 contended |
| `campus_emergency` | Vision use case, **video only, simulation mode** | silent comms, individual routing, headcount rollup |
| `incident_stabilization` | Disaster response | strong alternate; same graph shape |
| `resource_sort` | Everything to its matching zone | pure parallelism, all 5 at once |
| `human_relay` | One item through all five workers | pure serial chain |

**Zone ids are `z1`–`z4` + `field` in every scenario.** Only labels change. This is what preserves
Steven's camera calibration across a scenario switch — do not let anyone "helpfully" rename them.

**Write the flagship's `known_good_graph` by hand and keep it correct.** It is the parachute. If the
LLM is down *and* the template planner has a bug at 11pm, you click one button in Advanced Controls
and the demo still runs.

The warehouse flagship graph:

```
a1  move red    → z2 (Pack)    priority 100, no deps    ┐
a2  move blue   → z2 (Pack)    priority 90,  no deps    ├ 4-wide parallel opening
a3  move yellow → z2 (Pack)    priority 85,  no deps    │  ← the scanner, gates everything
a4  move orange → z4 (Aisle B) priority 60,  no deps    ┘  ← restock, fully independent
a5  hold yellow steady          (deps a3)   locks object:yellow
a6  move green  → z2            (deps a3)   materials follow the scanner
a7  release yellow              (deps a5, a6)
a8  inspect Pack Station        (deps a1, a2, a6, a7)
a9  move blue   → z3 (Aisle A)  (deps a8)   ← contention: shared SKU re-tasked
a10 inspect Pick Aisle B        (deps a4)
a11 final verification          (deps a8, a9, a10)
```

Point out **a9** to judges. One unit of SKU-2245, two orders need it. HIVE sequences the conflict
rather than failing on it — scheduling under scarcity, visible right in the graph.

**Scenario switching is a demo weapon.** The dropdown sends `host_compile_goal` with a `scenario_id`
and triggers reset + reload. Every zone, object, worker role, and headline re-labels in ~2 seconds.
Rehearse it. Done cleanly it's the strongest architectural argument we have:

> "Same webcam. Same five people. Same code. I just told it it's a different operation."

---

## 8b. The VLM perception layer — HIVE's visual cortex

> **Premise:** classical CV tells you *where a colored blob is*. It cannot tell you that a person
> reached across the table, that two objects are stacked rather than adjacent, that a box is open,
> that someone is holding something, or that the scene now looks wrong. That understanding is what
> makes HIVE feel like an intelligence watching a space rather than a color threshold with a UI.
>
> So: **OpenCV for geometry and latency. A VLM for meaning and judgment.** Both run. Neither is a
> single point of failure. Steven owns the first; you own the second; they meet in `fusion.py`.

### Model selection (NVIDIA NIM)

Two tiers, both from the NIM catalog, both free-endpoint:

| Tier | Model | Runs at | Job |
| --- | --- | --- | --- |
| **Fast** | `nvidia/nemotron-nano-12b-v2-vl` | 1–2 Hz continuous | structured scene state from the live frame: objects, attributes, spatial relations, people, activity. Multi-image + video understanding, small enough to be genuinely responsive. |
| **Reasoning** | `nvidia/cosmos3-nano-reasoner` | on-demand only | the hard calls: *did the world actually diverge from the plan?*, stacking/occlusion judgments, ambiguity adjudication, after-action explanation. Purpose-built for **structured reasoning about the physical world on video or images** — which is exactly our problem statement. |

Why this pair: Nemotron Nano VL is the throughput workhorse (12B VL, multi-image/video, fast enough
for a live loop). Cosmos-3 Nano Reasoner is a *physical-world* reasoner, not a general chat VLM —
when we claim "HIVE understands the physical world," we should be running the model that was built
for that claim. Fallbacks if either endpoint misbehaves: `llama-3.1-nemotron-nano-vl-8b-v1` (faster,
weaker) or `minimax-m3` / `kimi-k2.6` (stronger, slower).

### `nim_client.py`

NIM exposes an **OpenAI-compatible** API, so this is short. Verify the exact base path and model ids
against the catalog page before you build — do not trust these from memory:

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(base_url="https://integrate.api.nvidia.com/v1",
                     api_key=settings.nvidia_api_key)

async def vlm(model: str, prompt: str, jpeg: bytes, *, timeout: float, max_tokens: int = 700) -> str:
    b64 = base64.b64encode(jpeg).decode()
    return (await asyncio.wait_for(client.chat.completions.create(
        model=model, max_tokens=max_tokens, temperature=0.1,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}]), timeout=timeout)).choices[0].message.content
```

**Non-negotiables, all of which will bite you otherwise:**

- **Single in-flight request per tier.** If a call is outstanding, *drop the frame*. Never queue.
  A backlog turns a 1 Hz loop into a 20-second-stale hallucination machine.
- **Hard timeouts:** fast tier 2.5s, reasoning tier 8s. On timeout, log, keep the last good read,
  continue. Never await a VLM inside the orchestrator tick.
- **Downscale before encoding.** 640×360, JPEG quality 70 — about 40–60 KB. Full-res frames cost
  latency for zero accuracy on our scene.
- **Cost/rate discipline.** 1 Hz for ~3 minutes of demo is ~180 calls. Fine. 10 Hz is not. Put the
  rate in config and default it low.

### `scene_reader.py` — the fast tier

One call, one JSON object, every ~700ms. Force structure hard and repair leniently.

```python
SCENE_PROMPT = """You are a perception module for a physical operations system watching a work
surface through a fixed overhead camera.

Report ONLY what you can see right now. Do not infer intent. Do not guess object purposes.

Return STRICT JSON, no prose, no markdown fence:
{
  "objects": [
    {"ref": "<short stable description, e.g. 'red cup'>",
     "color": "<basic color word>",
     "kind": "<concrete noun: cup, box, folder, tool, unknown>",
     "position": {"x": <0-1 left→right>, "y": <0-1 top→bottom>},
     "held": <true if a hand is touching or holding it>,
     "on_top_of": "<ref of the object beneath it, or null>",
     "occluded": <bool>}
  ],
  "people": [{"side": "north|south|east|west", "reaching": <bool>, "toward": "<object ref or null>"}],
  "activity": "<one short sentence describing what is happening, or 'no activity'>",
  "anomalies": ["<anything unexpected: spilled, knocked over, obstructed, new item>"]
}"""
```

Notes that matter:

- **`held` and `on_top_of` are the payoff.** These are precisely the predicates OpenCV approximates
  badly (`docs/CONTRACTS.md` §12 admits `object_stacked_on` is inferred). A VLM answers them
  directly, and the verifier can weight that evidence honestly.
- **`people` + `reaching` is the surveillance capability** — HIVE seeing that a worker is physically
  reaching toward the wrong object, *before* the object moves, is a genuinely striking demo moment.
- **`ref` is a description, not an id.** Reconciling refs → `obj_N` is `fusion.py`'s job. Never let
  the VLM invent ids; models are bad at maintaining them and it corrupts state silently.
- Parse with a repair pass: strip fences, find the outermost `{...}`, `json.loads`, and on failure
  keep the previous scene and emit a `debug` event. **Never raise into the loop.**

### `event_watcher.py` — live semantic narration

Diff consecutive scene reads and emit human-readable events. This is what makes the feed feel
*alive* rather than polled — the overshoot-style "the camera is narrating itself" effect:

| Change | Event |
| --- | --- |
| object appears | `New item entered the workspace: blue folder.` |
| object disappears | `Red cup no longer visible.` |
| `held` false→true | `Worker at the west side picked up the red cup.` |
| `on_top_of` changes | `Blue box placed on the red box.` |
| position jump | `Yellow item moved from Pack Station to Pick Aisle A.` |
| `anomalies` non-empty | `Anomaly: green cup knocked over.` (severity `warn`) |
| `reaching` toward a locked object | `Worker reaching toward a locked resource.` (severity `warn`) |

Debounce two consecutive reads before emitting, same discipline as everywhere else. One flaky read
must never produce a headline.

### `adjudicator.py` — the reasoning tier

Called **only** on: scene scan, deviation candidate, grounding ambiguity, and goal completion.
Never on a timer.

The highest-value use is **deviation adjudication**. Instead of trusting a color centroid crossing a
rectangle, ask a physical-world reasoner to confirm before you put a red banner on the projector:

```python
ADJUDICATE = """Expected state: {expected}
A tracker believes this changed: {observed}
Looking at this frame, is that correct?

Return STRICT JSON:
{{"agrees": bool, "confidence": 0-1, "what_actually_happened": "<one sentence>",
  "recommended": "proceed|replan|ask_operator"}}"""
```

`agrees=false` suppresses the alarm; `agrees=true` fires it *and* hands David's overlay a
one-sentence natural-language explanation of what happened, which is far better copy than a
templated string. This single integration is the difference between "our color tracker flickered"
and "HIVE understood that someone moved the scanner to the wrong aisle."

### `fusion.py` — CV geometry + VLM semantics

Reconcile the VLM's `ref` strings to Steven's tracked `obj_N` ids by nearest-position + color match
(threshold 0.15 normalized distance). Then:

| Field | Authority | Why |
| --- | --- | --- |
| `position`, `zone` | **CV** | 10 Hz, smooth, precise, no latency |
| `visible`, `confidence` | CV | continuous |
| `semantic_label`, `kind` | **VLM** | "red plastic cup", not "red round object" |
| `held_by` | **VLM** | CV genuinely cannot do this |
| `stacked_on` | **VLM** | CV only approximates it |
| anomalies, activity, people | **VLM** | CV has no concept of these |

An unmatched VLM object still registers (with VLM-estimated position, confidence 0.6) — that's how
HIVE notices something the color tracker missed entirely. Mark `source: "vlm"` so the UI can show
where the belief came from; David should render VLM-sourced facts with the `--think` accent.

### Degradation ladder — rehearse each rung

| State | Behavior |
| --- | --- |
| Both tiers healthy | full semantic intelligence, live narration, adjudicated deviations |
| Reasoning tier down | fast tier + deterministic recovery. No visible difference to the audience. |
| Both VLM tiers down | pure OpenCV. Semantics degrade to descriptors. **Demo still completes.** |
| No `NVIDIA_API_KEY` | VLM layer never starts; a neutral `PERCEPTION: CV` chip shows in the header |
| Camera down | simulation mode; VLM layer idles |

Emit **one** `warn` event when a tier goes down, then stay quiet. Never spam a failing endpoint into
the timeline — the audience reads that as the system being broken, when it's the system coping.

### The demo beat: "Ask the feed"

A small input on the host: the presenter types a question about the live scene and the reasoning
tier answers from the current frame.

> *"Is anyone holding the scanner right now?"* → **"Yes — the worker on the east side is holding it
> above the pack station. It has not been set down."**

Ten minutes of work, uses machinery you already built, and it is the single clearest demonstration
that HIVE genuinely perceives the space rather than replaying a script. If a judge asks "is this
real?", this is your answer — hand them the keyboard.

### Config additions

```python
nvidia_api_key: str | None = None
nim_base_url: str = "https://integrate.api.nvidia.com/v1"
vlm_fast_model: str = "nvidia/nemotron-nano-12b-v2-vl"
vlm_reason_model: str = "nvidia/cosmos3-nano-reasoner"
vlm_fast_hz: float = 1.4
vlm_fast_timeout: float = 2.5
vlm_reason_timeout: float = 8.0
vlm_enabled: bool = True
```

---

## 8c. Attribution — delegating on evidence, not just capability

> Built and wired: `backend/app/attribution.py`.

The scheduler answers **who *can* do this** — capability, reachability, current load. That
is necessary and not sufficient. Two workers can both be able and still be very different
choices. Attribution answers **who *should*** from what has actually happened in this run:

| Signal | What it means |
| --- | --- |
| `reliability` | when they said "done", did the world agree? |
| `mean_seconds` | rolling dispatch→verified over their last five actions |
| `zones` / `objects` | where and what they have already worked successfully |
| `last_finished_at` | recency, so work stays visibly spread across all five |

Nobody is rated ahead of time. HIVE builds the picture from the run and can defend every
number it uses — which is the whole point, because the explanation goes on the projector:

> **DELTA selected on record:** every report so far has verified, has worked this area 2×.
> CHARLIE scored better on position alone.

**The ordering rule matters and is deliberate:** capability decides who is *eligible*;
attribution only re-orders candidates the scheduler already judged viable. A worker who
fails once slides down the list — they are never exiled from the demo, which would be both
unfair and boring to watch.

Surfaced as `contributions` in `state_snapshot` and in the `goal_completed` payload, plus a
one-sentence after-action line built from what actually happened. Cleared by `reset()`.

---

## 9. Integration seams (how you avoid merge hell)

Define these interfaces in H0 and hand them out. Everyone codes against the signature, not the
implementation, and stubs land immediately so the tick is never blocked.

```python
# scheduler.py — Zechariah
def score_workers(action: Action, state: HiveState) -> list[Assignment]:
    """Returns candidates sorted best-first. Assignment = (worker_id, score, reason, factors)."""

# planner/base.py — Zechariah
class Planner(Protocol):
    async def compile(self, goal_text: str, ctx: PlanContext) -> PlanResult: ...

# verifier.py — Nikki
def evaluate(action: Action, state: HiveState) -> VerificationResult:
    """Pure. Returns score, verified: bool, evidence list. Mutates nothing."""

# recovery.py — Nikki
def plan_recovery(trigger: DeviationTrigger, state: HiveState) -> RecoveryPlan:
    """Returns actions to cancel, actions to insert, workers to free, and a narration string."""

# vision/world_model.py — Steven
def refresh(state: HiveState) -> None:
    """Updates scene objects in place from CV geometry (camera/sim/host overrides). Never blocks."""

# perception/fusion.py — Ojas
def apply_vlm(scene_read: SceneRead, state: HiveState) -> list[Event]:
    """Overlays VLM semantics onto CV-tracked objects. Returns semantic events to emit.
       Called from the tick with the LAST COMPLETED read — never awaits a request."""
```

The last line is the whole contract between the two perception systems: **Steven's tracker is
synchronous and always current; your VLM is asynchronous and always slightly stale.** The tick reads
whatever the VLM last finished. It never waits.

**Every one of these ships as a working stub in your first commit.** `score_workers` returns the
first idle worker. `evaluate` returns verified-if-worker-said-so. `plan_recovery` returns
reassign-to-anyone. `refresh` is a no-op. Now the loop runs end to end in hour two, and each person
replaces their stub in place without a single merge conflict.

---

## 10. Tests you own — `backend/tests/`

`test_e2e_flagship.py` is the one that matters. It is also your regression alarm for everyone else's
changes.

```python
@pytest.mark.asyncio
async def test_flagship_completes_with_worker_failure():
    await state.reset("incident_stabilization")
    for wid in ["worker_a","worker_b","worker_c","worker_d","worker_e"]:
        connect_fake_worker(wid)

    await handle_host({"type": "host_compile_goal", "payload": {"scenario_id": "incident_stabilization"}})
    assert state.goal.status == "compiled"
    assert len(state.actions) >= 8

    await handle_host({"type": "host_start_execution", "payload": {}})
    await run_ticks(4)
    parallel = [a for a in state.actions.values() if a.status in ("dispatched","executing")]
    assert len(parallel) >= 3, "opening wave must be parallel"

    # complete the first two
    for a in parallel[:2]:
        complete(a.assigned_worker_id, a.id)
    await run_ticks(4)

    # kill a worker mid-action
    victim = next(a for a in state.actions.values() if a.status == "dispatched")
    await handle_host({"type": "host_set_worker",
                       "payload": {"worker_id": victim.assigned_worker_id, "available": False}})
    await run_ticks(6)
    assert victim.assigned_worker_id != previous_worker, "action must be reassigned"
    assert victim.status in ("assigned","dispatched")
    assert any(e.type == "action_reassigned" for e in state.events)

    # drive to completion
    await drive_to_completion(max_ticks=200)
    assert state.goal.status == "completed"
    assert state.metrics.reassignments >= 1
```

Helpers `run_ticks`, `complete`, `drive_to_completion` live in `tests/conftest.py`. `drive_to_completion`
loops: tick, then auto-complete every dispatched action. Cap the iterations so a bug fails the test
instead of hanging CI.

Other tests you own: reset idempotency (reset 10×, state identical), event seq monotonicity under
concurrent emits, duplicate `worker_completed` counted once, worker reconnect keeps the slot,
sixth connection rejected cleanly.

---

## 11. Demo direction — you are holding the clicker

### The 90-second script

| t | Screen | You say |
| --- | --- | --- |
| 0:00 | Host, 5 nodes pulsing | "Five workers on a floor. None of them knows the full plan. Each one only ever sees their next physical task." |
| 0:08 | Type the objective, hit Compile | "One sentence in." |
| 0:14 | DAG explodes into view | "Eleven actions. Four run in parallel. Two contend for the same SKU — there's one unit and two orders need it." |
| 0:25 | Instructions land, phones speak | "Private instructions. Nobody is coordinating verbally. Nobody sees anyone else's task." |
| 0:40 | Zones filling, confidence rising | "It's confirming against the camera, not taking their word for it." |
| 0:50 | **Judge moves the scanner** | "You're the floor. Change something — move any item." |
| 0:55 | Red banner: FLOOR STATE DEVIATION | *(say nothing — let the screen talk for 3 seconds)* |
| 1:05 | REPLANNING → reassignment | "It froze only the packing workflow. Picking and restock never stopped." |
| 1:20 | ORDER FULFILLED + metrics | "One deviation. One live recovery. Zero conflicting assignments. Nobody restarted anything." |
| 1:30 | Closing | "Today it coordinated five people around a table. The same code runs a warehouse, a hospital, an airport turnaround — or a campus evacuation, where every person needs a different instruction, silently, at the same second." |

### The scenario-switch beat (optional, ~15s, if judges are engaged)

After completion, pick `Campus Emergency` from the preset dropdown and let the whole UI re-label.

> "Same webcam, same five phones, same code. I just told it it's a different operation. Now the
> instructions are silent — no audio, no vibration, minimum brightness — because a speaking phone in
> a lockdown is a hazard. Wing A goes north, Wing B goes west, the gym holds because its route
> crosses the affected area. A PA announcement can't say three different things at once."

Then the honest line, which lands better than any claim:

> "It doesn't replace emergency dispatch and it doesn't make life-safety calls. It's how one
> coordinator's decisions reach forty people at once, each one different, in under a second."

The full campus scenario and video script are in `docs/SCENARIOS.md` §2. **Do not run the campus
scenario live on the tabletop** — it's a simulation-mode floor-plan scenario for the video.

### Rules for yourself

1. **Never narrate the failure before the screen shows it.** Three seconds of silence while the
   banner animates is worth more than any sentence you could say.
2. **Rehearse the recovery five times.** It is the only moment that matters. Everything else is
   setup for it.
3. **Have a spare judge disruption ready.** If they hesitate, say "or I can pull a responder
   offline" and hit Disable Worker C. Same story, fully deterministic.
4. **Pre-flight, memorized:** camera framed and calibrated → all five phones joined and on
   `/worker` → mode banner correct → `make demo` fresh → one full silent rehearsal → Reset →
   laptop plugged in, sleep disabled, notifications off.
5. **If the room's Wi-Fi has client isolation**, phones cannot reach the laptop. Test at the venue,
   early. Fallback: laptop joins a phone hotspot. Second fallback: simulation mode with simulated
   workers and you narrate — it still shows the whole intelligence stack.

### The cut list, in order

Under time pressure, drop in this order and do not agonize:
"Ask the feed" → VLM reasoning tier → LLM planner → Voygr call → AR overlay → live camera entirely
(fall back to simulation) → scene correction UI.

**Never cut:** the recovery moment, private instructions, the DAG, the event timeline.
Those four *are* the product.

Note that the **VLM fast tier survives further down the list than the camera itself** — if vision is
degraded but the VLM is up, run the VLM on the camera feed and let CV go. If both are down,
simulation mode still demonstrates the entire intelligence architecture. Know which rung you're on
before you walk up, and never explain the rung to the audience.
