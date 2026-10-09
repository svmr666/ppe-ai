from camera.frame_processor import FrameProcessor
from inference.detection_types import Detection
from inference.person_ppe import AnalyzerConfig, PersonPPEAnalyzer, PPEState
from violations.person_tracker import PersonTracker
from violations.violation_service import ViolationEngine


class StubDetector:
    last_inference_ms = 0.0

    def __init__(self, detections):
        self.detections = detections

    def detect(self, frame):
        return self.detections


def processor(detections):
    return FrameProcessor(
        detector=StubDetector(detections), analyzer=PersonPPEAnalyzer(AnalyzerConfig(helmet_absence_means_no=True, uniform_absence_means_no=True)),
        tracker=PersonTracker(0.25, 3.0), engine=ViolationEngine(camera_name="cam", camera_id=None, cooldown_seconds=10, confirm_window=5, confirm_min_hits=3), zone="FACTORY",
        min_assess_confidence=0.5,
    )


def run(proc, frames=8):
    events, result = [], None
    for i in range(frames):
        result = proc.process(None, now=100.0 + i)
        events += result.events
    return result, events


def test_confident_person_is_assessed_and_violates():
    result, events = run(processor([Detection("person", 0.9, 100, 50, 300, 450)]))
    p = result.people[0]
    assert p.helmet is PPEState.NO and p.uniform is PPEState.NO
    assert events  # PPE байхгүй => зөрчил


def test_weak_person_is_still_framed_but_never_judged():
    result, events = run(processor([Detection("person", 0.35, 100, 50, 300, 450)]))
    assert len(result.people) == 1  # хүрээлэгдэнэ
    p = result.people[0]
    assert p.helmet is PPEState.UNKNOWN and p.uniform is PPEState.UNKNOWN
    assert events == []  # сул илрүүлэлтээр худал зөрчил үүсэхгүй
