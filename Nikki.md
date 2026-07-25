# Nikki — Worker Client, Verification, Recovery Engine & Voygr Voice Escalation

> You own two things that look unrelated and are actually the same thing: **what an individual human
> experiences when HIVE talks to them**, and **what HIVE does when reality stops matching the plan**.
> The worker phone is the product's entire premise — five people acting in coordination without ever
> talking to each other. The recovery engine is the moment that wins the judging. Read
> `docs/CONTRACTS.md` first, then `docs/SCENARIOS.md`.

**Files you own**

```
frontend/src/routes/Join.tsx
frontend/src/routes/Worker.tsx
frontend/src/hooks/useSpeech.ts
frontend/src/hooks/useHaptics.ts
backend/app/verifier.py
backend/app/recovery.py
backend/app/integrations/voygr.py
backend/tests/test_verifier.py  test_recovery.py  test_voygr.py
```

---

## 1. Hour-by-hour

| Block | Deliverable |
| --- | --- |
| **H0–H1** | `Join.tsx` + `Worker.tsx` shells on Ojas's socket. A phone shows a callsign. |
| **H1–H2** | Instruction display + speech + haptics + the five response buttons |
| **H2–H3** | `verifier.py` — weighted evidence, all predicate types |
| **H3–H5** | `recovery.py` — deviation detection + all deterministic strategies |
| **H5–H6** | `voygr.py` + escalation policy + host call panel |
| **H6–H7** | Silent mode (campus scenario), reconnect hardening, phone polish |
| **H7–H8** | Tests, rehearse recovery with Ojas 5× |

---

## 2. The worker phone

### Design constraints that are not negotiable

A person is holding this at arm's length, in a noisy room, possibly moving, glancing at it for under
two seconds. That dictates everything:

- Instruction text: **32–40px, weight 600**, max ~7 words on the primary line.
- Buttons: minimum **64px tall**, full width, generous spacing. Thumb-reachable bottom half.
- Background is the worker's identity color at low opacity so a glance confirms *whose phone this is*.
- **No scrolling.** Ever. Everything fits one viewport, `100dvh` (not `100vh` — mobile Safari's
  toolbar will eat 60px and hide your Completed button, which is the single most common way this
  kind of UI fails on stage).
- No animation except a single state-change transition. Motion on a phone in someone's hand is noise.

### `/join`

```
        HIVE

   ◉  CONNECTING TO COLLECTIVE

        ── then ──

        BRAVO
        Responder B

   [ TEST AUDIO ]        ← speaks "Audio check. You are Bravo."
   [ I'M READY ]         ← 72px, worker color
```

The audio test button exists for one reason: iOS Safari **will not speak** until a user gesture has
unlocked the speech API. If a worker never taps anything before their first instruction, they hear
nothing all demo. Make "Test Audio" impossible to skip — gate the Ready button behind it.

On mount: read `localStorage['hive_token']`, generate a UUID if absent, send on connect. Store the
`token` returned in `worker_assigned`. This is what makes refresh reclaim the same slot.

Then `navigate('/worker/' + id)`.

### `/worker/:id`

```
┌─────────────────────────┐
│ BRAVO          ● LINKED │  ← identity + connection, 12px
├─────────────────────────┤
│                         │
│                         │
│   MOVE THE BLUE TOTE    │  ← 38px/600, the whole point
│   TO THE PACK STATION   │
│                         │
│   Set it inside the      │  ← 16px detail line, --fg-1
│   marked square.        │
│                         │
│   ▬▬▬▬▬▬▬░░░░░  0:08    │  ← timeout bar
├─────────────────────────┤
│  [    COMPLETED    ]    │  ← 72px, --ok
│  [ REPEAT ] [ HELP ]    │  ← 56px
│  [ CAN'T DO ] [ PAUSE ] │
└─────────────────────────┘
```

Idle state — this must feel calm and intentional, not empty:

```
        ◉  ← slow breathing pulse in worker color

   STAND BY
   HIVE is coordinating
   the next action.
```

