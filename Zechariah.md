# Zechariah — Planner, Task Graph & Capability-Aware Scheduler

> You own the part judges call "the intelligence." A goal sentence becomes a validated dependency
> graph; the graph becomes assignments that are *defensibly* correct. The scheduler's explanation
> string is, line for line, the most quoted thing on the screen. Read `docs/CONTRACTS.md` first.

**Files you own**

```
backend/app/planner/base.py
backend/app/planner/grounding.py       ← binds language to what the camera actually sees
backend/app/planner/llm_planner.py
backend/app/planner/template_planner.py
backend/app/planner/validator.py
backend/app/planner/prompts.py
backend/app/scheduler.py
backend/tests/test_grounding.py  test_planner.py  test_validator.py  test_scheduler.py
```

---

## 1. Your prime directive

**Nothing about the objects is known ahead of time.**

Steven's pipeline discovers whatever is physically present and hands you `obj_1 … obj_N` with
measured descriptors (hue, sampled hex, area, shape) and optional semantic labels. There is no
manifest. The presenter types an arbitrary task about arbitrary objects, and **your job is to bind
that sentence to the real scene and produce a graph over real observed ids.**

Concretely: if someone puts three coffee cups and a stapler on the table and types *"put the red cup
in the left box and stack the other two,"* HIVE must work. A judge will test exactly this.

**Second directive: the template planner is the product; the LLM planner is the upgrade.** Build
template first, fully. Then add the LLM. If you build the LLM first you'll spend four hours on JSON
repair and have nothing when the venue Wi-Fi drops.

The host shows plan source as a neutral chip — `AI PLANNER` / `TEMPLATE` / `KNOWN-GOOD`. Template
must never look like failure; it's "compiled from the operations template library," which is what a
mature deployment would use for known workflows anyway.

---

## 2. Hour-by-hour

| Block | Deliverable |
| --- | --- |
| **H0–H1** | `base.py` interfaces + a stub returning a trivial graph over discovered ids, so Ojas's loop runs |
| **H1–H2** | `grounding.py` — descriptor-based reference resolution. **Do this early; everything depends on it.** |
| **H2–H3** | `template_planner.py` — structural templates over resolved slots |
| **H3–H4** | `validator.py` — all ten checks + auto-repair |
| **H4–H6** | `scheduler.py` — scoring, locks, parallelism, explanations |
| **H6–H7** | `llm_planner.py` + `prompts.py` + fallback path |
| **H7–H8** | Tests, tune priorities so the opening wave is visibly 4-wide |

---

## 2b. `grounding.py` — language → observed reality

This is the component that makes the "promptable" claim true. It resolves noun phrases from the
objective against the *live* scene.

```python
@dataclass
class Binding:
    phrase: str                # "the red cup"
    object_id: str | None      # obj_3
    confidence: float
    alternatives: list[str]    # other plausible ids, best-first
    basis: str                 # "color match + shape match" — shown in the UI

def resolve(phrase: str, scene: Scene) -> Binding: ...
def resolve_all(goal_text: str, scene: Scene) -> GroundingResult: ...
```

### Scoring a candidate

Score every observed object against the phrase; take the best if it clears a margin.

| Signal | Weight | How |
| --- | --- | --- |
| Color word match | 0.40 | phrase contains a color word == `descriptor.color_name`; partial credit for adjacent hues (`teal`↔`cyan`) |
| Semantic label overlap | 0.30 | token overlap with `semantic_label` ("cup", "box", "folder") |
| Shape word match | 0.15 | "round"/"square"/"tall" vs `shape_hint` and `aspect` |
| Spatial qualifier | 0.10 | "left"/"nearest"/"in the dock" vs `position` / `zone` |
| Size qualifier | 0.05 | "big"/"small" vs `area_norm` percentile within the scene |

**Ambiguity is a first-class outcome, not an error.** If the top two candidates are within 0.15,
emit `grounding_ambiguous` and let the host click the right object on the live feed. That
interaction takes two seconds and reads as the system being *careful*, not broken:

> Two red objects detected. Which one is the priority item? *(click it on the feed)*

Never silently guess between near-ties. A wrong binding produces a plan that looks confident and
does the wrong thing — far worse on stage than a two-second clarification.

