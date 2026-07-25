# Steven — Vision, World Model, AR Overlay & Simulation

> You own HIVE's eyes at the geometric layer. Two hard truths up front: (1) color tracking on a
> hackathon table under unknown lighting **will** misbehave, and (2) that is fine, because you are
> also building the simulation and assisted modes that make it not matter. Your job is not perfect
> perception — it is perception that degrades gracefully and *looks* extraordinary.
> Read `docs/CONTRACTS.md` first.

## 0. Integration status — read this first

Your camera, discovery, world model and simulator are merged and green.

**The core does not import your classes directly.** `backend/app/vision/bridge.py` (owner:
Ojas) holds the singletons and exposes the flat, per-tick API the orchestrator uses
(`refresh`, `scan`, `burst`, `snapshot_jpeg`, `set_object_zone`, …). If one of your
signatures changes, the bridge absorbs it and nothing else moves. It also adds the two
things the core needs that vision has no reason to provide: a JPEG ring buffer feeding the
reasoning bursts, and eased motion so simulated objects glide rather than teleport.

Two changes landed in **your files**:

1. **`simulator.py` — `_distinct_hue` now requires distinct *names*, not just distinct
   degrees.** "indigo" and "magenta" are far apart numerically but normalize to adjacent
   colour words, so a generated scene could make HIVE's own suggested objective ambiguous
   against the scene it had just discovered. A well-set-up table has objects a person can
   name unambiguously; a simulated one should be no different.
2. `models.py` gained `Scene.object_count` and a non-serialized `WorldState.objects` mirror,
   so your `state.world.objects = …` writes work while the wire keeps one source of truth.

`state.py` gained `mark_world_dirty`, `emit_nowait` and `override_active`, so your 10–20 Hz
loop shares one gap-free event sequence and one coalesced broadcast with everything else.

### Your split with Ojas — read this before writing any code

Ojas owns a **VLM perception layer** (`Ojas.md` §8b) running NVIDIA NIM models on the same camera
feed. You are not competing; you are two halves of one system with a clean division:

| | **You (CV)** | **Ojas (VLM)** |
| --- | --- | --- |
| Rate | 10 Hz, synchronous, always current | ~1.4 Hz, async, always slightly stale |
| Owns | `position`, `zone`, `visible`, `confidence`, object identity/tracking | `semantic_label`, `kind`, `held_by`, `stacked_on`, activity, people, anomalies |
| Strength | precision, latency, smooth motion, deterministic | meaning, judgment, relations, "what just happened" |
| Fails when | lighting, glare, similar hues | endpoint down, latency, cost |

**Neither is a single point of failure.** If the VLM is down, your tracker runs the demo alone. If
your tracker degrades, the VLM still reports what it sees. Build yours assuming the other doesn't
exist — then `fusion.py` combines them and everything gets better.

One consequence for you: **don't burn hours chasing `stacked_on` or `held_by` in OpenCV.** Those are
the VLM's job now. Spend that time on stable ids, zone hysteresis, and the AR overlay instead.

**Files you own**

```
backend/app/vision/camera.py
backend/app/vision/scene_discovery.py
backend/app/vision/world_model.py
backend/app/vision/calibration.py
backend/app/demo/simulator.py
frontend/src/components/WorldView.tsx        ← camera feed + AR overlay
frontend/src/components/CalibrationPanel.tsx
```

---

## 1. Three modes, one interface

The rest of HIVE calls exactly one function and never knows or cares which mode is active:

```python
def refresh(state: HiveState) -> None:
    """Update state.world.objects in place from the active source. Never blocks. Never raises."""
```

| Mode | Source of truth | Object confidence | When |
| --- | --- | --- | --- |
| `live` | webcam + generic scene discovery | 0.5–0.95 from detection quality | the real demo |
| `assisted` | webcam, but host clicks override tracking for N seconds | 1.0 on overridden objects | when lighting fights you |
| `simulation` | draggable virtual objects | 0.95 flat | no camera, testing, backup demo |