Never show: the objective, the task graph, other workers, progress percentage, or how many actions
remain. A worker who can infer the plan breaks the premise. If a judge picks up a phone, all they
may see is one instruction.

### `useSpeech.ts` — speak exactly once

The single most common bug in this whole project. React re-renders; naive code re-speaks; five
phones start chanting over each other.

```ts
const spokenIds = useRef<Set<string>>(new Set());

function speak(instr: Instruction, { force = false } = {}) {
  if (!force && spokenIds.current.has(instr.id)) return;   // ← the guard
  spokenIds.current.add(instr.id);
  if (silentMode) return;                                   // campus scenario
  const u = new SpeechSynthesisUtterance(instr.spoken_text);
  u.rate = 1.05; u.pitch = 1.0; u.volume = 1.0;
  u.voice = pickVoice();                                    // prefer a local en-US voice
  window.speechSynthesis.cancel();                          // never queue; newest wins
  window.speechSynthesis.speak(u);
}
```

- `cancel()` before `speak()` — if a correction arrives mid-sentence, the old one must die instantly.
  A worker hearing a stale instruction is worse than hearing nothing.
- Repeat button calls `speak(current, { force: true })`. It does **not** ask the server for anything.
- Guard the whole thing: `if (!('speechSynthesis' in window))` → show a "TTS unavailable" chip and
  carry on. Text is the primary channel; audio is the enhancement.
- Chrome's voice list loads asynchronously — subscribe to `voiceschanged` once, or your first
  utterance uses a robotic default.
- `urgency === 'critical'` → rate 1.15, and prefix the spoken text with the callsign: "Bravo. Stop."

### `useHaptics.ts`

```ts
const PATTERNS = { normal: [40], high: [60, 50, 60], critical: [100, 60, 100, 60, 100] };
navigator.vibrate?.(PATTERNS[urgency]);
```

Feature-detect and no-op silently. iOS Safari does not support the Vibration API at all — that is
fine and expected, do not show an error for it.

### Response buttons → events

| Button | Sends | Effect |
| --- | --- | --- |
| **COMPLETED** | `worker_completed` | +0.30 weighted evidence, action → `awaiting_verification` |
| **REPEAT** | *(nothing)* | local re-speak |
| **CAN'T DO** | `worker_blocked` | action → `blocked`, triggers reassignment recovery |
| **NEED HELP** | `worker_help` | host alert, action holds, worker keeps the assignment |
| **PAUSE ME** | `worker_pause` | worker → `paused`, action released and reassigned |
| **EMERGENCY** | `worker_emergency` | global halt (long-press only — 800ms — no accidental taps) |

After COMPLETED, immediately show a confirmation state (`✓ REPORTED — awaiting verification`) and
disable the button. Do not wait for the server round-trip to acknowledge the tap; a button that
appears unresponsive gets tapped four more times.

Optional confidence selector: after tapping Completed, a one-tap `Sure / Not sure` row. "Not sure"
sends `confidence: 0.6`, which drops that evidence's contribution and forces vision to carry the
verification. It's a small thing that demonstrates real evidence fusion — worth the ten minutes.

---

## 3. `verifier.py`

Pure functions. Mutates nothing. Ojas's tick calls it and applies the result.

```python
def evaluate(action: Action, state: HiveState) -> VerificationResult:
    evidence = list(action.evidence)                      # worker reports already appended
    for pred in action.expected_predicates:
        ev = check_predicate(pred, state)                 # vision/simulation evidence
        if ev: evidence.append(ev)
    score = min(1.0, sum(e.confidence * WEIGHTS[e.kind] for e in evidence))
    return VerificationResult(
        score=round(score, 2),
        verified=score >= settings.verification_threshold,
        evidence=evidence,
        summary=narrate(evidence, score))
```

Weights and the 0.70 threshold are in `docs/CONTRACTS.md` §2. Do not redefine them locally.

### Predicate checks