### Zones from language

Same resolver over zone labels, plus: any place name in the objective with no matching zone becomes
an **unbound chip** in the host UI. The host drags it onto the feed to define it. That flow is what
lets someone type a task about a space HIVE has never seen.

### Roles

Once bound, write `object.role` = the phrase ("the priority item"). From then on, **every
instruction, event, and UI label uses the role**, so workers hear "move the priority item," not
"move obj_3." Roles are how the system's language stays human while its ids stay machine-stable.

### LLM-assisted grounding (optional upgrade)

If `NVIDIA_API_KEY` is set, one call to the planner model can resolve all phrases at once with better handling of pronouns and
relational language ("the other two", "whichever is closest to the dock"). Send the objective plus a
compact table of observed objects (id, color name, semantic label, shape, zone, position). Force a
tool call returning `{phrase: object_id}`. **Timeout 8s, fall back to descriptor scoring.** Same
discipline as everywhere else: the LLM improves the answer, it never gates it.

---

## 3. `base.py`

```python
@dataclass
class PlanContext:
    workers: list[Worker]
    scene: Scene                       # LIVE discovered objects + zones. Never a static manifest.
    bindings: GroundingResult          # phrase → object_id, resolved before planning
    supported_actions: list[str]
    constraints: list[str]
    scenario_id: str | None            # only supplies zone labels / lexicon / parachute graph

@dataclass
class PlanResult:
    actions: list[Action]
    success_predicates: list[Predicate]
    source: PlanSource                 # llm | template | demo_script
    normalized_intent: str
    notes: str                         # "11 actions · 4 parallel · 2 resource conflicts"
    warnings: list[str]                # validator repairs, shown subtly in UI

class Planner(Protocol):
    async def compile(self, goal_text: str, ctx: PlanContext) -> PlanResult: ...

async def compile_goal(goal_text: str, scene: Scene, workers: list[Worker]) -> PlanResult:
    """The only entry point Ojas calls. Owns grounding + the fallback chain."""
    if not scene.stable:
        raise NotReady("Scene still settling — rescan before compiling.")

    grounding = resolve_all(goal_text, scene)              # language → observed object ids
    if grounding.ambiguous:
        await ws.broadcast_host("grounding_ambiguous", grounding.ambiguous_payload())
        # Ojas holds the compile until the host clicks; resume with host_bind_object.
        return PlanResult.pending(grounding)

    ctx = PlanContext(workers=workers, scene=scene, bindings=grounding, ...)
    if settings.nvidia_api_key:
        try:
            result = await asyncio.wait_for(LLMPlanner().compile(goal_text, ctx), timeout=12.0)
            report = validate_and_repair(result, ctx)
            if report.ok:
                result.warnings = report.repairs
                return result
            log.warning("LLM plan rejected: %s", report.errors)
        except (asyncio.TimeoutError, Exception) as e:
            log.warning("LLM planner unavailable: %s", e)
    result = TemplatePlanner().compile_sync(goal_text, ctx)
    validate_and_repair(result, ctx)      # templates get validated too — catches your own typos
    return result
```

The 12-second timeout is deliberate. A judge will not wait longer, and neither will you.

---

## 4. `template_planner.py`

**Templates are structural shapes, not scripts.** A template says *"for each (object, destination)
pair, emit a move; then emit a verification per destination."* It never names an object. Objects
arrive from grounding; destinations arrive from the resolved zones. The same `deliver_to_zones`
template produces a warehouse plan, an incident plan, or a plan about coffee cups — the difference
is entirely in the bindings.

| Template | Triggers | Structural shape |
| --- | --- | --- |
| `deliver_to_zones` | move, deliver, bring, take, put, fulfill, stabilize, restock | N parallel moves + per-zone verify + final verify — **the workhorse** |
| `assemble_structure` | stack, tower, build, on top of | serial stack + hold + release |
| `sort_by_attribute` | sort, matching, each to its, distribute | N-way parallel, destination derived per object |
| `relay_chain` | pass, relay, through, hand off | strict serial across all available workers |
| `sequence_arrange` | arrange, order, sequence, left to right | positional placement in stated order |
| `gather` | gather, collect, bring everything, consolidate | N-way converge on one zone |

