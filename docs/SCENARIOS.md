# HIVE — Scenario Library

## What a scenario is — and what it is emphatically not

**A scenario does NOT declare what objects exist.** Objects are discovered by the camera at runtime
(`docs/CONTRACTS.md` §0). A scenario is a bundle of *starting conditions and framing*: a suggested
objective string, a zone layout, UI vocabulary, and an emergency parachute graph.

The system must run correctly on a table it has never seen, with objects nobody registered, and a
goal nobody pre-wrote. Scenarios exist so we can rehearse quickly — not so the demo can cheat.

```python
@dataclass
class Scenario:
    id: str
    title: str
    subtitle: str                    # one line, shown under the HIVE logo
    suggested_goal: str              # PREFILLED, fully editable — not the only goal that works
    zone_labels: list[str]           # names only; geometry comes from detection or the host
    worker_roles: dict[str, str]     # worker_id -> role label ("Picker A", "Teacher · Wing A")
    worker_reachability: dict[str, list[str]]
    expected_roles: list[str]        # ["the priority item", "the scanner"] — GROUNDING HINTS ONLY.
                                     # Bound to whatever the camera finds. Never assumed present.
    known_good_graph: list[Action] | None   # the parachute; built over live ids at load time
    recommended_failure: str
    expected_recovery: str
    comms_profile: str = "voice"     # voice | silent
    lexicon: dict[str, str]          # UI copy overrides — see §6
```

`expected_roles` is the one field that could tempt someone back into hardcoding. It is a **hint for
the grounding scorer** — a nudge that "the priority item" is a phrase likely to appear — and nothing
more. If the table has no object that resolves to it, the plan simply doesn't include it. It must
never cause the system to invent an object it cannot see.

All scenarios live in `backend/app/demo/scenarios.py`.

**Zone ids are `zone_1`…`zone_N` + `field`, allocated at detection time.** Scenarios supply labels
in order. That way a scenario switch re-labels the same detected geometry without invalidating
Steven's setup — swapping from warehouse to campus takes two seconds and no re-scan.

The demo move: switch scenarios from the preset dropdown and let the whole interface re-label
itself. Same table, same objects, same code — now a warehouse instead of an incident site.
Retargeting HIVE to a new operation is a config file, not an engineering project.

---

## 1. `warehouse_fulfillment` — **THE FLAGSHIP (live demo)**

> This is what we run on stage, on the table, with the webcam and five phones.

### The story

A fulfillment floor. Five workers, each seeing only their own next task. An expedited order needs
picking, staging, and packing — while a restock runs in parallel. Then something changes on the
floor, and the plan is instantly stale.

Physical operations don't fail because nobody knows what to do. They fail because **plans go stale
faster than people can re-coordinate**, and every worker only sees their own corner of it.

### Physical setup

Four taped rectangles, labeled in this order (geometry auto-detected or drawn by the host):

| Zone | Label | Table position |
| --- | --- | --- |
| `zone_1` | **Inbound Dock** | west |
| `zone_2` | **Pack Station** | east |
| `zone_3` | **Pick Aisle A** | north |
| `zone_4` | **Pick Aisle B** | south |
| `field` | Floor | everywhere else |

**Five objects of your choosing**, well-separated in hue, matte, similarly sized. HIVE discovers
them on *Scan Scene* and the objective binds roles to them. What we set out for rehearsal:

| We use | Role the objective refers to | Why it matters |
| --- | --- | --- |
| a red cup | "the expedited item" (SKU-1180) | highest priority, drives triage |
| a blue cup | "the shared item" (SKU-2245) | **two orders need it, only one exists** |
| a green cup | "packing materials" | must reach Pack Station before packing |
| a yellow cup | "the scanner" | **gates the entire pack workflow** |
| an orange cup | "the bulk item" (SKU-3390) | restock, runs fully in parallel |

Swap any of them for something else and re-scan — the objective still resolves, as long as the
phrasing matches what's on the table ("the red one" → whatever is actually red). **Do this once in
rehearsal so the team trusts it**, and consider doing it live: hand a judge a random object, drop it
on the table, rescan, and re-compile. It is the strongest possible answer to "is this scripted?"

Workers: `Picker A` (west), `Picker B` (north), `Runner C` (center — reaches everything),
`Packer D` (east), `Restocker E` (south). Reachability follows their positions; **Runner C is the
only one who reaches all four zones**, which makes them the natural recovery choice and gives the
scheduler something meaningful to reason about.

### The objective (type this on stage)

> Fulfill expedited order 4471 at the pack station, and restock Pick Aisle B. Order 4471 needs
> SKU-1180 and SKU-2245. Packing cannot start until the scanner is docked and materials are staged.

### What HIVE produces (~11 actions)

Object ids below are whatever discovery assigned; roles in brackets are what grounding bound.