| Predicate | Implementation |
| --- | --- |
| `object_in_zone` | `obj.zone == target` → evidence(vision, obj.confidence) |
| `object_near_object` | euclid < tolerance (default 0.12) |
| `object_stacked_on` | **Primary: VLM `on_top_of`** (evidence kind `vlm`, weight 0.55). CV fallback: centroid overlap < 0.06 + base verified first + worker reported (evidence kind `inference`, and the UI must say "inferred"). |
| `object_held_by` | **VLM `held` + the person's side of the table → worker mapping.** CV cannot do this; if the VLM is down, fall back to `worker_acknowledged` and say so. |
| `worker_ready` / `worker_idle` | worker status |
| `object_visible` | `obj.visible and obj.confidence > 0.4` |
| `all_objects_in_zone` | every listed object in the zone |
| `sequence_completed` | all listed action ids verified |
| `worker_acknowledged` | ack received |
| `manually_verified` | host override → evidence(host_override, 1.0) |

### `narrate()` — this string goes on the projector

```python
def narrate(evidence, score) -> str:
    parts = []
    if v := first(evidence, "vision"):        parts.append(f"tracker {int(v.confidence*100)}%")
    if m := first(evidence, "vlm"):           parts.append(f"scene model {int(m.confidence*100)}%")
    if first(evidence, "worker_report"):      parts.append("worker confirmed")
    if first(evidence, "host_override"):      parts.append("operator confirmed")
    if first(evidence, "simulation"):         parts.append("simulated state")
    return f"{' + '.join(parts)} → {int(score*100)}% confidence"
```

> `tracker 84% + scene model 91% + worker confirmed → 96% confidence`

Three independent sources agreeing is the strongest single line the timeline can produce. Make sure
it renders when it happens.

That one line demonstrates sensor fusion more convincingly than a paragraph of explanation, and it
is honest about where the belief came from.

---

## 4. `recovery.py` — the differentiator

### Detection

Run every tick. Each detector returns a `DeviationTrigger` or `None`.

| # | Detector | Condition |
| --- | --- | --- |
| 1 | `wrong_object_moved` | an object with no active action changed zone |
| 2 | `object_missing` | `confidence < 0.25`, not held, was visible ≤10s ago |
| 3 | `left_target_zone` | active action's object moved *away* from its target |
| 4 | `verification_regressed` | a **verified** predicate is now false ← the strongest demo trigger |
| 5 | `worker_timeout` | `now - dispatched_at > timeout_seconds` |
| 6 | `worker_disconnected` | holds an action, `connected=False` for >8s |
| 7 | `worker_blocked` | explicit `worker_blocked` message |
| 8 | `worker_paused` | explicit `worker_pause` |
| 9 | `conflicting_manipulation` | two actions touching the same object |
| 10 | `scheduler_deadlock` | Zechariah's 3-tick empty-batch signal |

**Debounce everything by 2 consecutive ticks (~0.5s).** A single-frame vision glitch must never
trigger the big red overlay. Steven's stability filter is your first line of defense; this is the
second. False deviations on stage are far more damaging than late ones.

### Strategy selection — deterministic first

```python
STRATEGIES = {
  "wrong_object_moved":    retrieve_and_restore,
  "object_missing":        pause_dependents_and_query,
  "left_target_zone":      reissue_with_correction,
  "verification_regressed":reverify_then_correct,
  "worker_timeout":        retry_then_reassign,
  "worker_disconnected":   reassign,
  "worker_blocked":        reassign,
  "worker_paused":         reassign,
  "conflicting_manipulation": freeze_and_serialize,
  "scheduler_deadlock":    rebuild_remaining_graph,
}

def plan_recovery(trigger, state) -> RecoveryPlan:
    plan = STRATEGIES[trigger.kind](trigger, state)
    if plan.confidence < 0.5 and settings.nvidia_api_key:
        try: plan = merge(plan, await_llm_replan(trigger, state))   # Zechariah's replan_from_state
        except Exception: pass                                       # deterministic plan stands
    return plan
```

**Deterministic always produces a valid plan.** The LLM only ever *improves* it. That ordering is
why this works on stage.

