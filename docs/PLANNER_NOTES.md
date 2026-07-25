# Planner & scheduler — state of play (Zechariah)

Everything in `backend/app/planner/*` and `backend/app/scheduler.py` is built and green:
`backend/.venv/bin/pytest backend/tests -q` → 68 passing, no network, no key, no camera.

## What Ojas calls

```python
from app.planner import compile_goal, replan_from_state          # async
from app.scheduler import assign_actions, unlock_dependents      # sync, per tick

plan = await compile_goal(goal_text, scene, workers, emit=broadcast)   # grounding + fallback chain
if plan.is_pending:            # grounding needs the host to click an object on the feed
    ...                        # resume by grounding.bind_manually(phrase, object_id) then recompile
plan.mark_ready()              # dependency-free actions → "available" (compile_goal already does it)

tick = assign_actions(state)   # -> SchedulerTick(batch=[(action, assignment)], deadlock=str|None)
for action, assignment in tick.batch:
    #  the scheduler never mutates Action.status — you apply the decision
    action.assigned_worker_id = assignment.worker_id
    action.assignment_reason = assignment.reason      # this string goes on the projector
if tick.deadlock:
    ...                        # emit scheduler_deadlock, then replan_from_state()
```

`emit` is an optional `(type, payload)` callback (sync or async). The planner uses it for
`grounding_resolved` and `grounding_ambiguous` only; it never touches the websocket directly.

State is duck-typed (`scheduler.SchedulerState`): anything with `workers`, `actions`, `scene`,
`locks` works, so the real `HiveState` is a drop-in replacement for the provisional one.

## Files I authored that are formally someone else's

- **`backend/app/models.py`** — written straight from `docs/CONTRACTS.md` §1–§2 so the planner
  could be tested for real. Ojas owns this file; if his version differs, his wins — the two
  should already agree because we both coded to the contract. `HiveState` is the one shape the
  contract does not pin down, so treat mine as provisional.
- **`backend/app/config.py`** — every variable from `.env.example` plus three planner timeouts
  (`planner_timeout_seconds` 12s, `replan_timeout_seconds` 8s, `grounding_timeout_seconds` 8s).
- **`backend/pytest.ini`** — `pythonpath=.`, `asyncio_mode=auto`. Needed by every test suite.

## Three decisions the whole team should know

1. **Locks are per-object, not per-zone.** The contract's `Action` example shows
   `lock_targets: ["object:yellow", "zone:z2"]`. If zones lock, only one action per zone can be
   dispatched per tick — and the flagship's opening wave puts three items into the Pack Station
   at once, so it would serialise into a queue. Two people cannot carry the same item; several
   can walk to the same station. Zone contention is therefore a *soft* cost in the scheduler
   (`collision_penalty`, weight 2.5), which is what §6 of my handoff describes anyway.
   → If anyone wants zone locks back, the flagship graph has to change shape.

2. **A gate only blocks lower-priority work in its zone.** "Packing cannot start until the
   scanner is docked" does not stop an expedited item from *arriving* at the pack station. Gates
   apply to actions in the gated zone with priority below the gate's (85), which is what keeps
   the opening wave four-plus wide while the stated dependency still shows up in the DAG.

3. **Superlatives score decisively.** "the leftmost box" is a selection operator, not a soft
   preference, so it gets 0.25 rather than the 0.10 spatial weight — otherwise it lands inside
   the 0.15 ambiguity margin and HIVE asks the host a question it should have answered itself.

## Grounding, in one paragraph

`resolve_all(goal_text, scene)` extracts noun phrases, place phrases, quantifiers ("everything",
"the other two"), prose gates ("cannot start until …"), stack relations, and urgency words, then
scores every phrase against every *observed* object (colour 0.40 · label 0.30 · shape 0.15 ·
spatial 0.10 · size 0.05). Near-ties (< 0.15 apart) become `grounding_ambiguous` instead of a
guess. Bound phrases are written onto `object.role`, so every instruction downstream says "the
priority item", never "obj_3". Unknown place names become `unbound_places` for the host to draw.
With `NVIDIA_API_KEY` set, one planner-model call fills *only* the leftovers (pronouns,
relational language) — it never overrides a confident descriptor binding.

## Known rough edges

- **Codes don't ground.** "SKU-1180" / "order 4471" resolve to nothing unless a VLM label
  contains that string; they surface in `unresolved_phrases`. The flagship objective in
  `docs/SCENARIOS.md` leans on them, so for stage safety phrase it around what the camera can
  actually see ("the red item", "the yellow scanner") and keep the order number as narration.
  What we currently rehearse against is in `backend/tests/planner_fixtures.py::FLAGSHIP_GOAL`.
- **The LLM path is untested against a live NIM endpoint.** The fallback chain (forced tool call
  → `json_object` → strict-JSON prompt → template) is tested with a fake client. Ten minutes with
  a real key would confirm `nvidia/nemotron-3-super-120b-a12b` honours `tool_choice`; if it
  doesn't, nothing breaks, it just degrades a step.
- **`describe_block` messages are user-facing** and start with "waiting: ". David can render them
  verbatim under an idle worker.
