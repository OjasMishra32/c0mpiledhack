"""Vision workstream tests (Steven.md §10). Synthetic frames only — no camera
needed in CI."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.models import Point, Rect, Zone
from app.state import HiveState
from app.vision.scene_discovery import (
    Detection,
    SceneDiscovery,
    StableTracker,
    detect_zones,
    name_hue,
)
from app.vision.world_model import WorldModel
from app.demo.simulator import Simulator

W, H = 480, 320
BG = (60, 60, 60)  # low-saturation plain surface


def make_frame(blobs, size=(W, H), bg=BG):
    """blobs: list of (cx_norm, cy_norm, radius_px, bgr, shape) where shape in
    {'circle', 'rect'}."""
    img = np.zeros((size[1], size[0], 3), np.uint8)
    img[:] = bg
    for cx, cy, r, color, shape in blobs:
        px, py = int(cx * size[0]), int(cy * size[1])
        if shape == "circle":
            cv2.circle(img, (px, py), r, color, -1)
        else:
            cv2.rectangle(img, (px - r, py - r), (px + r, py + r), color, -1)
    return img


def bgr_for_hue(h, s=220, v=220):
    px = np.uint8([[[h, s, v]]])
    bgr = cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0][0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def taped_zones_frame(size=(W, H)):
    img = np.zeros((size[1], size[0], 3), np.uint8)
    img[:] = (230, 230, 230)
    rects = [
        (20, 20, 180, 120),
        (260, 20, 180, 120),
        (20, 180, 180, 120),
        (260, 180, 180, 120),
    ]
    for x, y, w, h in rects:
        cv2.rectangle(img, (x, y), (x + w, y + h), (10, 10, 10), 4)
    return img


# ---- discovery ------------------------------------------------------------

def test_discovers_unknown_objects():
    """A synthetic frame with 4 arbitrary colored blobs yields exactly 4 objects
    with no preconfiguration. The most important test in this file."""
    blobs = [
        (0.2, 0.3, 22, bgr_for_hue(0), "circle"),
        (0.5, 0.3, 22, bgr_for_hue(60), "rect"),
        (0.8, 0.3, 22, bgr_for_hue(115), "circle"),
        (0.5, 0.7, 22, bgr_for_hue(150), "rect"),
    ]
    frame = make_frame(blobs)
    dets = SceneDiscovery().discover(frame)
    assert len(dets) == 4


def test_no_hardcoded_manifest():
    """Same code, two different synthetic scenes, two different object sets."""
    scene_a = make_frame([
        (0.25, 0.5, 20, bgr_for_hue(0), "circle"),
        (0.75, 0.5, 20, bgr_for_hue(90), "circle"),
    ])
    scene_b = make_frame([
        (0.3, 0.3, 20, bgr_for_hue(30), "rect"),
        (0.7, 0.3, 20, bgr_for_hue(160), "rect"),
        (0.5, 0.7, 20, bgr_for_hue(60), "circle"),
    ])
    disc = SceneDiscovery()
    a = {d.color_name for d in disc.discover(scene_a)}
    b = {d.color_name for d in disc.discover(scene_b)}
    assert len(disc.discover(scene_a)) == 2
    assert len(disc.discover(scene_b)) == 3
    assert a != b


def test_name_hue_wrap():
    """A pure-red patch names as red at both H~2 and H~178 (circular distance)."""
    assert name_hue(2, 220, 200) == "red"
    assert name_hue(178, 220, 200) == "red"


def test_split_touching_blobs():
    """Two adjacent differently-hued regions yield 2 objects, not 1."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = BG
    cv2.rectangle(img, (150, 120), (230, 200), bgr_for_hue(0), -1)
    cv2.rectangle(img, (231, 120), (310, 200), bgr_for_hue(100), -1)
    dets = SceneDiscovery().discover(img)
    assert len(dets) == 2


# ---- world model / tracking ------------------------------------------------

def _det(x, y, hue=0, conf=0.9):
    from app.models import Descriptor
    return Detection(
        position=Point(x=x, y=y),
        dominant_hsv=(hue, 220, 200),
        color_name=name_hue(hue, 220, 200),
        color_hex="#FF0000",
        area_norm=0.02,
        aspect=1.0,
        circularity=0.85,
        solidity=0.9,
        confidence=conf,
        bbox=(0, 0, 10, 10),
    )


