# Steven — Interface

> **You own everything the judges look at.** The system underneath works; it now has to
> *look* like it works. Your job is to make one screen that a person can read from ten
> feet away and immediately believe.

**First command, before anything else:**

```bash
git pull --rebase origin main && cd frontend && npm install && npm run dev
# then, in another terminal:  cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000
```

Open **http://localhost:5173/host**. Rebase before every work session — three people are
pushing to this repo tonight, and a merge conflict in a `.tsx` at 2am is a bad trade.

---

## What already works (don't rebuild it)

The host page is **fully wired to live state**. It was rendering `dummyData` until an hour
ago; that's gone. `useHiveState()` folds the socket snapshot plus deltas into one object,
and every panel reads from it. Compile, start, pause, reset, emergency stop, scenario
switching, failure injection, and the rescue drawer all send real messages to the backend
and the backend really responds.

So: **do not restructure the data flow.** Every improvement you make from here is visual.

| File | State |
| --- | --- |
| `routes/Host.tsx` | live, composes everything |
| `hooks/useHiveState.ts` | live state accumulator — treat as read-only |
| `components/ObjectiveBar.tsx` | scan · compile · scenario · plan chip · disambiguation |
| `components/AdvancedControls.tsx` | real rescue drawer, real payloads |
| `components/Sidebar.tsx` | workers + attribution + zone occupancy |
| `components/{TaskGraph,WorldView,Timeline,Toolbar,Inspector,MetricsPanel}.tsx` | live props |
| `routes/{Join,Worker}.tsx` | the phone client, already working |

---

## Your job, in priority order

### 1. The deviation moment (highest value, do this first)

This is the three seconds the entire demo is built around. A judge moves an object, and
the screen has to *land*. Right now `DeviationBanner` renders, but it doesn't feel like
anything.

What it should do, in sequence:

```
  a 2px --failure border draws around the whole viewport      (300ms)
  the world view dims and desaturates behind a blur           (200ms)
  a centered panel rises 12px and fades in                    (400ms)

        FLOOR STATE DEVIATION
   EXPECTED   handheld scanner · Pack Station
   OBSERVED   handheld scanner · Pick Aisle A     ← --failure
   IMPACT     3 actions paused

        ◈ REPLANNING
```

Then it **morphs** — does not unmount and remount — into the resolution, holds ~1.4s, and
dissolves. Total 4–5 seconds. The graph must keep updating behind the blur, because the
whole claim is that everything *else* kept running.

The data is already there: `state.deviation` has `expected`, `observed`, `message`,
`action_ids`; `state.recovery` has the narration. Ship the choreography.

**Test it without a judge:** Advanced Controls → Inject → *Verification regress*.

### 2. The task graph should be legible from ten feet

`TaskGraph` uses React Flow and lays columns out from `topoLayers` — column = time, column
height = parallelism. That's the right structure. What's missing is that four nodes stacked
in column one should *read* as "four things happening at once" instantly.

- Executing nodes: slow pulsing glow in the assigned worker's colour, 1.6s.
- When a node verifies, run a 400ms travelling highlight down its dependency edges. That
  is the "unlocking" moment and it's cheap with an animated `stroke-dasharray`.
- Verified nodes settle to ~70% opacity. Done work should recede.
- Failure is **static red** — never animate a failure, it reads as a broken UI.
- `fitView` once on compile, then never auto-pan again. A graph that re-centres while
  someone is pointing at it is maddening.

### 3. Make the world view feel like perception

`WorldView` draws the camera feed with an SVG overlay. Every object carries
`descriptor.color_hex` — its **actually sampled colour**. Render that, never a palette
constant. When a judge puts a teal mug on the table, the dot should be teal. That single
detail does more to prove this is real perception than any amount of copy.

Add: a fading trajectory polyline per object (last ~15 positions), and on deviation a
`--failure` marker at the *expected* location with a dashed line to where the object
actually is, labelled EXPECTED. That one element explains the whole story at a glance.

### 4. Ten-feet test — literally walk back and check

- [ ] Can you tell which workers are active without squinting?
- [ ] Is the parallel column obviously *a column of four*?
- [ ] Does the deviation panel read in under two seconds?
- [ ] Is anything moving that isn't communicating a state change? Delete it.
- [ ] Is the screen *calm* when idle? Idle should look composed, not dead.
- [ ] On a projector, is `--text-tertiary` still legible? If not lift it to `#7A838F`.

Most conference projectors crush blacks and blow out saturation. Test on an external
display **before** the demo and keep a one-line CSS override ready.

---

## Design system (already in `styles/tokens.css` — it's good, stay inside it)

Near-black surfaces, hairline separators, one accent at a time, huge numbers, tiny
uppercase labels. Apple keynote diagram meets air-traffic display. Motion only on real
state change, `--ease-standard`, 180–260ms, nothing bounces.

**Worker colours are fixed and shared with the backend** — ALPHA `#5AC8FA`, BRAVO `#5E5CE6`,
CHARLIE `#30D158`, DELTA `#FF9F0A`, ECHO `#FF375F`. There are deliberately **no object
colours** in the token file; objects carry their own sampled hex.

---

## Known issues that are yours

1. **Three vision tests fail in the full suite, pass alone.**
   `backend/tests/test_vision.py::{test_association_stable_ids, test_new_object_appears,
   test_missing_object_decay}`. `pytest tests/test_vision.py` → 13 passed;
   `pytest tests` → those 3 fail. It's fixture isolation: they build their own state while
   the shared `conftest` fixture resets the vision singletons between tests. Give
   `test_vision.py` its own fixture that constructs a `WorldModel` directly instead of
   going through the bridge. **The vision code itself is fine** — don't go looking for a
   bug in `world_model.py`.
2. **Bundle is 1.29 MB.** React Flow and Three are most of it. If the demo laptop is slow,
   `manualChunks` or lazy-load the graph.
3. **`Join.tsx` / `Worker.tsx` are unowned now.** They work. If you have time after the
   host is beautiful, the phone deserves a pass: 32–40px instruction text, 64px+ buttons,
   `100dvh` not `100vh` (mobile Safari's toolbar will eat the Completed button).

---

## Rules that will save you

- **Never let a worker's phone see the plan.** Only its own instruction. If a judge opens
  devtools on a phone and sees the task graph, the entire premise collapses. The server
  enforces it; don't route around it.
- **No modals.** A dropped socket is a quiet chip in the toolbar, never a dialog.
- **`prefers-reduced-motion` is your performance escape hatch** — it's already wired to
  cut every duration to 0.01ms if the render gets heavy live.
- Run `npx tsc --noEmit` before you push. It is faster than finding out from someone else.
