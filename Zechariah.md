# Zechariah — Planning, Grounding & Scheduling

> Your workstream is merged, integrated and green. What's left is making the plan HIVE
> produces as impressive as the machinery that produces it — because the graph on screen
> is the thing judges read as *intelligence*.

**First command, before anything else:**

```bash
git pull --rebase origin main
cd backend && .venv/bin/python -m pytest tests -q       # expect 110 passed
```

Rebase before every session. Two other people are pushing tonight.

---

## What changed in your files during integration

All three were blocking the flagship, all three have tests. Read these before you touch
`grounding.py`, so you don't re-break them:

1. **Zone phrases were deduped by text.** In *"Move X to the Pack Station. Move Y to the
   Pack Station."* only the first mention produced a `ZoneBinding`, so every later item
   found no destination in its clause and silently vanished from the plan. Pairing is
   span-based, so every mention must bind — now keyed by `(phrase, span)`.

2. **`_label_tokens` stripped articles anywhere in a label.** `"Pick Aisle A"` tokenised to
   `["pick","aisle"]` and therefore matched *"Pick Aisle B"*. Articles are stripped only at
   the front now; a trailing `"A"` is an identifier, not an article.

3. **Colour normalization was one-sided.** The vision namer emits `teal`, `lime`, `indigo`.
   `_phrase_colors` normalized the *operator's* word through `COLOR_SYNONYMS` but not the
   *object's* — so HIVE would print "teal item" on screen and then fail to resolve "the teal
   item". Both sides normalize now, and `indigo` was missing from the vocabulary entirely.
   **The namer's vocabulary and yours must stay identical.** There is a test asserting it.

Your three decisions from `PLANNER_NOTES` were all accepted and now live in
`docs/CONTRACTS.md §8` — per-object locks in particular; the core reached the same
conclusion independently, for the same reason.

---

## Your job, in priority order

### 1. The survey-floor fallback surprises people

When grounding under-binds, the template planner emits a survey plan so the screen is never
empty. Defensible — but it means **a narrower objective can produce a larger graph**. A
judge who types a one-item goal and watches the DAG *grow* will read that as the system not
listening.

Make the fallback legible instead of silent: when it fires, say so. A quiet line under the
plan chip — *"Couldn't bind 'the widget' — surveying all items instead"* — converts a
confusing moment into evidence that HIVE knows what it doesn't know. `PlanResult.warnings`
already flows to the UI.

### 2. Make the flagship graph richer without making it slower

Current flagship: 7 actions, 4-wide opening wave, 3 stages. That reads well. What it's
missing is a **visible serial spine** — the gate currently only affects priority, so the
graph is wide and shallow.

A `hold` → dependent deliveries → `release` chain around the gating item would give the DAG
a shape that reads as *reasoning about ordering*, not just fan-out. You wrote the
stabilization template already; it just isn't firing on the warehouse goal. Worth 20
minutes.

Target shape: **9–11 actions, 4 parallel, 4 stages deep.** More than that and it stops
being readable from ten feet.

### 3. Scheduler explanations are the most-quoted text on screen

They're good. Push them further, because this is where "capability-aware" becomes visible:

> **CHARLIE selected:** closest to the scanner, currently idle, no conflicting activity in
> Pack Station. DELTA was mid-task.

The counterfactual at the end is what makes it read as *reasoning* rather than assignment.
Make sure one is always present — an explanation that only says what, never why-not, is
half an explanation.

Note that `orchestrator._apply_attribution` may **re-rank your winner** using observed
performance (reliability, speed, familiarity) and will then rewrite the reason string to
say so. That's deliberate: you decide who *can*, attribution decides between equals. If you
change `Assignment`, keep `.reason`, `.callsign` and `.factors` — that function reads them.

### 4. Replanning from current state

`replan_from_state()` exists and is unused. Recovery currently uses deterministic
strategies only, which is correct as the floor — but a judge who moves *two* objects at
once will hit a case the deterministic path handles clumsily.

Wire it as the escalation: if `plan_recovery` returns `confidence < 0.5`, ask the model to
recompile the *remaining* objective from where the world actually is. Timeout 8s,
deterministic plan stands if it fails. Same discipline as everywhere else — the model
improves the answer, it never gates it.

---

## Constraints that are not yours to change alone

- **Locks are per-object, never per-zone.** Zone contention is a soft cost. Zone locks
  would serialise the opening wave into a queue.
- **`Action.type` is typed `str`, not the enum, on purpose.** Rejecting an unsupported type
  is your validator's job — it has a readable message. Rejecting it at model construction
  just raises on a plan you could have repaired.
- **Both predicate encodings are valid** (`docs/CONTRACTS.md §8.3`): `all_objects_in_zone`
  and `sequence_completed` each accept explicit ids *or* a zone/sentinel. The verifier
  handles both so neither side has to know the other's preference.
- **Never name an object in planner code.** No colour literals, no object ids. Objects come
  from the camera; meaning comes from grounding. There's a test for this too.

---

## How to check your work quickly

```bash
# does a plan actually respond to the words typed?
.venv/bin/python -m pytest tests/test_grounding.py tests/test_planner.py -q

# the whole thing, deterministically
for h in 0 1 2 3; do PYTHONHASHSEED=$h .venv/bin/python -m pytest tests -q | tail -1; done
```

Hash-seed variation is not paranoia — it caught four real product bugs, including the
colour-normalization one above. If a test only passes on some seeds, you've found
something real.
