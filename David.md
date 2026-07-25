# David — Host Command Center, Design System & Live Visualization

> You own everything the judges actually look at. The backend can be flawless and if the screen
> looks like an admin dashboard we lose. Your target is *Apple-clean mission control*: dark,
> spacious, typographically confident, with motion that means something. Read `docs/CONTRACTS.md`
> first — every pixel you draw is a field in there.

**Files you own**

```
frontend/src/routes/Host.tsx
frontend/src/components/
  ├── TaskGraph.tsx          ← the DAG, the centerpiece
  ├── WorkerGrid.tsx         ← five responder nodes
  ├── EventTimeline.tsx      ← the narration
  ├── GoalBar.tsx            ← objective input + plan source chip
  ├── ZonePanel.tsx          ← three zone status cards
  ├── MetricsPanel.tsx       ← the closing numbers
  ├── DeviationOverlay.tsx   ← the money shot
  ├── AdvancedControls.tsx   ← the rescue drawer
  └── primitives/            ← Chip, Stat, Panel, Pulse, Rule
frontend/src/styles/tokens.css
frontend/tailwind.config.ts
```

Steven owns `WorldView.tsx` (the camera + AR overlay); you own the frame it sits in and the design
tokens it uses.

---

## 1. The visual thesis

**Not** a SaaS dashboard: no rounded white cards on grey, no shadowed boxes everywhere, no icon
soup, no 14px body text.

**Yes**: near-black canvas, content floating on it with hairline separation, one accent color at a
time, enormous numbers, tiny uppercase labels, and lines that animate only when something real
happens. It should read as a *live system* from ten feet away, and reward a close look with detail.

Reference feel: Apple's keynote system diagrams meets an air-traffic display. Zero bee imagery.

### Tokens — `styles/tokens.css`

```css
:root {
  /* surfaces — near-black, never pure black; pure black kills depth on projectors */
  --bg-0:  #08090B;   /* page */
  --bg-1:  #0E1013;   /* panel */
  --bg-2:  #14171C;   /* raised */
  --line:  #1E232B;   /* hairline */
  --line-strong: #2B323C;

  /* text */
  --fg-0:  #F5F7FA;   /* primary */
  --fg-1:  #A2ACBB;   /* secondary */
  --fg-2:  #626C7A;   /* tertiary / labels */

  /* semantic */
  --ok:    #30D158;
  --warn:  #FF9F0A;
  --crit:  #FF375F;
  --info:  #5AC8FA;
  --think: #5E5CE6;   /* planning / replanning */

  /* worker identity — fixed, matches backend */
  --w-a: #5AC8FA; --w-b: #5E5CE6; --w-c: #30D158; --w-d: #FF9F0A; --w-e: #FF375F;
  /* NOTE: there are NO object colors here. Objects are discovered at runtime and carry their own
     sampled `descriptor.color_hex`. Render that value directly — never map an object to a palette
     constant. If you find yourself writing OBJECT_COLORS = {...}, stop. */

  --r-sm: 8px; --r-md: 14px; --r-lg: 20px;
  --ease: cubic-bezier(0.22, 1, 0.36, 1);   /* the only easing you use */
}
```

### Type scale

| Use | Size / weight / tracking |
| --- | --- |
| Hero metric | 64px / 300 / -0.03em — tabular numerals |
| Panel metric | 34px / 400 / -0.02em |
| Section label | 11px / 600 / **+0.14em uppercase** / `--fg-2` |
| Body | 15px / 400 |
| Event line | 13px / 400, mono for the timestamp only |
| Instruction preview | 20px / 500 |

Use one font: **Inter** (or the system stack) for everything, **JetBrains Mono** only for
timestamps, ids, and confidence numbers. Enable `font-variant-numeric: tabular-nums` globally on
metrics — numbers that jitter as they update look cheap and it's a one-line fix.

### Motion rules

- Everything uses `--ease`, 180–260ms. Nothing bounces.
- **Motion only on real state change.** No ambient floating, no decorative particles. A pulse means
  an action is executing. A line lights up because an instruction was actually dispatched.
- The deviation overlay is the one exception — it gets a deliberately dramatic 600ms entrance,
  because that's the beat we're selling.
- Respect `prefers-reduced-motion` by cutting durations to 0.01ms. Also: if the render gets heavy
  during the demo, this is your emergency performance switch.

---

## 2. Layout

