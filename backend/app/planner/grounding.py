"""Grounding — binds the words of an objective to the objects the camera actually discovered.

This module is what makes HIVE promptable. Nothing about the scene is known ahead of time:
the vision pipeline hands us `obj_1 … obj_N` with *measured* descriptors, and an operator
types an arbitrary sentence about arbitrary things. Our job is to bind that sentence to real
observed ids, or to say honestly that we cannot.

Design rules that matter downstream:

* **Ambiguity is a first-class outcome, not an error.** Two near-tied candidates produce a
  `grounding_ambiguous` payload and the host clicks the right object. A wrong binding
  produces a confident plan that does the wrong thing, which is far worse.
* **Superlatives are selection operators, not soft features.** "the leftmost box" is a
  command to pick an extreme, so it scores decisively rather than nudging (see `_spatial`).
* Every bound phrase becomes the object's `role`, and every instruction downstream speaks
  the role — "move the priority item", never "move obj_3".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import ObservedObject, Scene

# ── HUE_NAMES ──────────────────────────────────────────────────────────────────
# The ONLY place in planner/ where colour words are literals. Everything else reads
# `descriptor.color_name`, which the vision pipeline measures at runtime.
# Ranges are OpenCV hue (0..179); red wraps, so it gets two.

HUE_NAMES: dict[str, tuple[tuple[int, int], ...]] = {
    "red": ((0, 10), (170, 179)),
    "orange": ((11, 22),),
    "yellow": ((23, 33),),
    "green": ((34, 85),),
    "cyan": ((86, 95),),
    "blue": ((96, 130),),
    "purple": ((131, 155),),
    "pink": ((156, 169),),
}

COLOR_SYNONYMS: dict[str, str] = {
    "crimson": "red",
    "scarlet": "red",
    "maroon": "red",
    "amber": "orange",
    "gold": "yellow",
    "golden": "yellow",
    "lime": "green",
    "olive": "green",
    "teal": "cyan",
    "aqua": "cyan",
    "turquoise": "cyan",
    "navy": "blue",
    "azure": "blue",
    "violet": "purple",
    "magenta": "purple",
    "lavender": "purple",
    "grey": "gray",
}

COLOR_ADJACENCY: dict[str, set[str]] = {
    "red": {"orange", "pink"},
    "orange": {"red", "yellow"},
    "yellow": {"orange", "green"},
    "green": {"yellow", "cyan"},
    "cyan": {"green", "blue"},
    "blue": {"cyan", "purple"},
    "purple": {"blue", "pink"},
    "pink": {"purple", "red"},
    "white": {"gray"},
    "gray": {"white", "black"},
    "black": {"gray"},
}

_NEUTRALS = ("white", "gray", "black")
# ── end HUE_NAMES ──────────────────────────────────────────────────────────────


def color_name_from_hsv(h: int, s: int, v: int) -> str:
    """Name a measured HSV sample. Vision owns the pixels; this owns the vocabulary."""
    if v < 55:
        return _NEUTRALS[2]
    if s < 45:
        return _NEUTRALS[0] if v > 190 else _NEUTRALS[1]
    for name, ranges in HUE_NAMES.items():
        for lo, hi in ranges:
            if lo <= h <= hi:
                return name
    return "unknown"


COLOR_WORDS: set[str] = set(HUE_NAMES) | set(COLOR_SYNONYMS) | set(_NEUTRALS)

SHAPE_WORDS: dict[str, str] = {
    "round": "round",
    "circular": "round",
    "cylindrical": "round",
    "curved": "round",
    "square": "rectangular",
    "rectangular": "rectangular",
    "boxy": "rectangular",
    "flat": "rectangular",
    "irregular": "irregular",
}
_ASPECT_WORDS = {"tall": "tall", "narrow": "tall", "wide": "wide", "long": "wide"}

SIZE_WORDS: dict[str, str] = {
    "big": "large",
    "large": "large",
    "bigger": "large",
    "biggest": "large",
    "largest": "large",
    "huge": "large",
    "bulky": "large",
    "small": "small",
    "smaller": "small",
    "smallest": "small",
    "tiny": "small",
    "tiniest": "small",
    "little": "small",
}

SPATIAL_WORDS: dict[str, str] = {
    "left": "west",
    "leftmost": "west",
    "west": "west",
    "western": "west",
    "right": "east",
    "rightmost": "east",
    "east": "east",
    "eastern": "east",
    "top": "north",
    "topmost": "north",
    "upper": "north",
    "north": "north",
    "back": "north",
    "bottom": "south",
    "lower": "south",
    "south": "south",
    "front": "south",
    "middle": "center",
    "center": "center",
    "centre": "center",
    "central": "center",
    "nearest": "center",
    "closest": "center",
}

_SUPERLATIVES = {
    "leftmost",
    "rightmost",
    "topmost",
    "nearest",
    "closest",
    "farthest",
    "furthest",
    "biggest",
    "largest",
    "smallest",
    "tiniest",
}

GENERIC_HEADS = {
    "one",
    "ones",
    "item",
    "items",
    "object",
    "objects",
    "thing",
    "things",
    "unit",
    "units",
    "piece",
    "pieces",
    "resource",
    "resources",
}

URGENCY_WORDS = {
    "expedited": 100,
    "expedite": 100,
    "priority": 100,
    "critical": 100,
    "urgent": 100,
    "urgently": 100,
    "immediately": 100,
    "immediate": 100,
    "asap": 100,
    "first": 100,
    "emergency": 100,
    "routine": 70,
    "normal": 70,
    "background": 50,
    "eventually": 50,
    "later": 50,
    "afterwards": 50,
    "restock": 50,
    "restocking": 50,
    "whenever": 50,
}

PRIORITY_ROUTINE = 70
PRIORITY_GATING = 85

_DETERMINERS = r"the|a|an|all\s+of\s+the|all\s+the|all|both|every|each|another|its|this|that|these|those"
_CUT_WORDS = {
    "to",
    "into",
    "onto",
    "in",
    "at",
    "on",
    "through",
    "across",
    "via",
    "past",
    "toward",
    "towards",
    "around",
    "over",
    "under",
    "between",
    "using",
    "inside",
    "from",
    "for",
    "with",
    "and",
    "or",
    "then",
    "until",
    "before",
    "after",
    "once",
    "while",
    "because",
    "so",
    "is",
    "are",
    "was",
    "were",
    "be",
    "being",
    "been",
    "has",
    "have",
    "needs",
    "need",
    "requires",
    "require",
    "must",
    "can",
    "cannot",
    "should",
    "will",
    "goes",
    "go",
    "gets",
    "get",
    "stays",
    "stay",
    "holds",
    "hold",
    "moves",
    "move",
}

_PLACE_HEADS = {
    "zone",
    "zones",
    "area",
    "areas",
    "station",
    "aisle",
    "dock",
    "bay",
    "shelf",
    "room",
    "wing",
    "point",
    "corner",
    "side",
    "bin",
    "table",
    "floor",
    "gym",
    "gymnasium",
    "exit",
    "muster",
    "staging",
    "site",
    "space",
    "region",
}

# Phrases that describe how to choose a place rather than naming one. They must not become
# unbound-zone chips, or the host gets asked to draw "its matching area" on the feed.
_DISTRIBUTIVE_WORDS = {"matching", "its", "their", "own", "respective", "correct", "right", "each"}

_CARDINALS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

MIN_SCORE = 0.22
AMBIGUITY_MARGIN = 0.15

_NP_RE = re.compile(
    rf"\b(?P<det>{_DETERMINERS})\s+(?P<body>(?:[A-Za-z][\w\-]*\s+){{0,3}}[A-Za-z][\w\-]*)",
    re.IGNORECASE,
)
_CODE_RE = re.compile(r"\b[A-Za-z]{2,}[-\s]?\d{2,}\b")
# Up to four words after the preposition; `_trim_body` does the real work of cutting at the
# next connector ("at the Pack Station using the red item" → "Pack Station"). A lookahead-based
# terminator silently missed long phrases, which quietly mis-paired every delivery after it.
_DEST_RE = re.compile(
    r"\b(?:to|into|onto|in|at|inside|toward|towards)\s+(?:the\s+)?"
    r"(?P<body>[A-Za-z][\w\-]*(?:\s+[A-Za-z][\w\-]*){0,3})",
    re.IGNORECASE,
)
_BARE_QUANT_RE = re.compile(
    r"\b(?:everything|all\s+of\s+(?:it|them)|the\s+rest|the\s+others?|the\s+remaining\s+\w+)\b", re.IGNORECASE
)
_REMAINDER_RE = re.compile(
    r"\bthe\s+(?:other|others|rest|remaining)(?:\s+(?P<count>two|three|four|five|\d+))?", re.IGNORECASE
)
_STACK_RE = re.compile(r"\bon\s+top\s+of\b|\bstack(?:ed)?\b|\bon\s+top\b|\bpile\b", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][\w\-]*", text.lower())


def _label_tokens(label: str) -> list[str]:
    toks = _tokens(label)
    articles = {"the", "of", "a", "an"}
    while toks and toks[0] in articles:
        toks.pop(0)  # leading article only — a trailing "A" is an identifier ("Pick Aisle A")
    return [t for i, t in enumerate(toks) if t not in articles or i == len(toks) - 1]


# ── result types ───────────────────────────────────────────────────────────────


@dataclass
class Binding:
    """A phrase from the objective, bound to observed object id(s).

    `object_ids` supports quantified phrases ("everything", "the other two"). `object_id`
    is the single-binding accessor the rest of the planner uses.
    """

    phrase: str
    object_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    alternatives: list[str] = field(default_factory=list)
    basis: str = ""
    span: tuple[int, int] = (0, 0)

    @property
    def object_id(self) -> str | None:
        return self.object_ids[0] if self.object_ids else None

    @property
    def bound(self) -> bool:
        return bool(self.object_ids)


@dataclass
class ZoneBinding:
    phrase: str
    zone_id: str
    confidence: float = 0.0
    basis: str = ""
    span: tuple[int, int] = (0, 0)


@dataclass
class Ambiguity:
    phrase: str
    candidates: list[str]
    message: str

    def as_payload(self) -> dict:
        return {"phrase": self.phrase, "candidates": self.candidates, "message": self.message}


@dataclass
class Gate:
    """"Packing cannot start until the scanner is docked" — a dependency stated in prose."""

    raw: str
    gate_object_ids: list[str] = field(default_factory=list)
    gated_zone_id: str | None = None


@dataclass
class GroundingResult:
    goal_text: str = ""
    bindings: list[Binding] = field(default_factory=list)
    zone_bindings: list[ZoneBinding] = field(default_factory=list)
    deliveries: list[tuple[str, str]] = field(default_factory=list)  # (object_id, zone_id)
    ambiguous: list[Ambiguity] = field(default_factory=list)
    unresolved_phrases: list[str] = field(default_factory=list)
    unbound_places: list[str] = field(default_factory=list)
    stack_relations: list[tuple[str, str]] = field(default_factory=list)  # (top, base)
    gates: list[Gate] = field(default_factory=list)
    urgency: dict[str, int] = field(default_factory=dict)  # object_id -> priority
    roles: dict[str, str] = field(default_factory=dict)  # object_id -> phrase

    @property
    def bound_object_ids(self) -> list[str]:
        seen: list[str] = []
        for b in self.bindings:
            for oid in b.object_ids:
                if oid not in seen:
                    seen.append(oid)
        return seen

    @property
    def object_count(self) -> int:
        return len(self.bound_object_ids)

    @property
    def destinations(self) -> list[str]:
        seen: list[str] = []
        for zb in self.zone_bindings:
            if zb.zone_id not in seen:
                seen.append(zb.zone_id)
        return seen

    @property
    def destination_count(self) -> int:
        return len(self.destinations)

    @property
    def distinct_destinations(self) -> int:
        return len({z for _, z in self.deliveries}) or self.destination_count

    @property
    def underdetermined(self) -> bool:
        """No object phrase resolved. The plan must still be buildable — see the template."""
        return not self.bound_object_ids

    def mentions_relation(self, text: str) -> bool:
        return text.lower() in self.goal_text.lower()

    def priority_for(self, object_id: str) -> int:
        return self.urgency.get(object_id, PRIORITY_ROUTINE)

    def ambiguous_payload(self) -> dict:
        first = self.ambiguous[0]
        return {**first.as_payload(), "pending": [a.as_payload() for a in self.ambiguous]}

    def apply_roles(self, scene: Scene) -> None:
        """Write bound phrases onto the objects so every downstream string speaks human."""
        for b in self.bindings:
            if len(b.object_ids) != 1:
                continue
            obj = scene.by_id(b.object_ids[0])
            if obj is not None and not obj.role:
                obj.role = b.phrase
                obj.role_confidence = round(b.confidence, 3)
        self.roles = {
            b.object_ids[0]: b.phrase for b in self.bindings if len(b.object_ids) == 1
        }

    def bind_manually(self, phrase: str, object_id: str) -> None:
        """Host clicked the right object on the feed (`host_bind_object`)."""
        self.ambiguous = [a for a in self.ambiguous if a.phrase != phrase]
        self.unresolved_phrases = [p for p in self.unresolved_phrases if p != phrase]
        for b in self.bindings:
            if b.phrase == phrase:
                b.object_ids = [object_id]
                b.confidence = 1.0
                b.basis = "host-assisted binding"
                return
        self.bindings.append(
            Binding(phrase=phrase, object_ids=[object_id], confidence=1.0, basis="host-assisted binding")
        )


# ── scoring ────────────────────────────────────────────────────────────────────


def _phrase_colors(toks: list[str]) -> set[str]:
    out = set()
    for t in toks:
        if t in HUE_NAMES or t in _NEUTRALS:
            out.add(t)
        elif t in COLOR_SYNONYMS:
            out.add(COLOR_SYNONYMS[t])
    return out


def _percentile(value: float, values: list[float]) -> float:
    if len(values) < 2:
        return 0.5
    lo, hi = min(values), max(values)
    return 0.5 if hi - lo < 1e-9 else (value - lo) / (hi - lo)


def _spatial_rank(objects: list[ObservedObject], direction: str) -> list[str]:
    """Object ids ordered best-first for a spatial word."""
    if direction == "west":
        key = lambda o: o.position.x  # noqa: E731
    elif direction == "east":
        key = lambda o: -o.position.x  # noqa: E731
    elif direction == "north":
        key = lambda o: o.position.y  # noqa: E731
    elif direction == "south":
        key = lambda o: -o.position.y  # noqa: E731
    else:  # center
        key = lambda o: abs(o.position.x - 0.5) + abs(o.position.y - 0.5)  # noqa: E731
    return [o.id for o in sorted(objects, key=key)]


def _score(phrase: str, obj: ObservedObject, scene: Scene) -> tuple[float, list[str]]:
    toks = [t for t in _tokens(phrase) if t not in {"the", "a", "an"}]
    content = [t for t in toks if t not in GENERIC_HEADS and t not in URGENCY_WORDS]
    pool = scene.visible_objects or scene.objects
    score, basis = 0.0, []

    # colour — 0.40
    want = _phrase_colors(toks)
    if want:
        have = obj.descriptor.color_name
        if have in want:
            score += 0.40
            basis.append("colour match")
        elif want & COLOR_ADJACENCY.get(have, set()):
            score += 0.20
            basis.append("adjacent hue")
        else:
            score -= 0.35  # named a colour this object is not: effectively excluded

    # semantic label overlap — 0.30
    label_toks = set(_label_tokens(obj.semantic_label or ""))
    sem_toks = [t for t in content if t not in COLOR_WORDS and t not in SHAPE_WORDS and t not in SIZE_WORDS]
    if sem_toks and label_toks:
        hit = sum(1 for t in sem_toks if t in label_toks)
        if hit:
            score += 0.30 * (hit / len(sem_toks))
            basis.append("label match")

    # shape — 0.15
    for t in toks:
        if t in SHAPE_WORDS:
            if SHAPE_WORDS[t] == obj.descriptor.shape_hint:
                score += 0.15
                basis.append("shape match")
            break
        if t in _ASPECT_WORDS:
            tall, wide = obj.descriptor.aspect < 0.9, obj.descriptor.aspect > 1.2
            if (_ASPECT_WORDS[t] == "tall" and tall) or (_ASPECT_WORDS[t] == "wide" and wide):
                score += 0.15
                basis.append("proportion match")
            break

    # spatial qualifier — 0.10, or 0.25 for a superlative (a selection operator)
    for t in toks:
        if t in SPATIAL_WORDS:
            ranked = _spatial_rank(pool, SPATIAL_WORDS[t])
            idx = ranked.index(obj.id) if obj.id in ranked else len(ranked) - 1
            if t in _SUPERLATIVES:
                if idx == 0:
                    score += 0.25
                    basis.append(f"{t} in the scene")
            elif len(ranked) > 1:
                graded = 0.10 * (1 - idx / (len(ranked) - 1))
                score += graded
                if graded > 0.05:
                    basis.append("position match")
            break

    # zone qualifier — "the item in the dock"
    for zb in _zone_phrases_in(phrase, scene):
        if obj.zone == zb:
            score += 0.10
            basis.append("in the named area")
        else:
            score -= 0.15
        break

    # size — 0.05, or 0.20 for a superlative
    areas = [o.descriptor.area_norm for o in pool]
    for t in toks:
        if t in SIZE_WORDS:
            pct = _percentile(obj.descriptor.area_norm, areas)
            want_large = SIZE_WORDS[t] == "large"
            if t in _SUPERLATIVES:
                extreme = max(areas) if want_large else min(areas)
                if abs(obj.descriptor.area_norm - extreme) < 1e-9:
                    score += 0.20
                    basis.append(f"{t} in the scene")
            else:
                score += 0.05 * (pct if want_large else 1 - pct)
                basis.append("size match")
            break

    # a bare generic head ("the one", "the item") is weak evidence of objecthood, not identity
    if not basis and any(t in GENERIC_HEADS for t in toks):
        score += 0.05

    return score, basis


def _zone_phrases_in(text: str, scene: Scene) -> list[str]:
    """Zone ids whose labels appear inside `text`."""
    low = text.lower()
    hits = []
    for z in scene.zones:
        lt = _label_tokens(z.label)
        if lt and all(t in low for t in lt):
            hits.append(z.id)
    return hits


def _match_zone(phrase: str, scene: Scene) -> tuple[str | None, float, str]:
    """Resolve a place phrase against discovered zones."""
    toks = [t for t in _tokens(phrase) if t not in {"the", "a", "an", "of"}]
    if not toks:
        return None, 0.0, ""
    best: tuple[str | None, float, str] = (None, 0.0, "")
    for z in scene.zones:
        lt = _label_tokens(z.label)
        if not lt:
            continue
        exact = sum(1 for t in toks if t in lt)
        # stem match catches "packing" → "Pack Station", "aisle b" → "Pick Aisle B"
        stem = sum(1 for t in toks if any(len(t) >= 4 and len(l) >= 4 and (t[:4] == l[:4]) for l in lt))
        hit = max(exact, stem)
        if not hit:
            continue
        coverage = hit / max(len(lt), len(toks))
        conf = 0.5 + 0.5 * coverage
        if conf > best[1]:
            best = (z.id, conf, f"matched zone label “{z.label}”")
    if best[0] is None and any(t in {"floor", "field", "anywhere"} for t in toks):
        return "field", 0.6, "unassigned floor space"
    return best


def resolve(phrase: str, scene: Scene) -> Binding:
    """Resolve one noun phrase against the live scene. Ambiguity is reported, never guessed."""
    pool = scene.visible_objects or scene.objects
    if not pool:
        return Binding(phrase=phrase, basis="no objects discovered")
    scored = sorted(
        ((o.id, *_score(phrase, o, scene)) for o in pool), key=lambda r: r[1], reverse=True
    )
    top_id, top_score, top_basis = scored[0]
    if top_score < MIN_SCORE:
        return Binding(phrase=phrase, confidence=max(0.0, top_score), basis="no confident match")
    runner = scored[1] if len(scored) > 1 else None
    alternatives = [oid for oid, s, _ in scored[1:] if s >= MIN_SCORE]
    binding = Binding(
        phrase=phrase,
        object_ids=[top_id],
        confidence=round(min(1.0, top_score), 3),
        alternatives=alternatives,
        basis=" + ".join(top_basis) or "best available match",
    )
    if runner and runner[1] >= MIN_SCORE and top_score - runner[1] < AMBIGUITY_MARGIN:
        binding.object_ids = []  # do not silently guess between near-ties
        binding.alternatives = [top_id] + alternatives
    return binding


# ── phrase extraction ──────────────────────────────────────────────────────────


def _trim_body(body: str) -> str:
    out: list[str] = []
    for tok in body.split():
        if tok.lower().strip(",.;") in _CUT_WORDS:
            break
        out.append(tok)
    while out and out[-1].lower() in _CUT_WORDS:
        out.pop()
    return " ".join(out)


def _extract_place_phrases(goal_text: str, scene: Scene) -> tuple[list[ZoneBinding], list[str], list[tuple[int, int]]]:
    zone_bindings: list[ZoneBinding] = []
    unbound: list[str] = []
    spans: list[tuple[int, int]] = []
    seen: set[tuple[str, int]] = set()

    def consider(raw: str, span: tuple[int, int]) -> None:
        phrase = _trim_body(raw).strip()
        if not phrase:
            return
        key = (phrase.lower(), span[0])
        if key in seen:
            return
        seen.add(key)
        span = (span[0], span[0] + len(phrase))  # only the place itself, not what follows it
        zone_id, conf, basis = _match_zone(phrase, scene)
        if zone_id:
            zone_bindings.append(ZoneBinding(phrase=phrase, zone_id=zone_id, confidence=conf, basis=basis, span=span))
            spans.append(span)
        elif any(t in _PLACE_HEADS for t in _tokens(phrase)):
            if not (set(_tokens(phrase)) & _DISTRIBUTIVE_WORDS):
                unbound.append(phrase)  # a place HIVE has never seen → host draws it
            spans.append(span)

    for m in _DEST_RE.finditer(goal_text):
        consider(m.group("body"), m.span("body"))
    # zone labels can also appear bare: "restock Pick Aisle B"
    for z in scene.zones:
        lt = _label_tokens(z.label)
        if not lt:
            continue
        pattern = r"\b" + r"\s+".join(re.escape(t) for t in lt) + r"\b"
        for m in re.finditer(pattern, goal_text, re.IGNORECASE):
            if any(s[0] <= m.start() < s[1] for s in spans):
                continue
            if not any(zb.span == m.span() for zb in zone_bindings):
                zone_bindings.append(
                    ZoneBinding(phrase=m.group(0), zone_id=z.id, confidence=0.95,
                                basis=f"matched zone label “{z.label}”", span=m.span())
                )
            spans.append(m.span())
    zone_bindings.sort(key=lambda zb: zb.span[0])
    return zone_bindings, unbound, spans


def _extract_object_phrases(goal_text: str, scene: Scene, place_spans: list[tuple[int, int]],
                            hints: list[str]) -> list[tuple[str, tuple[int, int]]]:
    out: list[tuple[str, tuple[int, int]]] = []
    seen: set[str] = set()

    def add(phrase: str, span: tuple[int, int]) -> None:
        phrase = phrase.strip()
        if not phrase or phrase.lower() in seen:
            return
        if any(span[0] < s[1] and s[0] < span[1] for s in place_spans):
            return  # this is a place, and places are resolved separately
        if any(span[0] < s[1] and s[0] < span[1] for _, s in out):
            return  # already covered by a longer phrase over the same words
        seen.add(phrase.lower())
        out.append((phrase, span))

    for hint in hints:  # scenario grounding hints: phrases likely to appear, nothing more
        for m in re.finditer(re.escape(hint), goal_text, re.IGNORECASE):
            add(m.group(0), m.span())

    # Scan manually rather than with finditer: the body pattern is greedy, so a match may
    # cover several phrases ("the Pack Station using the red item"). After trimming we resume
    # from the end of what we actually kept, or the phrases after it are silently swallowed.
    pos = 0
    while (m := _NP_RE.search(goal_text, pos)) is not None:
        body = _trim_body(m.group("body"))
        start = m.start("body")
        if not body:
            pos = m.end()
            continue
        add(f"{m.group('det')} {body}", (m.start(), start + len(body)))
        pos = start + len(body)

    for m in _CODE_RE.finditer(goal_text):  # SKU-1180, order 4471
        add(m.group(0), m.span())

    for m in _BARE_QUANT_RE.finditer(goal_text):  # "everything", "the rest", "the others"
        add(m.group(0), m.span())

    # bare colour/shape words with no determiner: "move red to the dock"
    for m in re.finditer(r"\b[A-Za-z][\w\-]*\b", goal_text):
        tok = m.group(0).lower()
        if tok in COLOR_WORDS and not any(tok in p.lower() for p, _ in out):
            add(m.group(0), m.span())
    return out


def _clause_of(span: tuple[int, int], goal_text: str) -> tuple[int, int]:
    starts = [0] + [m.end() for m in re.finditer(r"[.;]\s*", goal_text)]
    ends = [m.start() for m in re.finditer(r"[.;]", goal_text)] + [len(goal_text)]
    for s, e in zip(starts, ends):
        if s <= span[0] <= e:
            return s, e
    return 0, len(goal_text)


def _segment_bounds(span: tuple[int, int], goal_text: str) -> tuple[int, int]:
    bounds = [0] + [m.end() for m in re.finditer(r"[,.;]\s*", goal_text)] + [len(goal_text)]
    for start, end in zip(bounds, bounds[1:]):
        if start <= span[0] < end:
            return start, end
    return 0, len(goal_text)


def _segment_of(span: tuple[int, int], goal_text: str) -> str:
    """The comma-delimited fragment a phrase sits in.

    Narrower than the sentence on purpose: in "move the red item to the dock, and restock the
    orange item", "restock" must not drag the red item down to background priority.
    """
    start, end = _segment_bounds(span, goal_text)
    return goal_text[start:end]


def _urgency_for(span: tuple[int, int], goal_text: str) -> int | None:
    window = _segment_of(span, goal_text).lower()
    found = [prio for word, prio in URGENCY_WORDS.items() if re.search(rf"\b{re.escape(word)}\b", window)]
    if not found:
        return None
    urgent = [p for p in found if p > PRIORITY_ROUTINE]
    if urgent:
        return max(urgent)
    return min(found)


def _pair_deliveries(bindings: list[Binding], zone_bindings: list[ZoneBinding],
                     goal_text: str) -> list[tuple[str, str]]:
    """Pair each bound object with the destination its clause points at."""
    if not zone_bindings:
        return []
    deliveries: list[tuple[str, str]] = []
    for b in bindings:
        if not b.bound:
            continue
        # Same comma-segment first: "…at the Pack Station using the red item and the blue item,
        # and restock the orange item to Pick Aisle B" pairs red and blue with the Pack Station,
        # not with the aisle that happens to be mentioned next.
        ss, se = _segment_bounds(b.span, goal_text)
        pool = [zb for zb in zone_bindings if ss <= zb.span[0] < se]
        if not pool:
            cs, ce = _clause_of(b.span, goal_text)
            pool = [zb for zb in zone_bindings if cs <= zb.span[0] <= ce]
        if not pool and len(zone_bindings) == 1:
            pool = list(zone_bindings)
        if not pool:
            continue
        after = [zb for zb in pool if zb.span[0] >= b.span[1]]
        pick = after[0] if after else min(pool, key=lambda zb: abs(zb.span[0] - b.span[0]))
        for oid in b.object_ids:
            if (oid, pick.zone_id) not in deliveries:
                deliveries.append((oid, pick.zone_id))
    return deliveries


def _extract_gates(goal_text: str, bindings: list[Binding], scene: Scene) -> list[Gate]:
    gates: list[Gate] = []
    patterns = [
        r"(?P<gated>[^.;]*?)\b(?:can(?:not|'t|\s+not)|must\s+not|may\s+not|should\s+not)\s+"
        r"(?:start|begin|proceed|commence|happen|occur|run)\b[^.;]*?\b(?:until|before)\b(?P<cond>[^.;]+)",
        r"\b(?:only\s+)?(?:after|once)\b(?P<cond>[^,;.]+),\s*(?P<gated>[^.;]+)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, goal_text, re.IGNORECASE):
            cond_span = m.span("cond")
            gate_ids: list[str] = []
            for b in bindings:
                if b.bound and cond_span[0] <= b.span[0] <= cond_span[1]:
                    gate_ids.extend(b.object_ids)
            gated_zone = None
            zones_in_gated = _zone_phrases_in(m.group("gated"), scene)
            if zones_in_gated:
                gated_zone = zones_in_gated[0]
            else:
                zone_id, conf, _ = _match_zone(m.group("gated"), scene)
                gated_zone = zone_id if conf >= 0.5 else None
            if gate_ids or gated_zone:
                gates.append(Gate(raw=m.group(0).strip(), gate_object_ids=gate_ids, gated_zone_id=gated_zone))
    return gates


def _extract_stacks(goal_text: str, bindings: list[Binding]) -> list[tuple[str, str]]:
    marker = _STACK_RE.search(goal_text)
    if marker is None:
        return []
    # Only objects the stacking clause actually refers to. In "put the red cup in the dock and
    # stack the other two", the red cup is a delivery, not part of the tower.
    scoped = [b for b in bindings if b.bound and b.span[0] >= marker.start()]
    ordered = scoped or [b for b in bindings if b.bound]
    pairs: list[tuple[str, str]] = []
    for m in re.finditer(r"\bon\s+(?:top\s+of\s+)?", goal_text, re.IGNORECASE):
        before = [b for b in ordered if b.span[1] <= m.start()]
        after = [b for b in ordered if b.span[0] >= m.end()]
        if before and after:
            pairs.append((before[-1].object_ids[0], after[0].object_ids[0]))
    if not pairs:
        ids = [oid for b in ordered for oid in b.object_ids]
        pairs = [(ids[i], ids[i - 1]) for i in range(1, len(ids))]  # stack them in stated order
    return pairs


_PEOPLE_NOUNS = {
    "worker", "workers", "person", "people", "responder", "responders", "staff", "team",
    "teams", "everyone", "operator", "operators", "hands", "teacher", "teachers", "group",
    "groups",
}


def _resolve_quantified(phrase: str, scene: Scene, already: set[str]) -> tuple[list[str], str] | None:
    toks = _tokens(phrase)
    if set(toks) & _PEOPLE_NOUNS:  # "all five workers" quantifies people, not inventory
        return None
    pool = scene.visible_objects or scene.objects
    m = _REMAINDER_RE.search(phrase)
    if m:
        rest = [o.id for o in pool if o.id not in already]
        count = m.group("count")
        if count:
            n = _CARDINALS.get(count, None) or (int(count) if count.isdigit() else len(rest))
            rest = rest[:n]
        return (rest, "the remaining discovered objects") if rest else None
    if not ({"all", "every", "each", "everything", "both"} & set(toks)):
        return None
    want = _phrase_colors(toks)
    matched = [o for o in pool if not want or o.descriptor.color_name in want]
    if "both" in toks:
        matched = matched[:2]
    if not matched:
        return None
    return [o.id for o in matched], f"quantifier over {len(matched)} discovered objects"


# ── entry point ────────────────────────────────────────────────────────────────


def resolve_all(goal_text: str, scene: Scene, hints: list[str] | None = None) -> GroundingResult:
    """Bind an entire objective to the live scene.

    `hints` are scenario `expected_roles` — phrases *likely* to appear in the objective.
    They only help extraction; they never cause HIVE to invent an object it cannot see.
    """
    result = GroundingResult(goal_text=goal_text)
    zone_bindings, unbound_places, place_spans = _extract_place_phrases(goal_text, scene)
    result.zone_bindings = zone_bindings
    result.unbound_places = unbound_places

    phrases = _extract_object_phrases(goal_text, scene, place_spans, hints or [])

    # singles first, so "the other two" knows what is left over
    quantified: list[tuple[str, tuple[int, int]]] = []
    for phrase, span in phrases:
        toks = set(_tokens(phrase))
        if toks & {"all", "every", "each", "everything", "both", "other", "others", "rest", "remaining"}:
            quantified.append((phrase, span))
            continue
        binding = resolve(phrase, scene)
        binding.span = span
        if binding.bound:
            result.bindings.append(binding)
        elif binding.alternatives:
            result.bindings.append(binding)
            result.ambiguous.append(
                Ambiguity(
                    phrase=phrase,
                    candidates=binding.alternatives,
                    message=f"{len(binding.alternatives)} objects match “{phrase}”. Which one is it?",
                )
            )
        else:
            result.unresolved_phrases.append(phrase)

    already = set(result.bound_object_ids)
    for phrase, span in quantified:
        got = _resolve_quantified(phrase, scene, already)
        if got is None:
            result.unresolved_phrases.append(phrase)
            continue
        ids, basis = got
        result.bindings.append(
            Binding(phrase=phrase, object_ids=ids, confidence=0.9, basis=basis, span=span)
        )
        already |= set(ids)

    result.bindings.sort(key=lambda b: b.span[0])
    result.gates = _extract_gates(goal_text, result.bindings, scene)
    result.deliveries = _pair_deliveries(result.bindings, zone_bindings, goal_text)

    # "packing can't start until the scanner is docked" says where the scanner has to BE.
    # Without this the gate object has no action, and the stated dependency evaporates.
    for gate in result.gates:
        if not gate.gated_zone_id:
            continue
        for oid in gate.gate_object_ids:
            if not any(o == oid for o, _ in result.deliveries):
                result.deliveries.append((oid, gate.gated_zone_id))

    result.stack_relations = _extract_stacks(goal_text, result.bindings)

    gate_ids = {oid for g in result.gates for oid in g.gate_object_ids}
    for b in result.bindings:
        prio = _urgency_for(b.span, goal_text)
        for oid in b.object_ids:
            if prio is not None:
                result.urgency[oid] = prio
            elif oid in gate_ids:
                result.urgency[oid] = PRIORITY_GATING
    for oid in gate_ids:  # a blocking dependency outranks routine work
        result.urgency[oid] = max(result.urgency.get(oid, 0), PRIORITY_GATING)

    result.apply_roles(scene)
    return result
