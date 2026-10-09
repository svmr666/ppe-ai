"""CameraWorker: нэг камерын RTSP stream + FrameProcessor-ийг background thread-ээр ажиллуулна.

Phase 15-д CameraManager олон CameraWorker-ийг удирдана (камер бүр тусдаа worker).
Snapshot / storage / DB / WebSocket нь `on_violation(event, frame)` callback-аар залгагдана.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from camera.frame_processor import FrameProcessor, FrameResult
from camera.rtsp_camera import FramePacket, RTSPCamera
from utils.errors import ErrorCode
from utils.logger import get_logger, log_error
from violations.violation_service import ViolationEvent

logger = get_logger("camera.worker")

ViolationCallback = Callable[[ViolationEvent, np.ndarray], None]


@dataclass(frozen=True)
class WorkerSnapshot:
    packet: FramePacket
    result: FrameResult


class CameraWorker:
    def __init__(
        self,
        camera: RTSPCamera,
        processor: FrameProcessor,
        on_violation: Optional[ViolationCallback] = None,
    ) -> None:
        self.camera = camera
        self.processor = processor
        self._on_violation = on_violation

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last: Optional[WorkerSnapshot] = None

        self.ai_enabled = True  # false: frame уншина (видео явна) ч YOLO ажиллуулахгүй
        self.frames_processed = 0
        self.violations_emitted = 0
        self._started_at = 0.0

    @property
    def name(self) -> str:
        return self.camera.name

    @property
    def inference_fps(self) -> float:
        elapsed = time.time() - self._started_at
        return self.frames_processed / elapsed if elapsed > 0 and self.frames_processed else 0.0

    def latest(self) -> Optional[WorkerSnapshot]:
        with self._lock:
            return self._last

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._started_at = time.time()
        self.camera.start()
        self._thread = threading.Thread(target=self._run, name=f"worker-{self.name}", daemon=True)
        self._thread.start()
        logger.info("AI inference started | camera=%s zone=%s", self.name, self.processor.zone.value)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.camera.stop()

    def _run(self) -> None:
        last_id = -1
        while not self._stop.is_set():
            packet = self.camera.get_latest()
            if packet is None or packet.frame_id == last_id:
                time.sleep(0.01)
                continue
            last_id = packet.frame_id

            if not self.ai_enabled:
                with self._lock:
                    self._last = None  # боксын мэдээлэл түлхэхээ зогсооно
                continue

            try:
                result = self.processor.process(packet.frame, packet.timestamp)
            except Exception as exc:
                log_error(logger, ErrorCode.AI_MODEL_ERROR, f"Frame processing failed: {exc}", camera=self.name)
                time.sleep(0.5)
                continue

            self.frames_processed += 1
            with self._lock:
                self._last = WorkerSnapshot(packet, result)

            for event in result.events:
                self.violations_emitted += 1
                if self._on_violation:
                    try:
                        self._on_violation(event, packet.frame)
                    except Exception:
                        logger.exception("on_violation callback failed | camera=%s", self.name)