In simulation, objects are still **generated, not hardcoded** — `simulator.spawn_scene(n=5)` creates
N objects with randomized hues, positions, and shapes, then runs them through the identical
descriptor path. Simulation must never be the one mode where a fixed object list sneaks back in.

Mode switches at runtime via `host_set_mode` with **no restart**. Practice the switch; you may need
it live, and it must be one click and instant.

---

## 2. Hour-by-hour

| Block | Deliverable |
| --- | --- |
| **H0–H1** | `world_model.py` + `simulator.py` — simulation mode fully working. Unblocks everyone. |
| **H1–H2** | `WorldView.tsx` in simulation: zones, draggable objects, clean look |
| **H2–H3** | `camera.py` + `scene_discovery.py` (generic segmentation + `name_hue`), MJPEG endpoint |
| **H3–H4** | Association/tracking (stable ids), zone drawing + occupancy classification |
| **H4–H5** | `ScenePanel.tsx` — scan, review, correct. Zone auto-detect if time allows. |
| **H5–H6** | AR overlay on the live feed (the visual payoff) |
| **H6–H7** | Assisted mode, stability filtering, tuning under real venue light |
| **H7–H8** | Semantic labeling pass (optional), scan at the real table, rehearse |

**Build simulation first.** Everyone else is blocked without a world model, and nobody is blocked by
the camera. Simulation is also the mode most likely to be on screen if venue lighting is bad, so it
deserves the polish anyway.

**Then build discovery before tracking quality.** A generic pipeline that finds *whatever is there*
is worth more than a precise pipeline that only finds five preregistered colors — the first one
survives a judge swapping an object, the second one does not.

---

## 3. `camera.py`

```python
class Camera:
    def __init__(self, index: int): ...
    def open(self) -> bool          # returns False, never raises, on missing/denied device
    def read(self) -> np.ndarray | None
    def release(self) -> None
    @property
    def online(self) -> bool
```

Non-negotiables:

- **Capture runs in a thread, not the event loop.** `cv2.VideoCapture.read()` blocks for ~30ms. In
  the async loop that's five phones stuttering. Use a dedicated thread writing the newest frame
  into a single slot with a lock; the async side reads that slot.
- Target **10 FPS** for processing (`cv2.CAP_PROP_FPS` 15, downscale to 640×360). You are tracking
  five large blobs, not doing SLAM. Higher framerates buy nothing and cost CPU that the UI needs.
- On macOS the first `read()` triggers the camera permission prompt. **Open the camera during
  setup, not during the demo** — a permission dialog on stage is a disaster. Add a
  `POST /api/vision/warmup` and have Ojas call it from the pre-flight checklist.
- If `open()` fails, log once, emit `vision_unavailable`, and set mode to `simulation`. Do not
  retry in a loop; retry every 5s at most.

```python
async def run_forever():
    while True:
        try:
            if state.world.mode == "live" and camera.online:
                frame = await asyncio.to_thread(camera.read)
                if frame is not None:
                    tracker.process(frame)
                    world_model.ingest(tracker.detections)
        except Exception:
            log.exception("vision tick")     # never kill the loop
        await asyncio.sleep(0.1)
```

---

## 4. `scene_discovery.py` — **discover, don't expect**

> **This is the most important change to your workstream. Read it twice.**
>
> There is **no preset list of objects**. No `PROFILES = {"red": ..., "blue": ...}` dict keyed to
> meanings. HIVE must work when someone puts five objects it has never seen on a table it has never
> seen, and the presenter types a task nobody pre-wrote. A judge will absolutely test this by
> swapping an object. If we've hardcoded a manifest, we're caught, and the whole "it perceives the
> world" claim dies on the spot.

### The pipeline

Segment **salient regions generically**, then describe each one. No expectations, no target list.