```
a1  move obj_A [expedited] → zone_2   priority 100  (no deps)   ┐
a2  move obj_B [shared]    → zone_2   priority 90   (no deps)   ├─ 4-wide opening wave
a3  move obj_D [scanner]   → zone_2   priority 85   (no deps)   │  ← gates everything
a4  move obj_E [bulk]      → zone_4   priority 60   (no deps)   ┘  ← restock, independent
a5  hold obj_D steady                 (deps a3)      locks object:obj_D
a6  move obj_C [materials] → zone_2   priority 80   (deps a3)
a7  release obj_D                     (deps a5, a6)
a8  inspect Pack Station              (deps a1, a2, a6, a7)
a9  move obj_B [shared]    → zone_3   priority 55   (deps a8)   ← contention: re-tasked
a10 inspect Pick Aisle B              (deps a4)
a11 final verification                (deps a8, a9, a10)
```

**Call out a9 explicitly to judges.** One unit of the shared SKU, two orders needing it. HIVE
sequences the conflict instead of failing on it — scheduling under scarcity, visible in the graph.

### Success predicates

Generated from the bindings, over live ids:

```
object_in_zone(expedited, zone_2) ∧ object_in_zone(materials, zone_2)
∧ object_in_zone(bulk, zone_4) ∧ object_in_zone(shared, zone_3)
∧ sequence_completed(a8, a10)
```

### The known-good graph is built at load time, not stored

The parachute graph is a *template over roles*. When the host clicks "Load known-good graph," it
resolves those roles against the current scene and emits actions over real ids. If a role can't be
bound, that branch is dropped and the rest still runs. A stored graph full of literal ids would
break the moment an object changed — which is exactly when you'd need the parachute.

### Recommended failure — **judge-triggered**

Hand the judge the yellow scanner and say: *"You're the floor. Change something."*

They move the scanner into Pick Aisle A.

| Step | What happens |
| --- | --- |
| Detection | Steven's tracker sees yellow leave `z2`, stable for 5 frames (~0.5s) |
| Trigger | `verification_regressed` — a verified predicate is now false |
| Overlay | **WORLD STATE DEVIATION** · expected Pack Station · observed Pick Aisle A |
| Isolation | pauses only a6, a7, a8 (the packing chain). **a4/a10 restock never stops.** |
| Recovery | inserts `retrieve scanner → Pack Station`, priority 105, rescored to nearest viable |
| Assignment | Runner C wins on reachability + idleness; explanation names the runner-up |
| Resume | packing chain unfreezes once the recovery action verifies |

The line to say out loud, after three seconds of silence:

> "It froze only the packing workflow. Picking and restock never stopped."

### Backup failure (fully deterministic)

If the judge hesitates: Advanced Controls → **Disable Picker B**. Their in-flight action reassigns
with a spoken-aloud explanation. Same story, zero dependence on vision.

---

## 2. `campus_emergency` — the vision use case (video only, simulation mode)

> **Not run live at the table.** This is the second half of the video: the same system, a different
> operation, where the stakes make the capability obvious.

### The story

A school goes into emergency lockdown. The coordinator has a phone, a PA system, and no way to know
which rooms are clear. Teachers can't confer — that's precisely the situation. Every staff member
needs a *different* instruction, silently, based on where they are and what is happening.

HIVE already does this. It is the identical loop: observe, decompose, assign privately, verify,
recover. Only the labels and the comms profile change.

### Setup (simulated floor plan, not the tabletop)

| Zone | Label |
| --- | --- |
| `zone_1` | **Wing A** |
| `zone_2` | **Wing B** |
| `zone_3` | **Gymnasium** |
| `zone_4` | **Muster Point** (exterior assembly) |
| `field` | Corridors |

Tracked entities become **class groups** being accounted for and routed. In simulation these are
generated, not hardcoded — `spawn_scene` creates them and grounding binds the roles:

| Role | Represents | Status tracked |
| --- | --- | --- |
| Group 1 | Wing A, 26 students | sheltering / evacuating / accounted |
| Group 2 | Wing B, 24 students | " |
| Group 3 | Gymnasium, 31 students | " |
| Group 4 | Wing A annex, 19 students | **the one that goes unaccounted** |
| Support | Facilities / nurse | mobile support |

Workers become staff: `Teacher · Wing A`, `Teacher · Wing B`, `Coach · Gym`,
`Front Office`, `Facilities`.

### The objective

> Evacuate Wings A and B to the muster point using routes that avoid the east corridor, hold the
> gymnasium in place until the route clears, and account for every group.

### What HIVE does that a PA announcement cannot

1. **Individually addressed routing.** Wing A goes north stairwell; Wing B goes west exit; the gym
   *holds* because their only route crosses the reported area. One broadcast cannot say three
   different things — and saying the wrong one to the wrong room is the failure mode.