def make_world_model():
    state = HiveState()
    return WorldModel(state), state


def test_association_stable_ids():
    wm, state = make_world_model()
    for _ in range(4):
        wm.ingest([_det(0.2, 0.2)])
    assert len(state.scene.objects) == 1
    oid = state.scene.objects[0].id
    for _ in range(4):
        wm.ingest([_det(0.3, 0.2)])  # moved 10% across frame
    assert len(state.scene.objects) == 1
    assert state.scene.objects[0].id == oid


def test_new_object_appears():
    wm, state = make_world_model()
    wm.ingest([_det(0.2, 0.2)])
    before = len(state.scene.objects)
    wm.ingest([_det(0.2, 0.2), _det(0.8, 0.8, hue=100)])
    assert len(state.scene.objects) == before + 1
    assert any(e.type == "object_appeared" for e in state.events)


def test_zone_classification():
    wm, state = make_world_model()
    state.scene.zones = [Zone(id="zone_1", label="A", bounds=Rect(x=0.0, y=0.0, w=0.5, h=0.5))]
    assert wm.classify_zone(Point(x=0.1, y=0.1)) == "zone_1"
    assert wm.classify_zone(Point(x=0.9, y=0.9)) == "field"


def test_zone_autodetect():
    frame = taped_zones_frame()
    proposed = detect_zones(frame)
    assert len(proposed) == 4


def test_stability_filter():
    wm, state = make_world_model()
    for _ in range(3):
        wm.ingest([_det(0.2, 0.2)])
    state.scene.zones = [
        Zone(id="zone_1", label="A", bounds=Rect(x=0.0, y=0.0, w=0.3, h=0.3)),
        Zone(id="zone_2", label="B", bounds=Rect(x=0.6, y=0.6, w=0.3, h=0.3)),
    ]
    wm.ingest([_det(0.15, 0.15)])
    zone_before = state.scene.objects[0].zone
    wm.ingest([_det(0.9, 0.9)])  # one-frame outlier, should be smoothed away
    assert state.scene.objects[0].zone == zone_before


def test_hysteresis():
    wm, state = make_world_model()
    state.scene.zones = [
        Zone(id="zone_1", label="A", bounds=Rect(x=0.0, y=0.0, w=0.5, h=1.0)),
        Zone(id="zone_2", label="B", bounds=Rect(x=0.5, y=0.0, w=0.5, h=1.0)),
    ]
    for _ in range(4):
        wm.ingest([_det(0.48, 0.5)])
    assert state.scene.objects[0].zone == "zone_1"
    for _ in range(4):
        wm.ingest([_det(0.505, 0.5)])  # just barely across the border
    # within HYSTERESIS of the border: must not flip to zone_2 yet
    assert state.scene.objects[0].zone == "zone_1"


def test_missing_object_decay():
    wm, state = make_world_model()
    for _ in range(3):
        wm.ingest([_det(0.5, 0.5)])
    for _ in range(12):
        wm.ingest([])
    obj = state.scene.objects[0]
    assert obj.visible is False
    assert obj.confidence < 0.25
    assert any(e.type == "object_missing" for e in state.events)


def test_held_object_not_flagged():
    wm, state = make_world_model()
    for _ in range(3):
        wm.ingest([_det(0.5, 0.5)])
    state.scene.objects[0].held_by = "worker_a"
    for _ in range(12):
        wm.ingest([])
    assert not any(e.type == "object_missing" for e in state.events)


# ---- simulator --------------------------------------------------------

@pytest.mark.asyncio
async def test_simulator_completes_action():
    state = HiveState()
    sim = Simulator(state)
    sim.spawn_scene(n=1)
    obj = state.scene.objects[0]
    target = Point(x=0.9, y=0.9)
    await sim.auto_execute(obj.id, target)
    final = state.scene.objects[0]
    assert abs(final.position.x - target.x) < 0.01
    assert abs(final.position.y - target.y) < 0.01
