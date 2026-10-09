"""Нэг frame-ийг бүрэн боловсруулах pipeline (камерын нэг instance-д зориулагдсан):

    frame -> YOLO -> person+PPE -> tracker -> zone rules -> violation engine -> events

Камер бүр өөрийн FrameProcessor-той (tracker, engine-ийн төлөв камер бүрт тусдаа).
Detector нь ачаалах нь удаан тул камеруудын хооронд хуваалцаж болно.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from config.settings import Settings
from inference.detection_types import Detection
from inference.person_ppe import AnalyzerConfig, PersonPPE, PersonPPEAnalyzer, PPEState
from inference.yolo_detector import YoloDetector
from rules.violation_rules import PersonEvaluation, evaluate_people
from rules.zone_rules import ZoneType, parse_zone
from violations.person_tracker import PersonTracker
from violations.violation_service import ViolationEngine, ViolationEvent


@dataclass
class FrameResult:
    detections: list[Detection]
    people: list[PersonPPE]
    evaluations: list[PersonEvaluation]
    events: list[ViolationEvent] = field(default_factory=list)
    inference_ms: float = 0.0


class FrameProcessor:
    def __init__(
        self,
        detector: YoloDetector,
        analyzer: PersonPPEAnalyzer,
        tracker: PersonTracker,
        engine: ViolationEngine,
        zone: "ZoneType | str",
        min_assess_confidence: float = 0.0,
    ) -> None:
        self.min_assess_confidence = min_assess_confidence
        self.detector = detector
        self.analyzer = analyzer
        self.tracker = tracker
        self.engine = engine
        self.zone = parse_zone(zone)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        detector: YoloDetector,
        camera_name: str,
        zone: "ZoneType | str",
        camera_id: Optional[str] = None,
    ) -> "FrameProcessor":
        return cls(
            detector=detector,
            analyzer=PersonPPEAnalyzer(AnalyzerConfig.from_mapped_classes(detector.mapped_classes)),
            tracker=PersonTracker(settings.track_iou_threshold, settings.track_max_age_seconds),
            engine=ViolationEngine(
                camera_name=camera_name,
                camera_id=camera_id,
                cooldown_seconds=settings.violation_cooldown_seconds,
                confirm_window=settings.violation_confirm_window,
                confirm_min_hits=min(settings.violation_confirm_min_hits, settings.violation_confirm_window),
            ),
            zone=zone,
            min_assess_confidence=settings.confidence_threshold,
        )

    def process(self, frame: np.ndarray, now: Optional[float] = None) -> FrameResult:
        now = time.time() if now is None else now

        detections = self.detector.detect(frame)
        inference_ms = self.detector.last_inference_ms

        people = self.analyzer.analyze(detections)
        for person, track_id in zip(people, self.tracker.update([p.person for p in people], now)):
            person.track_id = track_id

        for person in people:
            if person.person.confidence < self.min_assess_confidence:
                # Сул (хол/хэсэгчлэн харагдсан) хүн: хүрээлнэ, гэхдээ каск/хувцасны дүгнэлт хийхгүй => худал зөрчил үүсэхгүй
                person.helmet = person.uniform = PPEState.UNKNOWN
                person.helmet_evidence = person.uniform_evidence = None

        evaluations = evaluate_people(people, self.zone)
        events = self.engine.process(evaluations, now)
        return FrameResult(detections, people, evaluations, events, inference_ms)