2. **Silent delivery.** No audio, no vibration, minimum brightness, high contrast. Text only.
3. **Live accounting.** Each teacher reports a headcount; HIVE reconciles against the roster and
   surfaces the gap: `100 / 119 accounted · Group 4 unreported`.
4. **Recovery on new information.** Facilities reports the east corridor obstructed. HIVE reroutes
   only the affected groups — the same isolate-don't-restart logic as the warehouse — and reissues
   corrected routes to exactly those staff.
5. **Escalation via Voygr.** HIVE compiles a structured situation report from live state and places
   the call to the district safety line while the coordinator keeps both hands on the evacuation.

The demo beat is the same as the warehouse one, which is exactly the point: **it is the same code.**

### Voygr brief (generated from state, never freehand)

```
Automated escalation from HIVE, campus operations coordinator.

SITUATION REPORT
Site: Lincoln Campus — emergency lockdown, initiated 14:22
Zones: Wing A evacuating · Wing B evacuating · Gymnasium sheltering in place · Muster Point active
Accounted: 100 of 119 · Unreported: Group 4 (Wing A annex, 19)
Staff: 5 of 5 responding · 0 offline
Routing constraint: east corridor reported obstructed at 14:24; affected groups rerouted
Attempted automatically: reroute issued to 2 groups; 3 acknowledgement requests sent to Group 4 staff

ASK: confirm receipt, and confirm whether responders are en route to the Wing A annex.
```

### Framing discipline — read this before writing the script

Be precise and modest, because overclaiming here reads as reckless to exactly the judges we want:

- HIVE **assists staff executing an established emergency protocol.** It does not replace 911
  dispatch and does not direct law enforcement.
- Every instruction it sends is one a trained coordinator could have sent — HIVE sends it
  *individually, simultaneously, and silently*, which a human coordinator cannot.
- Life-safety decisions stay with humans. HIVE routes, accounts, reports, and escalates.
- Say plainly in the video: *"The coordinator is still in command. HIVE is how one person's decisions
  reach forty people at once, each one different, in under a second."*

That sentence is the whole product, and it is true in a warehouse too.

---

## 3. `incident_stabilization` — disaster response (alternate live demo)

The original scenario, kept because it's a strong fallback and a good second slide. Zones become
Medical Station / Emergency Shelter / Comms Station / Staging; objects become medical kit / water /
radio / battery / food. Same graph shape: water is the contended resource, the battery gates comms.

Keep it working. If the warehouse framing lands flat with a particular judge, switching scenarios
mid-conversation is a genuinely impressive move.

---

## 4. `resource_sort` — parallelism showcase

> Move every item to its matching zone.

Five independent actions, zero dependencies, all five workers active simultaneously. Runs in ~25
seconds and produces the highest `parallel_peak`. Use it as a warm-up if judges arrive early, or to
demonstrate throughput specifically.

## 5. `human_relay` — dependency showcase

> Pass the priority item through all five workers, then stage it at the dock.

Strictly serial: five actions, each depending on the last. The DAG renders as a straight line, which
is a nice visual contrast right after `resource_sort`'s wide column. Good for showing the handoff
verification (`object_held_by` transitions) and for demonstrating that a single worker dropping out
stalls the *chain* — then recovers.

---

## 6. The lexicon system

Every scenario carries a `lexicon` dict that overrides UI copy. This is how the same code reads as a
warehouse OS, an incident command system, or a campus safety platform.

```python
lexicon = {
  "collective":   "FLOOR TEAM",        # campus: "STAFF"       incident: "RESPONDERS"
  "worker":       "Operator",          # campus: "Staff"       incident: "Responder"
  "objective":    "WORK ORDER",        # campus: "PROTOCOL"    incident: "INCIDENT OBJECTIVE"
  "object":       "Item",              # campus: "Group"       incident: "Resource"
  "zone":         "Station",           # campus: "Wing"        incident: "Zone"
  "complete":     "ORDER FULFILLED",   # campus: "ALL CLEAR"   incident: "INCIDENT STABILIZED"
  "deviation":    "FLOOR STATE DEVIATION",  # campus: "SITUATION CHANGE"
}
```

David reads these through a `useLexicon()` hook; **no scenario-specific strings are hardcoded in
components.** Nikki's worker client uses the same hook. When you add a scenario, you write data —
nobody touches a component.

---

## 7. Scenario switching during the demo

The preset dropdown sends `host_compile_goal` with a `scenario_id`, which triggers a reset + reload.
Switching takes about two seconds and re-labels every zone, object, worker role, and headline in the
UI simultaneously.

Rehearse this transition. Done cleanly, it's the strongest architectural argument in the whole demo:

> "Same webcam. Same five people. Same code. I just told it it's a different operation."
