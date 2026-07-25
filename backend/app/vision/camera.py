"""Camera capture + frame ring buffer.

OWNER: Steven (capture) / Ojas (ring buffer feeds the reasoning burst).

Capture runs on a dedicated THREAD, never in the event loop: cv2.read() blocks ~30ms and
in the async loop that stutters every phone in the room. The thread writes the newest
frame into a single slot and appends JPEGs to a short ring buffer that the perception
layer samples when something interesting happens.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

import cv2
import numpy as np

from ..config import settings

log = logging.getLogger("hive.camera")

PROC_W, PROC_H = 640, 360


def probe_cameras(max_index: int = 4) -> list[dict]:
    """Enumerate working capture devices. Indices shift when devices are replugged, so
    the host UI offers a picker rather than trusting a constant."""
    out = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        try:
            if not cap.isOpened():
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            out.append(
                {
                    "index": i,
                    "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "brightness": round(float(np.mean(frame)), 1),
                    "active": i == settings.camera_index,
                }
            )
        except Exception:
            continue
        finally:
            cap.release()
    return out


class Camera:
    """Threaded capture with a JPEG ring buffer."""

    def __init__(self, index: int | None = None) -> None:
        self.index = index if index is not None else settings.camera_index
        self._cap: cv2.VideoCapture | None = None
        self._frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._fps = 0.0
        self._ring: deque[tuple[float, bytes]] = deque(maxlen=90)
        self.error: str | None = None

    # ── lifecycle ───────────────────────────────────────────────────────────

    @property
    def online(self) -> bool:
        return self._cap is not None and self._frame is not None

    @property
    def fps(self) -> float:
        return round(self._fps, 1)

    def open(self, index: int | None = None) -> bool:
        """Returns False (never raises) on a missing or denied device."""
        if index is not None and index != self.index:
            self.release()
            self.index = index
        if self._cap is not None:
            return True
        try:
            cap = cv2.VideoCapture(self.index)
            if not cap.isOpened():
                cap.release()
                self.error = f"camera {self.index} unavailable"
                log.warning(self.error)
                return False
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                self.error = f"camera {self.index} opened but produced no frame"
                log.warning(self.error)
                return False
            self._cap = cap
            self._frame = cv2.resize(frame, (PROC_W, PROC_H))
            self.error = None
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="hive-capture")
            self._thread.start()
            log.info("camera %s online", self.index)
            return True
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.warning("camera open failed: %s", self.error)
            return False

    def release(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
            self._thread = None
        if self._cap:
            self._cap.release()
            self._cap = None
        self._frame = None
        self._ring.clear()

    # ── capture thread ──────────────────────────────────────────────────────

    def _loop(self) -> None:
        last_jpeg = 0.0
        frames, t0 = 0, time.monotonic()
        while not self._stop.is_set():
            cap = self._cap
            if cap is None:
                break
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.05)
                continue
            small = cv2.resize(frame, (PROC_W, PROC_H))
            with self._lock:
                self._frame = small
            frames += 1
            now = time.monotonic()
            if now - t0 >= 1.0:
                self._fps = frames / (now - t0)
                frames, t0 = 0, now
            # Ring buffer at ~6 Hz — enough to sample a 2s burst at 2 fps without
            # holding a wall of JPEGs in memory.
            if now - last_jpeg >= 1 / 6:
                last_jpeg = now
                enc = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 72])[1]
                self._ring.append((now, enc.tobytes()))
            time.sleep(0.005)

    # ── access ──────────────────────────────────────────────────────────────

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def snapshot_jpeg(self, quality: int = 75) -> bytes | None:
        f = self.latest()
        if f is None:
            return None
        return cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()

    def burst(self, count: int = 5, seconds: float = 2.5) -> list[bytes]:
        """The most recent `count` frames spanning ~`seconds`, oldest first.

        This is what a reasoning burst consumes: 4–8 frames over the last couple of
        seconds beats one still, because most questions we ask are about CHANGE.
        """
        if not self._ring:
            return []
        now = time.monotonic()
        recent = [(t, j) for t, j in list(self._ring) if now - t <= seconds]
        if not recent:
            recent = list(self._ring)[-count:]
        if len(recent) <= count:
            return [j for _, j in recent]
        step = len(recent) / count
        return [recent[min(len(recent) - 1, int(i * step))][1] for i in range(count)]


camera = Camera()