```python
def discover(self, frame_bgr) -> list[Detection]:
    frame = cv2.GaussianBlur(frame_bgr, (5, 5), 0)
    hsv   = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    H, W  = frame.shape[:2]

    # 1. Saliency: anything meaningfully saturated and bright stands out from a plain surface.
    #    Thresholds adapt to the frame so it works under any lighting.
    s_thr = max(70, int(np.percentile(hsv[:, :, 1], 82)))
    v_thr = max(50, int(np.percentile(hsv[:, :, 2], 25)))
    mask  = cv2.inRange(hsv, (0, s_thr, v_thr), (179, 255, 255))
    mask  = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  KERNEL_5)
    mask  = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, KERNEL_11)

    # 2. Split touching blobs of different hue — two adjacent objects must not merge.
    labels = self._split_by_hue(mask, hsv)

    # 3. Describe each region. MEASURE, never look up.
    out = []
    for region in labels:
        area = cv2.contourArea(region)
        if not (MIN_AREA_FRAC * H * W <= area <= MAX_AREA_FRAC * H * W):
            continue                                  # reject dust and reject the whole table
        m = cv2.moments(region)
        cx, cy = m["m10"]/m["m00"], m["m01"]/m["m00"]
        px = self._region_pixels(hsv, region)
        h, s, v = np.median(px, axis=0)
        hull = cv2.convexHull(region)
        perim = cv2.arcLength(region, True)
        out.append(Detection(
            position   = Point(cx / W, cy / H),                    # NORMALIZED. Always.
            dominant_hsv = (int(h), int(s), int(v)),
            color_name = name_hue(h, s, v),                        # runtime function, see below
            color_hex  = hsv_to_hex(h, s, v),                      # the object's ACTUAL color
            area_norm  = area / (H * W),
            aspect     = self._aspect(region),
            circularity= 4 * np.pi * area / max(1.0, perim**2),
            solidity   = area / max(1.0, cv2.contourArea(hull)),
            confidence = self._confidence(area, solidity, s),
            bbox       = cv2.boundingRect(region)))
    return out
```

`_split_by_hue`: within each connected component, if the hue histogram is strongly bimodal
(two peaks >25° apart, each >20% of pixels), watershed-split it. Otherwise two touching objects
become one blob and the count is wrong — which the host would then have to correct by hand.

### `name_hue()` — a function, not a table

This is the **only** color knowledge in the system, and it is generic: it names an arbitrary
measured hue. It does not know what a red thing *is*.

```python
HUE_NAMES = [(0,"red"),(15,"orange"),(28,"yellow"),(38,"lime"),(52,"green"),
             (85,"teal"),(100,"cyan"),(115,"blue"),(135,"indigo"),(150,"purple"),
             (165,"magenta"),(179,"red")]

def name_hue(h, s, v) -> str:
    if v < 55:  return "black"
    if s < 40:  return "white" if v > 190 else "grey"
    return min(HUE_NAMES, key=lambda hn: circular_dist(h, hn[0]))[1]
```

Note it handles red's hue wrap naturally by listing red at both 0 and 179 and using circular
distance — no special-case `wraps=True` flag anywhere. That bug class disappears entirely.

### Identity & tracking

Ids are assigned at first sight and maintained by association, so an object keeps its id as it moves:

```python
def associate(self, detections, existing) -> dict[str, Detection]:
    """Hungarian assignment on cost = 3.0*position_dist + 1.0*hue_dist + 0.5*area_dist."""
```

- Matched under threshold → keep the existing `obj_N` id.
- Unmatched detection → **new object appeared**. Assign a fresh id, emit `object_appeared`.
  This is a first-class event: someone put something new on the table and HIVE noticed.
- Unmatched existing object → decay confidence; after ~1s emit `object_disappeared`.

Both feed Nikki's deviation detector. "A new object entered the workspace" is a legitimate,
impressive thing for the system to say out loud.

### Confidence — be honest, it's a feature

```python
def _confidence(self, area, solidity, sat) -> float:
    a  = min(1.0, area / TYPICAL_AREA)                 # big enough to be a real object?
    sd = min(1.0, solidity / 0.85)                     # solid blob, not fragments?
    st = min(1.0, sat / 140.0)                         # confidently chromatic?
    return round(min(0.95, 0.40 + 0.25*a + 0.20*sd + 0.15*st), 2)
```

Cap at **0.95**. Never report 1.0 from a camera. "84%" is far more credible to a technical judge
than "100%", and displaying honest uncertainty is part of the pitch.