```python
def route(goal_text: str, bindings: GroundingResult) -> str:
    t = goal_text.lower()
    scores = {name: sum(2 for kw in kws if kw in t) for name, kws in TEMPLATE_KEYWORDS.items()}
    # structural signals from the bindings themselves, not from any object names
    if bindings.distinct_destinations >= 2: scores["deliver_to_zones"] += 3
    if bindings.mentions_relation("on top of"): scores["assemble_structure"] += 5
    if bindings.destination_count == 1 and bindings.object_count >= 3: scores["gather"] += 3
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "deliver_to_zones"
```

Then build the graph **from the bindings only**:

```python
def deliver_to_zones(ctx) -> list[Action]:
    actions, per_zone = [], defaultdict(list)
    for i, (obj_id, zone_id) in enumerate(ctx.bindings.deliveries, start=1):
        obj = ctx.scene.by_id(obj_id)
        a = Action(id=f"a{i}", type="place_in_zone", object_id=obj_id, target_zone=zone_id,
                   description=f"Move the {obj.display_label()} to {ctx.scene.zone_label(zone_id)}.",
                   priority=priority_for(obj, zone_id, ctx),      # from stated urgency words
                   dependencies=[], lock_targets=[f"object:{obj_id}", f"zone:{zone_id}"],
                   expected_predicates=[Predicate("object_in_zone", obj_id, zone_id)])
        actions.append(a); per_zone[zone_id].append(a.id)
    # one verification per destination, then a terminal verification over all of them
    ...
```

Never write `if object_id == "red"` anywhere. If you find yourself typing a color literal in this
file, the design has gone wrong — the color lives in `descriptor.color_name`, and the *meaning*
lives in the binding.

### Prove it responds to the text

Type a narrower objective and the graph must get smaller. "Only deliver the red item" → one move,
one verify. This single behavior kills the "it's hardcoded" suspicion faster than any explanation,
so make sure it works and **demo it deliberately** — compile once, then compile a narrower version
and let judges watch the DAG shrink.

### Template construction rules

- Every move is one atomic action. Never "pick it up and take it there."
- Stacking always emits a `hold` stabilization action on the base object with a lock, then `release`.
- Each zone gets a terminating `inspect` action whose predicates are the zone's success condition.
- The graph terminates in one `inspect` action depending on every zone inspection — the final
  verification step. It is what turns the DAG into a visible funnel on David's screen.
- Priorities come from urgency language in the objective ("expedited", "first", "priority",
  "critical") mapped onto the bound objects — not from any object's identity. This is why the
  urgent actions light
  up first, and it reads as triage to anyone watching.
- Keep it ≤ 20 actions. 11 is ideal: enough to look complex, small enough to read from ten feet.

---

## 5. `validator.py`

```python
@dataclass
class ValidationReport:
    ok: bool
    errors: list[str]      # fatal → reject the plan, fall back
    repairs: list[str]     # fixed automatically → surface subtly in UI
```

Ten checks. First six auto-repair; last four are fatal.

| # | Check | Response |
| --- | --- | --- |
| 1 | Unknown `object_id` / `target_zone` | **repair** — drop the action, drop dangling deps |
| 2 | Dependency on a nonexistent action id | **repair** — strip the dep |
| 3 | Duplicate action ids | **repair** — resuffix |
| 4 | Missing `expected_predicates` | **repair** — synthesize from type+target |
| 5 | Missing `lock_targets` | **repair** — derive: `object:<id>`, `zone:<target_zone>` |
| 6 | `assigned_worker_id` set to an unknown/unavailable worker | **repair** — null it, let the scheduler decide |
| 7 | **Cycle in the dependency graph** | fatal |
| 8 | Unsupported `action.type` | fatal |
| 9 | No worker can ever reach a required zone | fatal |
| 10 | No success predicates / no terminal action | fatal |

Cycle detection with NetworkX — do not hand-roll it:

```python
g = nx.DiGraph()
g.add_nodes_from(a.id for a in actions)
g.add_edges_from((d, a.id) for a in actions for d in a.dependencies)
if not nx.is_directed_acyclic_graph(g):
    cycle = nx.find_cycle(g)
    return ValidationReport(ok=False, errors=[f"Circular dependency: {' → '.join(n for n,_ in cycle)}"])
```