A three-column grid on a 16:9 projector, 100vh, no page scroll. Panels scroll internally.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  HIVE      COLLECTIVE ONLINE · 5 NODES        MODE: LIVE      ⏱ 00:47        │  56px
├──────────────┬───────────────────────────────────────┬───────────────────────┤
│ WORKERS      │  OBJECTIVE                            │  EVENT TIMELINE       │
│              │  ┌─────────────────────────────────┐  │                       │
│  ● ALPHA     │  │ fulfill order 4471 and restock…  │  │  19:41:02  dispatch   │
│    executing │  └─────────────────────────────────┘  │  19:41:04  verified   │
│  ● BRAVO     │  [AI PLANNER] 11 actions · 4 parallel │  19:41:09  DEVIATION  │
│    idle      │                                       │  19:41:09  replanning │
│  ● CHARLIE   │  ┌─────────────────────────────────┐  │  …                    │
│  ● DELTA     │  │                                 │  │                       │
│  ● ECHO      │  │        TASK GRAPH (DAG)         │  │                       │
│              │  │                                 │  │                       │
│ ── SCENE ──  │  └─────────────────────────────────┘  │                       │
│ 5 OBJ · 4 ZN │  ┌─────────────────────────────────┐  │                       │
│  ● PACK   ◐  │  │   WORLD VIEW (camera + overlay) │  │                       │
│  ● AISLE A ✓ │  └─────────────────────────────────┘  │                       │
├──────────────┴───────────────────────────────────────┴───────────────────────┤
│  ▸ Advanced Controls              [Compile] [Start] [Pause] [Reset] [E-STOP] │  64px
└──────────────────────────────────────────────────────────────────────────────┘
```

Column widths: `320px | 1fr | 380px`. Gap 1px with `--line` backgrounds — hairline dividers, not
gutters. That single choice is most of the "operations center vs dashboard" difference.

---

## 3. TaskGraph.tsx — the centerpiece

Use **React Flow** (`@xyflow/react`). Do not hand-roll SVG layout; you don't have the hours.

### Layout

Zechariah exposes topological generations. Column = generation index, row = position within it.
This makes time flow left→right and **parallelism becomes literal vertical height** — four nodes
stacked in column 1 *is* the "4 actions in parallel" claim, visually.

```tsx
const cols = topoLayers(actions);          // string[][] from the plan_compiled payload
const nodes = cols.flatMap((col, x) =>
  col.map((id, y) => ({
    id, type: 'action',
    position: { x: x * 210, y: y * 96 - (col.length - 1) * 48 },  // vertically centered
    data: actions[id],
  })));
```

`fitView` on compile, then **never auto-pan again**. A graph that re-centers itself mid-demo while
you're pointing at a node is maddening.

### Node design

96×64px. Not a card — a slab.

```
┌─────────────────────────┐
│ ▮ MOVE · ◗ SCANNER      │   11px uppercase; worker color bar left, object's SAMPLED color dot
│ Pack Station            │   13px, --fg-1
│ ●●●●○  CHARLIE      84% │   progress dots · callsign · confidence (mono)
└─────────────────────────┘
```

The object dot uses `descriptor.color_hex` straight from the backend. The label is
`role ?? semantic_label ?? "{color_name} {shape_hint} object"` — a helper on the type, not a lookup
table in your component. When a judge puts a teal mug on the table, that node shows a teal dot and
says "teal mug". Nothing in the frontend knows what objects exist until the scene is scanned.

| Status | Treatment |
| --- | --- |
| `queued` | `--bg-1`, border `--line`, text `--fg-2`, 55% opacity |
| `available` | border `--line-strong`, full opacity |
| `assigned`/`dispatched` | border in the assigned worker's color |
| `executing` | worker-color border + **slow pulsing glow** (`box-shadow` 0→18px, 1.6s) |
| `awaiting_verification` | border `--info`, dashed, subtle shimmer |
| `verified` | border `--ok`, ✓ badge, then settle to 70% opacity — done work recedes |
| `failed`/`blocked` | border `--crit`, static (do not animate failure; it reads as broken UI) |
| `recovery` | border `--think`, and the node **slides in** rather than fading |

### Edges

- Dependency edges: `--line-strong`, 1px, bezier. When the source verifies, run a 400ms traveling
  highlight along the edge to the target. This is the "unlocking" moment and it's cheap to do with
  an animated `stroke-dasharray`.
- Assignment edges (worker → action): worker color, 1.5px, only rendered while that action is
  live, animated dash flow. They should feel like current in a wire.
- Lock indicators: a small 🔒-free glyph (a filled square) on nodes holding a lock, in `--warn`.
  Tooltip: "Holds object:blue — no other responder may move it."

### Recovery animation

When `recovery_started` arrives: dim every node to 30% except the affected ones over 200ms, hold
600ms while the overlay reads, then fade back as new nodes slide in. It reads as the system
*thinking about a specific part of the problem*, which is exactly the claim.

---

## 4. WorkerGrid.tsx

Five nodes, always all five, even when disconnected — an empty slot with a dashed ring says
"ECHO not yet online," which is better than a grid that reflows as phones join.

```
  ◉ ALPHA                        ← 10px dot in --w-a, pulsing when executing
    EXECUTING · 00:04            ← status + elapsed on current action
    Move priority item to Pack   ← their current instruction, 13px, --fg-1
    ▬▬▬▬▬▬▬▬░░░░  timeout ring