### Calibration is now optional refinement, not setup

Because discovery is generic, **the system works the instant the camera opens** — nothing to
configure. The calibration panel (§6) becomes a *correction* tool: adjust the saliency thresholds if
the surface is busy, merge/split a mis-segmented region, or nudge min/max area. Present it that way.
"Zero setup, and here are the knobs if the environment fights us" is a much stronger story than
"first we teach it the five colors."

### Semantic labeling (optional, high payoff, ~40 lines)

**Ojas's VLM layer (`Ojas.md` §8b) does this now** — coordinate with him rather than duplicating
it. If you build the hook yourself: once per scan — **not per frame** — send a single annotated
JPEG to the NIM VLM with the discovered
bounding boxes drawn and numbered, and ask for a short label per box:

```python
async def label_scene(frame, detections) -> dict[str, str]:
    """Returns {object_id: 'red plastic cup'}. Timeout 6s. On any failure return {} and
       fall back to descriptor labels ('red round object'). NEVER blocks the loop."""
```

Prompt: *"Each numbered box marks one physical object on a work surface. Reply with JSON mapping
each number to a 2–4 word concrete description. No prose."*

The payoff: the host UI stops saying "red round object" and starts saying "red plastic cup," and
Zechariah's grounding can then resolve "the cup" as well as "the red one." If it fails, everything
still works on descriptors alone. Build it last, guard it hard.

### Temporal stability — the single most valuable filter you will write

A raw per-frame centroid jitters, and a one-frame glitch would fire a false deviation mid-demo.

```python
class StableTracker:
    HISTORY = 6
    def update(self, color, det):
        buf = self.hist[color]; buf.append(det)
        if len(buf) < 3: return None
        med = np.median([[d.position.x, d.position.y] for d in buf], axis=0)
        spread = np.std([[d.position.x, d.position.y] for d in buf], axis=0).mean()
        return Smoothed(position=Point(*med),
                        confidence=det.confidence * (1.0 if spread < 0.03 else 0.7),
                        settled=spread < 0.02)
```

**Rule: only report a zone change after the object has been stably in the new zone for 5
consecutive frames (~0.5s).** This kills nearly every false positive, including a hand passing over
an object. It also makes the deviation, when it fires, *always real* — which is what you want when
a judge is holding the object.

Add hysteresis at zone borders: an object must be 0.02 (normalized) *inside* a new zone's bounds to
be reassigned. Objects sitting on a taped line otherwise flicker between zones forever.

---

## 5. `world_model.py`

Owns the merge of every observation source into `state.world.objects`.

```python
def ingest(detections: list[Detection]) -> None:
    matches = associate(detections, state.scene.objects)     # id -> Detection, or None

    for obj in state.scene.objects:
        if obj.id in host_overrides and not expired(obj.id):
            continue                                          # assisted mode wins for 20s
        det = matches.get(obj.id)
        if det is None:
            obj.visible = False
            obj.confidence = max(0.0, obj.confidence - 0.08)  # decay, don't snap to 0
            if obj.confidence < 0.25 and obj.held_by is None:
                flag_missing(obj)                             # → deviation candidate
            continue
        smoothed = stable.update(obj.id, det)
        if smoothed is None: continue
        obj.position, obj.confidence, obj.visible = smoothed.position, smoothed.confidence, True
        obj.descriptor = merge_descriptor(obj.descriptor, det)   # slow-EMA the hue; objects don't
                                                                 # change color, but lighting drifts
        new_zone = classify_zone(smoothed.position)
        if new_zone != obj.zone and smoothed.settled:
            prev, obj.zone = obj.zone, new_zone
            emit_zone_change(obj, prev, new_zone)             # Nikki's deviation detector listens
        obj.last_updated_at = now(); obj.source = "vision"

    for det in unmatched(detections, matches):                # something NEW entered the workspace
        obj = register_new_object(det)                        # obj_N, measured descriptor, no role
        emit("object_appeared",
             f"New object detected in {label_of(obj.zone)}: {obj.display_label()}.", severity="warn")

    ws.mark_world_dirty()
```

