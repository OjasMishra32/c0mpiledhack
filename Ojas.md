# Ojas — Intelligence & Orchestration

> You own the loop, the perception layer, and the demo itself. The system runs end to end
> today. What's left is the part that makes judges believe it's *thinking* rather than
> executing — and being the person who can make it do that on command.

**First command, before anything else:**

```bash
git pull --rebase origin main
cd backend && .venv/bin/python -m pytest tests -q          # expect 129 passed
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Rebase before every session. Two other people are pushing tonight.

---

## Where it actually stands

Verified end to end over real sockets, not just in tests:

- 5 phones connect, get distinct callsigns, refresh reclaims the same slot
- Objective compiles to a ~9-action graph, **4 executable in parallel**, 5 stages deep
- Private instructions dispatch — no phone ever receives the plan
- Disable a worker mid-action → reassigned with a spoken reason → run completes
- 129 tests pass under every `PYTHONHASHSEED`, with no key, no camera, no network
- 3 NVIDIA keys pooled at ~114 RPM with sticky affinity and 429 cooldown

**Your files:** `main · config · models · state · websocket_manager · orchestrator ·
host_commands · attribution · key_pool · perception/{nim_client,analyzer} · vision/bridge ·
demo/scenarios · integrations/voygr`

---

## What's left, in priority order

### DONE — deviation adjudication and Ask the feed are shipped

Both are wired and tested; do not re-implement them.

- `orchestrator._start_adjudication` sends a burst of the last ~2.5s of frames to
  `analyzer.analyze_deviation` before any banner fires. A refuted deviation is dismissed
  quietly; a confirmed one uses the model's own sentence as the headline. It never blocks
  the tick and **fails open** after `ADJUDICATION_TIMEOUT` (3s) — a hung endpoint cannot
  swallow a real deviation. Covered by `test_adjudication_fails_open_when_unavailable` and
  `test_adjudication_suppresses_a_refuted_deviation`.
- **Ask the feed** is in the toolbar (`AskFeed.tsx` → `host_ask_feed`). Verified against the
  live camera: asked what was in front of the lens, it correctly reported three people and
  no work surface.

### 3. Rehearse until you don't need the screen

**`DEMO.md` is the runbook — use that on stage.** This is the same script, kept here for context:

| t | Screen | You say |
| --- | --- | --- |
| 0:00 | 5 nodes pulsing | "Five workers. None of them knows the full plan — each one only ever sees their next task." |
| 0:08 | Scan Scene | "It has never seen this table. It's looking now." |
| 0:14 | Type objective, Compile | "One sentence in." |
| 0:20 | Graph explodes out | Read the counts off the screen — the scene is discovered fresh, so they vary. Typically nine actions, four parallel. "It worked out the ordering itself, and two of these need the same item so it sequenced them." |
| 0:30 | Instructions land | "Private instructions. Nobody is coordinating out loud." |
| 0:45 | **Judge moves an object** | "You're the floor. Change something." |
| 0:50 | FLOOR STATE DEVIATION | *(say nothing for three seconds — let the screen talk)* |
| 1:00 | REPLANNING → reassign | "It froze only the packing chain. Picking and restock never stopped." |
| 1:20 | Completion metrics | "One deviation. One recovery. Zero conflicting assignments. Nothing restarted." |
| 1:30 | Close | "Today it coordinated five people around a table. Same code runs a warehouse, a hospital, an airport — or a campus evacuation, where every person needs a different instruction, silently, at the same second." |

**Rules for yourself:**
- Never narrate the failure before the screen shows it. Three seconds of silence beats any
  sentence you could say.
- Rehearse the recovery five times. It is the only moment that matters.
- Have a deterministic backup: Advanced Controls → Worker → **Disable**. Same story, zero
  dependence on vision. Use it if the judge hesitates.
- **Pre-flight:** `pytest` green → server up → 5 phones joined → Scan Scene shows the right
  count → one silent full rehearsal → Reset → laptop plugged in, sleep off, notifications off.

### 4. The scenario-switch beat (15s, only if they're engaged)

Pick a different scenario from the dropdown. Every zone, role and headline re-labels in
about two seconds.

> "Same webcam, same five phones, same code. I just told it it's a different operation."

It is the strongest architectural argument you have, and it costs one click.

---

## The two things that could still bite you

1. **Cosmos is 404 on our account.** Every `nvidia/cosmos-*` model returns
   `"Not found for account"` — a known "Public API Endpoints" permission gap. Nemotron and
   GLM work, and `analyzer.probe()` auto-selects whatever is reachable, so the demo is
   unaffected and Cosmos lights up automatically if access lands. Don't hard-code a model.
2. **Voygr escalation ships disarmed.** `ESCALATION_PHONE` is unset deliberately — an
   accidental live call during setup dials a real person. To demo it: set the number, then
   Advanced Controls → arm. 2000 credits ≈ 200 calls, expires 2026-08-22.

---

## Architecture, in one paragraph, for when someone asks

Perception is **two independent sensors over one camera feed**: OpenCV at 10–20 Hz owns
geometry and object identity; a VLM, invoked only on meaningful events, owns meaning —
what things are, who is holding what, what just changed. Neither is a single point of
failure. Nothing about the objects is hardcoded: the camera discovers whatever is there,
and grounding binds plain-language phrases to real observed ids, so you can swap an object,
rescan, and it still works. The LLM thinks occasionally; deterministic code controls
continuously. And delegation runs on evidence — `attribution.py` tracks whether a worker's
"done" survived verification, how fast they are, and what they've already handled, then
re-orders candidates the scheduler already judged capable, carrying the sentence that
explains why.