### The RecoveryPlan

```python
@dataclass
class RecoveryPlan:
    cancel_action_ids: list[str]      # actions to cancel outright
    pause_action_ids: list[str]       # dependents to freeze, NOT cancel
    release_locks: list[str]
    free_workers: list[str]
    insert_actions: list[Action]      # is_recovery=True, priority +20
    reassign: dict[str, str|None]     # action_id -> worker_id or None (rescore)
    narration: str                    # what David's overlay reads
    expected_predicates: list[Predicate]
    confidence: float
```

### The critical design decision: **isolate, don't restart**

This is the whole pitch. When the scanner (yellow) is moved to the wrong aisle:

```
1. PAUSE   only actions whose dependency chain includes the scanner  (a6, a8)
2. CONTINUE everything else — picking, packing prep, restock — untouched
3. RELEASE the lock the paused actions were holding
4. INSERT  a recovery action: retrieve scanner → dock  (priority 105, is_recovery=True)
5. RESCORE which responder retrieves it — nearest viable, not necessarily the original
6. RESUME  the paused chain once the recovery action verifies
```

Step 2 is the sentence that wins:

> "It froze only the packing workflow. Picking and restock never stopped."

Never call `state.reset()` from recovery. Never rebuild the whole graph unless the deadlock detector
fires. Surgical, not nuclear.

### Narration — write these as mission-control lines

```python
NARRATION = {
 "wrong_object_moved":
   "{object} detected in {actual} — expected {expected}. {n} dependent actions paused. Rerouting.",
 "worker_disconnected":
   "{callsign} offline. Releasing held resources and reassigning by reachability and current load.",
 "worker_timeout":
   "No confirmation from {callsign} within {t}s. Reissuing instruction, then reassigning.",
 "verification_regressed":
   "{object} left {zone} after verification. Confidence withdrawn. Corrective action dispatched.",
}
```

Fill from the trigger. Never emit a generic "recovery started."

### Retry ladder (`worker_timeout`)

1. **1st** — reissue the same instruction, new `instruction.id`, urgency `high`, prefixed with the
   callsign. Often the phone was in a pocket.
2. **2nd** — rephrase (`correction_text`), urgency `critical`, halve the timeout.
3. **3rd** — mark worker `blocked`, drop `worker.confidence` to 0.6 (the scheduler's `risk_penalty`
   now avoids them), reassign to the next viable responder.

That ladder is visible in the timeline and reads as patience → escalation → adaptation. Judges
notice it.

---

## 5. `integrations/voygr.py` — HIVE picks up the phone

**The premise:** HIVE coordinates people who are *reachable through the app*. When a situation
exceeds what the collective can resolve — a zone goes critical with no viable responder, an
emergency stop fires, or a life-safety threshold trips — it escalates outside the system by placing
a real voice call to a human who is not in the app.

That is a genuine capability gap in every coordination product, and it is a 15-second demo beat.

### Credentials — already provisioned, no setup needed

Our team key is committed as a default so nobody has to configure anything:

```
key id      6884ce45-356f-499a-b006-3e42006195a2
key         pk_live_7f130e9d22a7480b8816ec0033cf4de7
quota       2000 credits  (~200 calls at ~10 credits each)
expires     2026-08-22
team        Hackathon 2
```

Put it in `config.py` as the default and mirror it in `.env.example`:

```python
callwright_api_key: str = "pk_live_7f130e9d22a7480b8816ec0033cf4de7"
```

Two operational notes:
- It's committed to a repo we're pushing publicly. It's a capped hackathon key that expires in a
  month and Voygr can revoke it, so the exposure is acceptable — but **treat it as burned after the
  event** and don't reuse the pattern for anything real.
- 2000 credits ≈ 200 calls. That's plenty, but the escalation gate (below) exists partly so a stuck
  loop can't burn it in ten minutes. Check `/v1/usage` on startup and surface the remaining count in
  the host header.

### Client