Two notes that matter:

- **Never re-key objects by color.** `obj.id` is the identity; the descriptor is an attribute that
  can drift with lighting. Keying on color means an object that shifts hue under a shadow becomes a
  different object mid-action.
- **`object_appeared` is a feature, not noise.** A judge dropping a new item on the table and HIVE
  announcing it — then the planner being able to use it — is a genuinely strong moment. Make sure
  the event fires cleanly and doesn't spam (debounce 2 ticks, same as everything else).

**Held objects:** while `held_by` is set, suppress missing-object flags entirely. A hand covers the
object; that is expected, not a deviation. Getting this wrong produces spurious alarms at exactly
the wrong moment.

### Zones — also discovered, not hardcoded

Same principle as objects: no constant list. Zones arrive three ways.

**1. Auto-detect (`detect_zones`)** — find the taped rectangles:

```python
def detect_zones(frame) -> list[ProposedZone]:
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.bilateralFilter(g, 9, 60, 60), 50, 150)
    edges = cv2.dilate(edges, KERNEL_3)
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        approx = cv2.approxPolyDP(c, 0.03 * cv2.arcLength(c, True), True)
        area = cv2.contourArea(approx)
        if len(approx) == 4 and 0.02 < area / frame_area < 0.30 and is_convex(approx):
            out.append(ProposedZone(bounds=normalized_rect(approx), confidence=rect_quality(approx)))
    return dedupe(out)
```

Works well with thick black tape on a plain surface, which is exactly our setup. Propose them to the
host with confidence scores; the host accepts, edits, or discards. Getting 4 of 4 automatically is
a great moment; getting 3 of 4 and adjusting one is still fast.

**2. Drawn** — host drags rectangles on the live feed. ~15 seconds for four zones. This is the
reliable path; build it first, and treat auto-detect as the upgrade.

**3. Named from the objective** — Zechariah's grounding extracts place names from the goal text
("the packing area", "aisle B"). Any unbound name shows as a chip the host clicks, then drags onto
the feed. This is the flow that makes "type any task about any space" actually true.

```python
def classify_zone(p) -> str:
    for z in state.scene.zones:                # runtime list, never a module constant
        if z.bounds.contains(p, margin=HYSTERESIS): return z.id
    return "field"
```

Persist accepted zones to `scene_profile.json` keyed by a camera fingerprint, so a restart doesn't
cost you the setup. **You will re-tape the table at the venue and the camera angle will change** —
redrawing in 30 seconds beats editing constants, every time.

---

## 6. `calibration.py` + `ScenePanel.tsx` — correction, not setup

**Discovery runs with zero configuration.** The panel exists to *correct* it when the environment
fights you, and to let the host confirm what HIVE found. Frame it that way in the UI — the header is
`SCENE`, not `CALIBRATION`.

### The scan flow (the demo opener, ~5 seconds)

1. Host clicks **SCAN SCENE**.
2. Feed freezes for a beat; discovered regions animate in one by one with their sampled colors.
3. Panel lists what was found:

```
   SCENE · 5 OBJECTS · 4 REGIONS                    [ RESCAN ]
   ● obj_1   red round object          zone_1   0.91
   ● obj_2   blue rectangular object   zone_3   0.88
   ● obj_3   yellow round object       zone_4   0.84
   ● obj_4   green rectangular object  field    0.90
   ● obj_5   orange round object       zone_2   0.87
```

The dot for each row is `descriptor.color_hex` — the **actual sampled color**. Seeing the UI render
the true colors of whatever is on the table is the cheapest, most convincing proof that this is real
perception and not a script.

4. If semantic labeling succeeded, rows upgrade in place: `red round object` → `red plastic cup`.
   Animate that transition; it reads as the system understanding what it's looking at.

**This scan is a demo beat.** Do it live, in front of judges, with objects they can see. It takes
five seconds and it forecloses the "is this hardcoded?" question before anyone asks it.

### Correction controls