Keep the same `g` around — Ojas's `unlock_dependents` and David's layout both want topological
order. Expose `def topo_layers(actions) -> list[list[str]]` using `nx.topological_generations`.
**David lays the DAG out in columns straight from this.** Each generation is one column, so the
graph visually reads left-to-right as time, and parallelism is literally the height of a column.

Validation messages are user-facing. Write them for a person:

- ✗ `ValidationError: cycle detected in DAG at node a7`
- ✓ `Plan rejected: actions a5 and a7 each wait on the other. Recompiling from template library.`

---

## 6. `scheduler.py` — the centerpiece

### The scoring function

**Lower is better.** Return candidates sorted ascending; the orchestrator takes index 0.

```python
@dataclass
class Assignment:
    worker_id: str
    score: float
    reason: str                  # one sentence, shown in the UI
    factors: dict[str, float]    # shown on hover; this is the "receipts"
    viable: bool

def score_workers(action: Action, state: HiveState) -> list[Assignment]:
```

Hard filters first — anything failing these is `viable=False` and never assigned:

1. `connected` and `available` and `status` not in `(unavailable, paused, emergency, executing)`
2. `action.type in worker.supported_actions`
3. object's current zone ∈ `worker.reachable_zones`
4. `action.target_zone` ∈ `worker.reachable_zones`
5. no lock conflict: no `lock_target` of this action is held by another live action
6. worker is not already holding a different object

Then the soft score:

```python
score = (
    2.0 * distance_cost          # euclid(worker.position, object.position), normalized 0..1
  + 1.5 * workload_penalty       # 1.0 if currently assigned, else 0
  + 1.0 * reachability_penalty   # 0 if both zones native; 0.5 if via an adjacent zone
  + 3.0 * capability_penalty     # 0 normally (hard-filtered), used for degraded/partial support
  + 2.5 * collision_penalty      # another active action targets the same zone
  + 0.8 * fairness_penalty       # worker.assignment_count / max(1, total_assignments)
  + 2.0 * risk_penalty           # worker.confidence < 1.0 after a failure; disconnect-recent
)
```

Weights are tuned for *legibility*, not optimality. You want the demo to visibly pick the near
worker, visibly spread work across all five, and visibly avoid the worker who just failed. Tune
until the opening wave uses four different responders. If two responders get everything, judges
read it as a script.

### The explanation string — spend real time here

Generate from the top two or three contributing factors, never generic:

```python
def explain(action, winner, runner_up, factors) -> str:
    bits = []
    if factors["distance_cost"] < 0.25: bits.append(f"closest responder to the {obj.label.lower()}")
    if factors["workload_penalty"] == 0: bits.append("currently idle")
    if factors["collision_penalty"] == 0: bits.append("no conflicting activity in that zone")
    if factors["fairness_penalty"] < 0.2: bits.append("lowest current workload")
    why_not = f" {runner_up.callsign} was {runner_up.reason_short}." if runner_up else ""
    return f"{winner.callsign} selected: {', '.join(bits[:3])}.{why_not}"
```

Target output:

> **CHARLIE selected:** closest responder to the handheld scanner, currently idle, no conflicting
> activity in that zone. DELTA was holding the radio steady.

Note the counterfactual at the end. Saying *why not the other one* is what makes it read as
reasoning rather than assignment. This is explanation from explicit scoring factors — not model
chain-of-thought — which is exactly what we want to claim.

### Parallel selection

`assign_actions()` must pick a **set** per tick, not one at a time:

```python
def select_batch(state) -> list[tuple[Action, Assignment]]:
    available = sorted(actions_with_status("available"), key=lambda a: -a.priority)
    claimed_locks: set[str] = set(state.locks.keys())
    claimed_workers: set[str] = set()
    batch = []
    for a in available:
        if claimed_locks & set(a.lock_targets):     # resource contention within this tick
            continue
        cands = [c for c in score_workers(a, state) if c.viable and c.worker_id not in claimed_workers]
        if not cands:
            if not a.blocked_reason:
                a.blocked_reason = describe_block(a, state)   # "waiting: no worker can reach Pack Station"
            continue
        best = cands[0]
        batch.append((a, best))
        claimed_locks |= set(a.lock_targets)
        claimed_workers.add(best.worker_id)
    return batch
```