```

The host **can** see each worker's instruction (they're the commander). Workers cannot see each
other's. Don't confuse these.

Status colors: `ready` → `--fg-1`; `executing` → worker color; `blocked` → `--warn`;
`unavailable` → `--crit` with a strikethrough on the callsign; `disconnected` → dashed ring, 40%.

Timeout ring: a thin arc around the dot depleting over `timeout_seconds`. When it passes 75% it
shifts to `--warn`. Judges notice this. It makes deadlines feel real.

Clicking a worker opens a compact popover: reachable zones, assignment count, confidence, and
**Disable / Enable** — your fastest path to the worker-failure demo.

---

## 5. EventTimeline.tsx

Newest at top. Auto-scroll only when already at top (never yank the view while someone reads).

```
19:41:09  ⚠  Handheld scanner detected outside planned route.
19:41:09  ◈  Replanning around blocked packing workflow.
19:41:04  ✓  Water supply confirmed at Medical Station · 84%
19:41:02  →  CHARLIE dispatched: move handheld scanner to Pack Station
```

- Timestamp in mono, `--fg-2`, 11px.
- A 3px left rule in the severity color. No icon backgrounds, no chips per row.
- `critical` events get a one-shot background flash (`--crit` @ 12% → transparent, 900ms).
- Cap the DOM at ~120 rows. Virtualization is overkill; slicing is not.

Group runs of identical types with a count badge (`×3`) so a burst of vision updates doesn't push
the deviation off screen.

---

## 6. DeviationOverlay.tsx — the money shot

This is the single most important component in the repo. Everything else supports the three seconds
this is on screen.

On `deviation_detected`:

1. A `--crit` 2px border draws around the entire viewport (300ms, clockwise).
2. Center panel, backdrop-blurred, slides up 12px + fades in (400ms):

```
        WORLD STATE DEVIATION

  EXPECTED    handheld scanner · Pack Station
  OBSERVED    handheld scanner · Pick Aisle A     ← --crit
  IMPACT      3 actions paused · packing workflow blocked

        ◈ REPLANNING RESPONSE
```

3. `REPLANNING` gets a scanning shimmer while the recovery is computed.
4. On `recovery_completed`, the panel morphs (don't unmount — morph) to the resolution, holds
   1.4s, then dissolves:

```
        RESPONSE REPLANNED

  CHARLIE reassigned  →  retrieve handheld scanner
  Picking & restock continued uninterrupted
  0 conflicting assignments
```

Total 4–5 seconds. The overlay must be **non-blocking**: the graph keeps updating behind the blur,
because the whole point is that the rest of the operation never stopped.

Build this early and rehearse it against a fake event. Do not leave it to hour seven.

---

## 7. GoalBar, ZonePanel, MetricsPanel

**GoalBar** — a single large borderless input on `--bg-1`, 20px text, placeholder
`Enter operational objective…`, plus a preset dropdown of Ojas's five scenarios. After compile,
the input collapses to a one-line objective statement with the plan source chip:

`[ AI PLANNER ]` `--think` · `[ TEMPLATE ]` `--info` · `[ KNOWN-GOOD ]` `--fg-2`

All three styled identically in weight. **Template must not look like a downgrade.** Show
`11 actions · 4 parallel · 2 resource conflicts` next to it in `--fg-2`.

**ScenePanel** — replaces the old fixed zone list. Two parts, both driven entirely by discovery:

```
   SCENE                             5 OBJECTS · 4 ZONES   [ SCAN ]
   ● red plastic cup      zone_1  0.91      ← dot = descriptor.color_hex
   ● blue folder          zone_3  0.88
   ● yellow box           zone_2  0.84   ⟵ "the scanner"   ← bound role, --think
   ─────────────────────────────────────
   PACK STATION      ●●     ◐ active
   PICK AISLE A      ●      ✓ satisfied