```python
import httpx

class CallwrightClient:
    BASE = settings.callwright_base_url          # https://api.voygr.tech

    async def place_call(self, to: str, brief: str, *, meta: dict) -> CallRecord:
        if not settings.callwright_api_key:
            return await self._simulate(to, brief, meta)      # demo-safe, still emits events
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(f"{self.BASE}/calls",
                             headers={"X-API-Key": settings.callwright_api_key},
                             json={"to": to, "brief": brief, "metadata": meta})
            if r.status_code == 402:
                await state.emit("call_failed", "Voice escalation unavailable: call credits exhausted.",
                                 severity="warn")
                return CallRecord.failed("insufficient_credits")
            r.raise_for_status()
            return CallRecord.from_api(r.json())

    async def usage(self) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{self.BASE}/v1/usage",
                            headers={"X-API-Key": settings.callwright_api_key})
            return r.json()
```

Everything goes in the **brief** — that's the validated freeform launch path. Generate it from
structured state so it's accurate and never hallucinated:

```python
def build_brief(trigger, state) -> str:
    return f"""You are HIVE, an autonomous operations coordinator, placing an automated
escalation call. Be calm, concise, and factual. Deliver the situation report, then ask
the recipient to confirm they can respond, then confirm and end the call.

SITUATION REPORT
Site: {state.scenario.title}
Trigger: {trigger.human_readable}
Time: {now_local()}
Zone status: {", ".join(f"{z.label}: {z.status}" for z in state.world.zones)}
Responders: {n_available} available of {n_total}; {n_offline} offline
Blocked operations: {", ".join(a.description for a in blocked)}
Resources unaccounted for: {", ".join(o.label for o in missing) or "none"}
Attempted automatically: {trigger.attempted_summary}

ASK: confirm whether they can dispatch support to {zone.label} within 10 minutes.
If yes, say support is logged and end. If no, ask who to contact instead, then end.
Do not speculate beyond this report. Do not give instructions to anyone else."""
```

### Escalation policy — `should_escalate()`

Automatic calls need a **hard gate**, or you will dial someone during a rehearsal.

```python
def should_escalate(state) -> EscalationDecision | None:
    if not settings.escalation_phone: return None
    if state.call_cooldown_active(): return None          # 1 call / 120s, hard cap 3 per run
    if not settings.demo_mode and not state.escalation_armed: return None
    ...
```

Triggers, in priority order:

1. `worker_emergency` received from any phone → immediate
2. A zone `critical` for >20s with zero viable responders
3. `scheduler_deadlock` unresolved after two recovery attempts
4. Host presses **ESCALATE** (always allowed, manual)

**Ship it disarmed.** `escalation_armed` defaults False; the host arms it from Advanced Controls
before the demo. An accidental live call during setup burns ~10 credits and, more importantly, calls
a real person. Log every attempt loudly, including simulated ones.

Track spend: ~10 credits/call. Check `/v1/usage` on startup and show remaining calls in the host
header (`VOICE ESCALATION · 18 CALLS`). Running out mid-demo with no indicator is avoidable.

### Host call panel

On `call_initiated`, David's UI shows a compact panel:

```
   ◉ VOICE ESCALATION ACTIVE
   → +1 ••• ••• 4471  ·  Site Supervisor
   "Pack station blocked, no responder available…"
   ⏱ 00:12   ● CONNECTED
```

Poll `GET /calls/{id}` every 3s for status/transcript; emit `call_updated`. If polling fails, keep
the panel and show `STATUS UNKNOWN` — never let a failed poll blank out a panel mid-demo.

### Failure handling

| Failure | Behavior |
| --- | --- |
| No API key | simulated call, full UI, event says `simulated` in metadata (not in the headline) |
| 402 credits | `warn` event, demo continues |
| Timeout / network | `warn` event, retry once after 5s, then give up |
| Invalid number | `warn` event, surface in host panel |

The call is an **enhancement**. Nothing about the core loop may block on it.

---

## 6. Campus emergency scenario — silent mode (the video use case)

See `docs/SCENARIOS.md` for the full scenario. The engineering requirements that land on **you**:

`scenario.comms_profile = "silent"` changes the worker client materially:

- **TTS disabled entirely.** No audio, ever. A speaking phone is a hazard in a lockdown.
- **Vibration disabled.** Same reason. Visual only.
- Screen switches to a **high-contrast dark palette at minimum brightness** — a bright phone is
  visible under a door.
- Instructions become larger and shorter still: `EVACUATE — NORTH STAIRWELL — NOW`.
- The button set changes: `ACKNOWLEDGED` · `ALL ACCOUNTED FOR` · `SHELTERING IN PLACE` ·
  `NEED ASSISTANCE` · `REPORT` — reporting statuses, not task completion.
- A persistent headcount control: `24 / 26 accounted` with `-`/`+`, because roll-call rollup is the
  single most valuable output in that scenario.

Implementation: one field on the scenario drives a `commsProfile` in the state snapshot; the worker
client branches on it. Roughly 40 lines. It reads in the video as a completely different product,
which is exactly the point — **the coordination layer is general; only the presentation is
situational.**

Voygr's role here is the clearest it will ever be: HIVE compiles a structured situation report from
live state — zones cleared, headcount reconciled, last known threat location, staff unaccounted for —
and places the call to the district safety line while the coordinator keeps both hands on the
evacuation. That is the escalation capability doing exactly what it should.

**Framing discipline (matters for the video):** HIVE assists the humans running an established
protocol. It does not replace 911 dispatch, it does not direct law enforcement, and it does not make
life-safety decisions autonomously. It routes, it accounts, it reports, and every instruction it
sends is one a coordinator could have sent themselves — only faster and individually addressed. Say
that plainly in the video. Overclaiming here reads as reckless to exactly the judges you want.

---

## 7. Reliability checklist (these will all happen tomorrow)

- [ ] Phone refresh mid-action → same callsign, instruction re-delivered, no re-speak
- [ ] Wi-Fi drop 5s → auto-reconnect, `RECONNECTING` chip, no lost state
- [ ] Double-tap Completed → counted once (Ojas guards server-side; you disable client-side)
- [ ] Two workers complete simultaneously → both processed, ordered, no lost update
- [ ] Sixth phone joins → clean "all slots occupied" screen, no crash
- [ ] Screen lock during demo → tell workers to set auto-lock to Never; also request a
      **Wake Lock** (`navigator.wakeLock.request('screen')`) and re-request on `visibilitychange`
- [ ] iOS silent switch on → speech still works (it uses the media channel), but **test it**
- [ ] Speech unsupported → text-only, chip shown, no error
- [ ] Vibration unsupported → silent no-op
- [ ] Reset pressed → all phones return to STAND BY, speech queues cleared
- [ ] Emergency stop → every phone shows `ALL STOP · HANDS STILL` in `--crit`, full screen

---

## 8. Tests

| Test | Assert |
| --- | --- |
| `test_weighted_verification` | vision 0.85 alone = 0.51 → not verified; + worker report → 0.81 verified |
| `test_host_override_always_verifies` | 1.0 × 1.0 clears the threshold alone |
| `test_duplicate_completion` | second `worker_completed` adds no evidence |
| `test_predicate_object_in_zone` | true/false/missing-object cases |
| `test_deviation_debounce` | one bad tick → no trigger; two → trigger |
| `test_regression_detected` | verified object leaving its zone fires `verification_regressed` |
| `test_recovery_isolates` | only dependents of the affected object are paused; siblings keep running |
| `test_timeout_ladder` | attempts 1,2 reissue; attempt 3 reassigns and drops worker confidence |
| `test_disconnect_releases_locks` | locks freed, action reassigned, no orphan lock remains |
| `test_recovery_no_valid_worker` | escalation decision produced instead of a silent hang |
| `test_voygr_no_key_simulates` | returns a `CallRecord`, emits events, never raises |
| `test_voygr_402` | credit exhaustion → `warn` event, core loop unaffected |
| `test_escalation_disarmed` | `escalation_armed=False` → no call attempted |