`describe_block` matters more than it looks. It powers the host's honest idle explanations:

> ECHO is waiting because packing depends on the handheld scanner, which has not arrived.

### Deadlock guard

If a tick produces an empty batch, nothing is executing, and actions remain unverified — you have a
deadlock. Detect it after 3 consecutive such ticks and emit `scheduler_deadlock` with the blocking
reason. Ojas's recovery path turns that into a replan rather than a silent hang. A demo that
freezes with no explanation is far worse than one that says "no viable responder — rebuilding plan."

---

## 7. `llm_planner.py`

**We use NVIDIA NIM free endpoints for everything — no Anthropic, no OpenAI.** NIM exposes an
OpenAI-compatible API, so use the `openai` SDK pointed at the NIM base URL with our single
`NVIDIA_API_KEY`.

**Model: `nvidia/nemotron-3-super-120b-a12b`** — the catalog describes it as excelling at "agentic
reasoning, coding, planning, tool calling," which is precisely what an operations compiler is. It's
a hybrid Mamba-Transformer MoE, so it's fast for its size. If latency hurts, drop to
`nvidia/nemotron-3-nano-30b-a3b` (same family, much faster, still tool-calling). If output quality
hurts, try `qwen3-next-80b-a3b-instruct` or `gpt-oss-120b`. All free endpoints; swapping is one
config line, so **benchmark two or three early** and pick on measured latency, not vibes.

Force a **tool call** rather than begging for raw JSON — dramatically more reliable than parsing
prose. Verify tool-calling works on your chosen model in the first ten minutes; if it doesn't,
fall back to `response_format={"type": "json_object"}` plus the repair pass below.

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(base_url=settings.nim_base_url, api_key=settings.nvidia_api_key)

PLAN_TOOL = {
  "type": "function",
  "function": {
    "name": "emit_plan",
    "description": "Emit the compiled operational task graph.",
    "parameters": {
        "type": "object",
        "properties": {
            "normalized_intent": {"type": "string"},
            "actions": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"},
                "type": {"type": "string", "enum": SUPPORTED_ACTIONS},
                "description": {"type": "string"},
                "object_id": {"type": "string"},
                "target_zone": {"type": ["string", "null"]},
                "target_object_id": {"type": ["string", "null"]},
                "dependencies": {"type": "array", "items": {"type": "string"}},
                "priority": {"type": "integer"},
                "expected_predicates": {"type": "array", "items": {"type": "object"}},
            }, "required": ["id","type","description","object_id","dependencies","priority"]}},
            "success_predicates": {"type": "array", "items": {"type": "object"}},
            "notes": {"type": "string"},
        },
      "required": ["normalized_intent", "actions", "success_predicates"],
    },
  },
}

async def compile(self, goal_text, ctx) -> PlanResult:
    resp = await client.chat.completions.create(
        model=settings.planner_model, max_tokens=3000, temperature=0.2,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user",   "content": render_context(goal_text, ctx)}],
        tools=[PLAN_TOOL],
        tool_choice={"type": "function", "function": {"name": "emit_plan"}},
    )
    call = resp.choices[0].message.tool_calls[0]
    return to_plan_result(json.loads(call.function.arguments), source="llm")
```

Forcing `emit_plan` means you cannot get prose back. That eliminates ~90% of the failure modes
people hit here.

**If the model ignores `tool_choice`** (some open models do), degrade in this order and don't fight
it: `response_format={"type":"json_object"}` → strict-JSON prompt + `_extract_json()` (strip fences,
take the outermost `{...}`, `json.loads`) → template planner. Wrap the whole thing so any exception
lands on the template path. Write `_extract_json` in the first fifteen minutes; you will need it.

### `prompts.py` — the system prompt

```
You are HIVE's operations compiler. You convert a high-level objective for a physical
operation into a minimal, validated task graph that will be executed by individual humans
who each receive ONLY their own next instruction.

CRITICAL CONSTRAINT: workers cannot see the objective, the plan, each other's instructions,
or the state of the operation. Every action description must be fully self-contained and
physically unambiguous to someone who knows nothing else. Never write an instruction that
depends on shared knowledge, on another worker's action, or on the word "then".