| Control | When you need it |
| --- | --- |
| Merge regions | one object split into two blobs (glare across the middle) |
| Split region | two touching objects merged into one |
| Delete region | it detected a hand, a phone, or the tape |
| Rename / bind role | host types a role, or clicks a chip from the grounding step |
| Saliency slider | busy surface → raise; dim room → lower |
| Min / max area | reject small clutter or the whole tabletop |
| Show mask | flips the feed to the binary mask — what HIVE actually sees |

```python
@app.post("/api/vision/scan")
async def scan(relabel: bool = True):
    frame = camera.snapshot()
    dets  = discovery.discover(frame)
    scene = world_model.rebuild_scene(dets)
    if relabel and settings.nvidia_api_key:
        with suppress(Exception):
            scene.apply_labels(await label_scene(frame, dets))   # best-effort, 6s timeout
    return scene
```

### Physical setup still matters more than any algorithm

Generic discovery is robust, but you can hand it an easy problem or a hard one:

- **Matte, strongly saturated objects.** Solid plastic cups beat printed paper. Glossy objects throw
  specular highlights that fragment a blob into three.
- **Plain matte surface** — black or white cloth. A wood-grain table adds texture the saliency
  threshold has to fight.
- **Camera 60–80cm above**, slight angle, no direct spotlight.
- **Pick objects with well-separated hues.** Discovery names whatever it sees, but *humans* saying
  "the yellow one" when yellow and orange are adjacent creates grounding ambiguity, not detection
  failure. Different failure mode, same fix: pick distinguishable colors at the store.

Diffuse light, matte objects, plain surface. Five minutes of setup buys more than an hour of code.

---

## 7. `WorldView.tsx` — the AR overlay

This is the component that makes people say "how is it doing that." An `<img src="/api/vision/frame.mjpg">`
with an absolutely-positioned SVG layer on top, both in the same normalized coordinate space.

MJPEG is deliberately chosen over WebRTC: ~20 lines of backend, zero negotiation, works everywhere.

```tsx
<div className="relative aspect-video rounded-[--r-md] overflow-hidden">
  <img src="/api/vision/frame.mjpg" className="w-full h-full object-cover opacity-90" />
  <svg viewBox="0 0 1000 563" className="absolute inset-0 w-full h-full">
    {zones.map(z => <ZoneOverlay key={z.id} zone={z} />)}
    {objects.map(o => <ObjectOverlay key={o.id} obj={o} />)}
    {activeLinks.map(l => <FlowLine key={l.id} {...l} />)}
  </svg>
</div>
```

### Overlay elements (use David's tokens)

- **Zone**: 1px rect in the zone's status color at 40% + a 6% fill. Corner ticks rather than a full
  border reads far more "instrument" than "rectangle." Label top-left, 10px uppercase, tracked.
- **Object**: a ring (not a filled dot) in the object's color, radius scaled by detection area, with
  a 2px crosshair at the centroid, drawn in the object's **sampled** `color_hex`. Label below is its
  bound role if it has one, else its semantic label: `SCANNER · 0.88` or `yellow cup · 0.84`.
  Ring **dashes** when
  `confidence < 0.6`, and pulses when it's the subject of the active action.
- **Trajectory**: keep the last ~15 positions per object and draw a fading polyline. Almost free,
  and it makes the overlay feel genuinely alive when objects move.
- **Flow line**: for a live action, an animated dashed line from the object to the target zone
  center, in the assigned worker's color. This is the "3D overlay on the real world" moment — the
  plan is literally drawn onto the physical table.
- **Deviation**: when an object leaves its expected zone, drop a `--crit` marker at the *expected*
  location with a dashed line to where it actually is, labeled `EXPECTED`. One glance tells the whole
  story. Judges love this specific element — build it.

Add a subtle perspective: skew the zone overlays ~4° with a CSS `transform: perspective(900px)
rotateX(6deg)` on the SVG layer if the camera is angled. It sells the "3D overlay on reality" read
without you doing any actual homography. If the camera is truly top-down, skip it — don't fake
depth that isn't there.

### Assisted mode

Click anywhere on the feed → pick an object from a small radial menu → that object's position is
overridden for 20 seconds at confidence 1.0, and an event fires:

> Host-assisted observation received.

Style this **subtly** — a brief ring at the click point, an ordinary info-level event line. It is a
legitimate operator capability (a supervisor correcting the model), not something to hide, but it
also shouldn't draw a spotlight.

---

## 8. `demo/simulator.py`

Simulation must be a first-class mode, not a placeholder. It runs the full stack: planner,
scheduler, verification, deviation, recovery, timeline.

```python
class Simulator:
    def drag(self, object_id, position): ...          # host drags in WorldView
    def auto_execute(self, action: Action):
        """Animate the object toward the target over ~1.5s, then report completion."""
    def spawn_workers(self, n=5): ...                 # fake worker sockets for solo testing
    def inject(self, kind: str, target_id: str|None): ...
```

Object motion should **interpolate over ~1.5 seconds**, never teleport. Watching an object glide
into a zone while the DAG node pulses and confidence climbs is genuinely convincing — that smooth
motion is most of why simulation mode looks premium instead of fake.

`spawn_workers` is the highest-leverage thing in this file: it lets any one of you demo the entire
system alone at 3am with no phones. Build it in hour one.

Simulated workers should also have realistic delay (1–3s to acknowledge, 3–6s to complete) and one
of them should occasionally be slow — that keeps the timeout logic honest during testing.

---

## 9. Failure injection you own

| Injection | Implementation |
| --- | --- |
| `wrong_object_move` | teleport an object into a wrong zone with high confidence |
| `object_removed` | `visible=False`, decay confidence to 0 over 1s |
| `verification_regress` | move an already-verified object out of its target zone |
| `zone_blocked` | mark a zone `critical`, scheduler must route around it |
| `vision_degraded` | multiply all confidences by 0.5 — proves the verifier needs worker confirmation |

Each is one host WS message and lands in the same code path as a real event.

---

## 10. Tests & the venue checklist

Tests (`backend/tests/test_vision.py`) — use synthetic frames, no camera needed in CI:

- `test_discovers_unknown_objects` — a synthetic frame with 4 arbitrary colored blobs yields exactly
  4 objects with no preconfiguration. **The most important test in your file.**
- `test_no_hardcoded_manifest` — grep-style assertion that discovery output depends only on the
  frame: same code, two different synthetic scenes, two different object sets
- `test_name_hue_wrap` — a pure-red patch names as `red` at both H≈2 and H≈178 (circular distance)
- `test_split_touching_blobs` — two adjacent differently-hued regions yield 2 objects, not 1
- `test_association_stable_ids` — an object moved 10% across frames keeps its id
- `test_new_object_appears` — an added blob gets a fresh id and fires `object_appeared`
- `test_zone_classification` — points map to the right zones, including the `field` fallback
- `test_zone_autodetect` — a synthetic frame with 4 taped rectangles proposes 4 zones
- `test_stability_filter` — a one-frame outlier does not produce a zone change
- `test_hysteresis` — an object oscillating on a border does not flap zones
- `test_missing_object_decay` — confidence decays and flags missing after N frames
- `test_held_object_not_flagged` — `held_by` set → no missing flag
- `test_simulator_completes_action` — auto-execute drives an object into the target zone

### Venue checklist (do this the moment you get access to the room)

- [ ] Camera mounted, table framed edge-to-edge, nothing important cropped
- [ ] Overhead lighting identified; no hard shadow or specular hotspot on the objects
- [ ] **Scan Scene finds every object, first try, in that light** — count is exactly right
- [ ] Sampled colors in the panel visibly match the real objects
- [ ] Adjacent-hue objects still segment as separate regions (raise saliency if not)
- [ ] Zones drawn or auto-detected to match the actual tape, saved
- [ ] Camera permission already granted, warmup called
- [ ] **Swap test:** replace one object with a different one, rescan → new object discovered and
      usable in a plan. Do this once in front of the team so everyone trusts it.
- [ ] Test: judge moves an object → deviation fires within 1 second, no false positives in 60s idle
- [ ] Fallback rehearsed: switch to simulation mid-run in one click without breaking execution