```

Row count is whatever the camera found. Never render a placeholder for an object that "should" be
there. When `object_appeared` fires, the new row **slides in** — a judge putting something new on the
table and watching a row appear is a five-second proof that this is live perception.

Zone rows: label, occupancy dots (in each object's sampled color), status glyph. `blocked` pulses
`--crit`. When a zone flips to `satisfied`, run a one-shot green sweep across the row. All rows
green at the end is the closing image.

**Grounding disambiguation** — when `grounding_ambiguous` arrives, the objective bar shows a calm
inline prompt, *not* a modal:

> Two red objects detected. Which is **the priority item**? → *click it in the world view*

Both candidates get a pulsing `--think` ring in the AR overlay; clicking either sends
`host_bind_object` and compilation resumes. Style this as deliberate, not as an error — it reads as
the system checking rather than guessing, which is exactly the right impression.

**MetricsPanel** — hidden until `goal_completed`, then it takes over the center column:

```
        INCIDENT STABILIZED

   3            5            4              1            1           0
   ZONES        RESPONDERS   PARALLEL       DEVIATION    RECOVERY    CONFLICTS
   RESTORED     COORDINATED  PEAK           DETECTED     COMPLETED

   ⏱ 01:34 total     ◈ 87% mean verification confidence     ↓ 41% responder idle time
```

64px numbers, 11px uppercase labels. Count numbers up over 600ms. This is the slide the judges
photograph — make it the cleanest thing on screen.

---

## 8. AdvancedControls.tsx

A bottom drawer, collapsed by default, labeled `▸ Advanced Controls` in `--fg-2` at 11px. Opens to
a 200px tray of small, dense, monospace-labeled buttons. It must be reachable in one click and
invisible from ten feet.

Grouped:

| Group | Controls |
| --- | --- |
| Verification | Force verify · Force fail · Skip action · Replay instruction |
| World | Set object zone · Move object · Remove object · Restore object |
| Responders | Set ready · Set unavailable · Reset worker · Reassign action |
| Failures | Inject: wrong object · missing object · timeout · worker down · regress |
| Plan | Load known-good graph · Override plan JSON · Recompile · Scripted recovery |
| Mode | Live · Assisted · Simulation · Spawn simulated workers |

Every one maps to a documented host WS message. Nothing here is bespoke UI logic — you send the
event, the backend does the work, the UI reacts to the resulting state. That way the rescue controls
exercise the *same* code path as the real thing and can't silently diverge.

Bind `⌘K` to a command palette over these. On stage, keyboard beats hunting for a button.

---

## 9. `useHiveSocket` contract (Ojas builds it, you consume it)

```ts
const { state, connected, send } = useHiveSocket('host');
send('host_compile_goal', { text });
```

Requirements you should hold him to:

- Auto-reconnect with backoff (250ms → 4s), and a **subtle** `RECONNECTING` chip in the header —
  not a modal, never a modal.
- `state` is one immutable object replaced on each update. Memoize derived values (`useMemo` on
  `topoLayers`) or React Flow will re-layout 4×/second and the graph will vibrate.
- Events arrive individually *and* in the snapshot. Dedupe by `seq`; render sorted by `seq`
  descending. Never sort by timestamp — clocks are not the ordering authority, `seq` is.

---

## 10. Hour-by-hour

| Block | Deliverable |
| --- | --- |
| **H0–H1** | Vite + Tailwind + tokens + layout shell with dummy data. Get the *shape* right first. |
| **H1–H2** | WorkerGrid + EventTimeline on live socket data |
| **H2–H4** | TaskGraph with React Flow, all status states, edge animation |
| **H4–H5** | GoalBar + ZonePanel + AdvancedControls |
| **H5–H6** | **DeviationOverlay** + recovery animation, driven by faked events |
| **H6–H7** | MetricsPanel, polish pass, projector test |
| **H7–H8** | Rehearse with Ojas; fix everything that reads badly from ten feet |

---

## 11. Ten-feet test (do this literally, three times)

Walk ten feet back from the laptop, and check:

- [ ] Can you tell which responders are active without squinting?
- [ ] Is the parallel column obviously *a column of four*?
- [ ] Does the deviation overlay read in under two seconds?
- [ ] Are any labels below 11px carrying meaning? (If yes, they're decoration — delete them.)
- [ ] Does anything move that isn't communicating a state change? (Delete it.)
- [ ] With the projector's washed-out contrast, is `--fg-2` still legible? (If not, lift to `#7A838F`.)
- [ ] Is the screen quiet when the system is idle? Idle should look *calm*, not dead — one slow
      breathing pulse on the collective status dot, nothing else.

**Projector reality check:** most conference projectors crush blacks and blow out saturation. Test
on an external display before the demo. If contrast is bad, raise `--bg-0` to `#0D0F12` and lift all
the semantic colors ~8% in lightness. Have that as a single-line CSS toggle you can flip in seconds.