RULES
- Use ONLY the object ids, zone ids, worker ids, and action types provided. Invent nothing.
- One atomic physical movement per action. No compound actions.
- Express ordering ONLY through the dependencies array. Never through wording.
- Maximize parallelism: actions that touch different objects and different zones must NOT
  depend on each other.
- Never allow two concurrent actions to manipulate the same object or target the same zone.
- When an object is placed on top of another, include a hold action on the base object
  before it and a release action after it.
- Prefer plans that use several different workers over plans that use one worker repeatedly.
- Do not assign workers. Leave assigned_worker_id unset; the scheduler assigns by capability.
- Derive priority from urgency words in the objective (expedited/priority/critical/first = 100,
  blocking-dependency = 85, routine = 70, background = 50). Never from what an object is.
- Include expected_predicates for every action and success_predicates for the objective.
- End with one final inspect action that depends on all zone verifications.
- At most 20 actions. Aim for 9-13.

Call emit_plan exactly once. Produce no other output.
```

`render_context` should be a compact table of workers (id, reachable zones, supported actions),
objects (id, label, current zone), and zones (id, label, adjacency). Include the current world
state — this is what lets a *replan* prompt work mid-run from wherever reality actually is.

### Repair before reject

Before falling back, try one repair pass. Missing ids get generated, unknown objects get dropped
with their dependents pruned, a missing final action gets appended. Only reject on a cycle or an
empty plan. Every repair goes into `warnings` and David shows them as a quiet line under the plan
source chip.

---

## 8. Recovery replanning (with Nikki)

Nikki owns deterministic recovery. You own the *AI-assisted* variant she calls when the deviation
doesn't match a known pattern:

```python
async def replan_from_state(state, deviation) -> PlanResult:
    """Recompile the REMAINING objective from the world as it actually is now."""
```

Same tool schema, different prompt: here is the original objective, here is what is already
verified, here is what just went wrong, here is the true current world state — produce only the
remaining actions. Timeout 8 seconds, and on any failure return Nikki's deterministic plan. The
UI's REPLANNING animation runs for a beat regardless, so the latency is covered.

---

## 9. Tests — `backend/tests/`

| Test | Assert |
| --- | --- |
| **`test_arbitrary_scene`** | **a scene of 3 randomly-generated objects + a goal naming two of them compiles to a valid graph over their real ids. The headline test — this is the promptability claim.** |
| `test_no_color_literals` | `grep -rE '"(red\|blue\|green\|yellow\|orange)"' planner/` returns nothing outside `HUE_NAMES` |
| `test_grounding_color` | "the red one" binds to the object whose `color_name == red` |
| `test_grounding_semantic` | "the cup" binds via `semantic_label` when two colors tie |
| `test_grounding_spatial` | "the leftmost box" binds by position |
| `test_grounding_ambiguous` | two red objects → `ambiguous`, no silent guess, alternatives populated |
| `test_grounding_unknown_zone` | a place name with no zone → unbound chip, not a crash |
| `test_narrower_goal_smaller_graph` | naming one object yields strictly fewer actions than naming three |
| `test_cycle_detection` | a↔b plan is rejected with a readable message |
| `test_unknown_object_repaired` | action referencing an id not in the scene is dropped, dependents pruned, plan still valid |
| `test_invalid_llm_json` | malformed tool input → falls back to template, `source == "template"`, never raises |
| `test_no_api_key_fallback` | `settings.nvidia_api_key = None` → template, no network call attempted |
| `test_template_routing` | six goal phrasings each route to the right template |
| `test_parallelism` | flagship plan has ≥4 actions with zero dependencies |
| `test_scheduler_reachability` | worker whose `reachable_zones` excludes the target is never viable |
| `test_scheduler_lock_conflict` | two actions locking `object:blue` are never batched in one tick |
| `test_scheduler_fairness` | over 20 assignments, no worker exceeds 2× the mean |
| `test_scheduler_reassign_on_unavailable` | marking the winner unavailable yields a different viable worker |
| `test_explanation_nonempty` | every assignment produces a reason string with ≥3 words |

That last one sounds trivial. It is not. An empty explanation string on the projector is a hole in
the middle of the demo.
