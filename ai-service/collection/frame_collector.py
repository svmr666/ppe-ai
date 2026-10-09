"""Сургалтын өгөгдөл цуглуулагч.

Ажиллаж буй камеруудаас frame-ийг тогтмол хугацаанд хадгална (ХҮНИЙ шошглох зураг):
    <DATASET_DIR>/raw/<камер>_<цаг>.jpg           — бүрэн нягтралтай frame
    <DATASET_DIR>/labels_auto/<камер>_<цаг>.txt   — тухайн үеийн AI-ийн таамаг (YOLO формат) = шошглолтын эхлэл

Анхдагчаар УНТАРСАН. Dashboard → Сургалт хэсгээс admin асаана (DB-ийн `app_settings.dataset_collection`).
Зурагт хүмүүсийн царай орох тул зөвхөн локал дискэнд хадгалагдаж, git-д орохгүй (.gitignore: datasets/).

Хүн харагдсан frame-ийг `interval` тутам, хоосон frame-ийг (AI-ийн худал дохиог бууруулах сөрөг жишээ) `interval * EMPTY_FACTOR` тутам авна.
"""

from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np

from inference.detection_types import Detection
from utils.logger import get_logger

logger = get_logger("collection")

# backend/app/services/training_service.py-ийн CLASSES-тай ижил дараалал (YOLO class id)
CLASSES = ("person", "helmet", "no_helmet", "work_uniform", "no_work_uniform")
EMPTY_FACTOR = 5
JPEG_QUALITY = 92
_SAFE = re.compile(r"[^A-Za-z0-9]+")


def safe_name(camera_name: str) -> str:
    return _SAFE.sub("-", camera_name).strip("-")[:40] or "cam"


def yolo_lines(detections: Sequence[Detection], width: int, height: int) -> list[str]:
    lines = []
    for d in detections:
        if d.class_name not in CLASSES:
            continue
        x1, y1 = max(0.0, d.x1), max(0.0, d.y1)
        x2, y2 = min(float(width), d.x2), min(float(height), d.y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        lines.append(
            f"{CLASSES.index(d.class_name)} {(x1 + x2) / 2 / width:.6f} {(y1 + y2) / 2 / height:.6f} "
            f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}"
        )
    return lines


class FrameCollector:
    def __init__(self, base_dir: Path) -> None:
        self._raw = base_dir / "raw"
        self._auto = base_dir / "labels_auto"
        self._enabled = False
        self._interval = 10.0
        self._max_images = 5000
        self._count = 0
        self._last: dict[str, float] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="collector")
        self._pending = threading.Semaphore(4)  # диск удаан бол frame хуримтлуулахгүй, алгасна
        self._full_logged = False

    # ------------------------------------------------------------------ тохиргоо
    @property
    def enabled(self) -> bool:
        return self._enabled

    def configure(self, setting: Optional[dict]) -> None:
        """DB-ийн `dataset_collection` утгаар шинэчилнэ (manager sync бүрд)."""
        setting = setting or {}
        enabled = bool(setting.get("enabled", False))
        interval = float(setting.get("interval_seconds", 10) or 10)
        self._interval = min(max(interval, 1.0), 3600.0)
        self._max_images = max(1, int(setting.get("max_images", 5000) or 5000))
        if enabled and not self._enabled:
            self._count = self._count_existing()
            self._full_logged = False
            logger.info("Сургалтын өгөгдөл цуглуулалт АСЛАА | %d сек тутам, одоо %d зураг, дээд хязгаар %d", self._interval, self._count, self._max_images)
        elif not enabled and self._enabled:
            logger.info("Сургалтын өгөгдөл цуглуулалт УНТАРЛАА")
        self._enabled = enabled

    def _count_existing(self) -> int:
        if not self._raw.is_dir():
            return 0
        with os.scandir(self._raw) as it:
            return sum(1 for e in it if e.is_file() and e.name.endswith(".jpg"))

    # ------------------------------------------------------------------ цуглуулалт
    def maybe_collect(self, camera_name: str, frame: np.ndarray, detections: Sequence[Detection],
                      now: float, timestamp: Optional[datetime] = None) -> bool:
        """Шаардлагатай бол frame-ийг дараалалд оруулна. Хадгалахаар шийдсэн бол True."""
        if not self._enabled:
            return False
        if self._count >= self._max_images:
            if not self._full_logged:
                self._full_logged = True
                logger.warning("Цуглуулалт зогслоо: %d зураг (дээд хязгаар). Шошглоод/устгаад үргэлжлүүлнэ үү.", self._count)
            return False
        has_person = any(d.class_name == "person" for d in detections)
        wait = self._interval if has_person else self._interval * EMPTY_FACTOR
        key = safe_name(camera_name)
        if now - self._last.get(key, 0.0) < wait:
            return False
        if not self._pending.acquire(blocking=False):
            return False
        self._last[key] = now
        self._count += 1
        h, w = frame.shape[:2]
        stamp = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        self._pool.submit(self._write, f"{key}_{stamp}", frame.copy(), yolo_lines(detections, w, h))
        return True

    def _write(self, stem: str, frame: np.ndarray, lines: list[str]) -> None:
        try:
            self._raw.mkdir(parents=True, exist_ok=True)
            self._auto.mkdir(parents=True, exist_ok=True)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if not ok:
                raise RuntimeError("JPEG кодлож чадсангүй")
            (self._auto / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
            tmp = self._raw / f"{stem}.tmp"
            tmp.write_bytes(buf.tobytes())
            tmp.replace(self._raw / f"{stem}.jpg")  # зураг сүүлд гарна => шошго нь үргэлж бэлэн
        except Exception as exc:
            self._count = max(0, self._count - 1)
            logger.error("Сургалтын frame хадгалж чадсангүй: %s", type(exc).__name__)
        finally:
            self._pending.release()

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=False)
