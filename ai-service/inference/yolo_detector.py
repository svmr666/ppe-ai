"""YOLO inference service. Rules / camera-аас бүрэн тусдаа.

Input : OpenCV frame (BGR numpy array)
Output: list[Detection] — canonical class нэртэй (person, helmet, no_helmet, work_uniform, no_work_uniform)

Model-ийн class нэр canonical-тай таарахгүй бол CLASS_MAPPING (.env)-ээр хөрвүүлнэ.
Mapping-д байхгүй class-ыг үл тоомсорлоно (жишээ нь COCO model-ийн "car").
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from config.settings import Settings
from inference.detection_types import Detection, PPEClass
from utils.errors import ErrorCode
from utils.logger import get_logger, log_error

logger = get_logger("inference.yolo")


class ModelLoadError(RuntimeError):
    pass


def _normalize(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def _preview(names: list[str], limit: int = 12) -> str:
    return ", ".join(names[:limit]) + (f" ... (+{len(names) - limit} more)" if len(names) > limit else "")


class YoloDetector:
    def __init__(
        self,
        model_path: Path,
        confidence: float,
        iou: float,
        class_mapping: Optional[dict[str, str]] = None,
        imgsz: int = 640,
        device: str = "",
        person_confidence: Optional[float] = None,
    ) -> None:
        self._model_path = model_path
        self._confidence = confidence
        self._person_confidence = min(confidence, person_confidence) if person_confidence else confidence
        self._iou = iou
        self._imgsz = imgsz
        self._device = device or None
        self._user_mapping = {_normalize(k): _normalize(v) for k, v in (class_mapping or {}).items()}

        self._model = None
        self._id_to_canonical: dict[int, str] = {}
        self.model_class_names: dict[int, str] = {}
        self.last_inference_ms: float = 0.0
        self._lock = threading.Lock()  # олон камер нэг model-ийг хуваалцана; inference-ийг дарааллуулна

    @classmethod
    def from_settings(cls, settings: Settings) -> "YoloDetector":
        return cls(
            model_path=settings.resolved_model_path,
            confidence=settings.confidence_threshold,
            iou=settings.iou_threshold,
            class_mapping=settings.parsed_class_mapping,
            imgsz=settings.inference_imgsz,
            device=settings.inference_device,
            person_confidence=settings.person_confidence_threshold,
        )

    # ------------------------------------------------------------------
    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def mapped_classes(self) -> set[str]:
        return set(self._id_to_canonical.values())

    @property
    def missing_classes(self) -> list[str]:
        """Model-д байхгүй canonical class-ууд (PPE дүрэм ажиллахад хэрэгтэй)."""
        return [c.value for c in PPEClass if c.value not in self.mapped_classes]

    def load(self) -> None:
        logger.info("Loading YOLO model: %s", self._model_path)
        if not self._model_path.is_file():
            msg = (
                f"Model file not found: {self._model_path}. "
                "MODEL_PATH-ийг шалгаж, .pt файлаа ai-service/models/ руу хуулна уу."
            )
            log_error(logger, ErrorCode.AI_MODEL_ERROR, msg)
            raise ModelLoadError(msg)

        try:
            from ultralytics import YOLO  # lazy: torch import удаан

            self._model = YOLO(str(self._model_path))
        except Exception as exc:
            msg = f"Failed to load model {self._model_path.name}: {exc}"
            log_error(logger, ErrorCode.AI_MODEL_ERROR, msg)
            raise ModelLoadError(msg) from exc

        self.model_class_names = {int(k): str(v) for k, v in self._model.names.items()}
        self._build_class_map()

        names = list(self.model_class_names.values())
        logger.info("Model has %d classes: %s", len(names), _preview(names))
        logger.info("Mapped classes: %s", ", ".join(sorted(self.mapped_classes)) or "NONE")
        ignored = [n for i, n in self.model_class_names.items() if i not in self._id_to_canonical]
        if ignored:
            logger.info("Ignored classes (no mapping): %s", _preview(ignored))
        if self.missing_classes:
            logger.warning(
                "Model has no mapping for: %s — PPE rules need these. Set CLASS_MAPPING or use a trained model.",
                ", ".join(self.missing_classes),
            )

    def _build_class_map(self) -> None:
        canonical = {c.value for c in PPEClass}
        self._id_to_canonical = {}
        for class_id, name in self.model_class_names.items():
            norm = _normalize(name)
            target = self._user_mapping.get(norm, norm)
            if target in canonical:
                self._id_to_canonical[class_id] = target

        for target in self._user_mapping.values():
            if target not in canonical:
                logger.warning("CLASS_MAPPING target '%s' is not a canonical class %s", target, sorted(canonical))

    # ------------------------------------------------------------------
    def detect(self, frame: np.ndarray) -> list[Detection]:
        if self._model is None:
            raise ModelLoadError("Model is not loaded. Call load() first.")

        with self._lock:
            started = time.perf_counter()
            try:
                results = self._model.predict(
                    frame,
                    conf=min(self._confidence, self._person_confidence),  # хүнийг нам босгоор, бусдыг доор шүүнэ
                    iou=self._iou,
                    imgsz=self._imgsz,
                    device=self._device,
                    verbose=False,
                )
            except Exception as exc:
                log_error(logger, ErrorCode.AI_MODEL_ERROR, f"Inference failed: {exc}")
                raise
            self.last_inference_ms = (time.perf_counter() - started) * 1000

        detections: list[Detection] = []
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return detections

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        classes = boxes.cls.cpu().numpy().astype(int)
        for (x1, y1, x2, y2), conf, class_id in zip(xyxy, confs, classes):
            canonical = self._id_to_canonical.get(int(class_id))
            if canonical is None:
                continue
            if conf < (self._person_confidence if canonical == "person" else self._confidence):
                continue
            detections.append(
                Detection(canonical, float(conf), float(x1), float(y1), float(x2), float(y2))
            )
        return detections
