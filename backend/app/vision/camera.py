"""Threaded camera capture. Never blocks the asyncio event loop, never raises.

Requests the camera's native MJPEG mode at 720p — USB webcams (this was tuned
against a Logitech C920) only unlock their higher resolutions/framerates in
that mode; the default raw YUY2 path caps out around 640x480. Consumers are
notified of new frames via a condition variable instead of polling on a fixed
sleep, so end-to-end latency tracks the camera's actual frame interval rather
than an arbitrary poll period.
"""
from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

log = logging.getLogger("hive.vision.camera")

DISPLAY_WIDTH = 1280
DISPLAY_HEIGHT = 720
REQUEST_FPS = 30
REOPEN_INTERVAL_S = 5.0


class Camera:
    def __init__(self, index: int = 0) -> None:
        self.index = index
        self._cap: cv2.VideoCapture | None = None
        self._frame: np.ndarray | None = None
        self._frame_version = 0
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._online = False
        self._last_open_attempt = 0.0
        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0

    @property
    def online(self) -> bool:
        return self._online

    def open(self) -> bool:
        """Attempt to open the device. Returns False, never raises, on failure."""
        self._last_open_attempt = time.time()
        try:
            cap = cv2.VideoCapture(self.index)
            if not cap.isOpened():
                cap.release()
                self._online = False
                return False
            # Order matters: width/height before FOURCC, or the driver silently
            # ignores the resolution request and falls back to its raw-mode cap.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, DISPLAY_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, DISPLAY_HEIGHT)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FPS, REQUEST_FPS)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # minimize driver-side frame queuing
            self._cap = cap
            self._online = True
            self._start_thread()
            return True
        except Exception:
            log.exception("camera open failed")
            self._online = False
            return False

    def _start_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _capture_loop(self) -> None:
        fps_count = 0
        fps_t0 = time.time()
        while not self._stop.is_set():
            try:
                cap = self._cap
                if cap is None:
                    time.sleep(0.1)
                    continue
                ok, frame = cap.read()
                if not ok or frame is None:
                    self._online = False
                    time.sleep(0.1)
                    continue
                self._online = True
                self.actual_height, self.actual_width = frame.shape[:2]
                with self._cond:
                    self._frame = frame
                    self._frame_version += 1
                    self._cond.notify_all()
                fps_count += 1
                now = time.time()
                if now - fps_t0 >= 1.0:
                    self.actual_fps = fps_count / (now - fps_t0)
                    fps_count = 0
                    fps_t0 = now
            except Exception:
                log.exception("camera capture loop")
                self._online = False
                time.sleep(0.5)

    def read(self) -> np.ndarray | None:
        """Returns the newest captured frame (a copy), or None. Non-blocking."""
        with self._cond:
            if self._frame is None:
                return None
            return self._frame.copy()

    def wait_for_new_frame(self, last_version: int, timeout: float = 1.0) -> tuple[np.ndarray | None, int]:
        """Blocks (call via asyncio.to_thread) until a frame newer than
        `last_version` arrives, or `timeout` elapses. Multiple independent
        consumers can each track their own last_version safely.
        Returns (frame_copy_or_None, new_version)."""
        with self._cond:
            got = self._cond.wait_for(lambda: self._frame_version > last_version, timeout=timeout)
            if not got or self._frame is None:
                return None, last_version
            return self._frame.copy(), self._frame_version

    def maybe_reopen(self) -> bool:
        """Retry opening at most every REOPEN_INTERVAL_S. Call from the vision tick."""
        if self._online:
            return True
        if time.time() - self._last_open_attempt < REOPEN_INTERVAL_S:
            return False
        return self.open()

    def release(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._cap is not None:
            self._cap.release()
        self._cap = None
        self._online = False
