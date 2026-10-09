import numpy as np
import pytest

from camera.frame_processor import FrameProcessor
from inference.detection_types import Detection
from inference.person_ppe import AnalyzerConfig, PersonPPE, PersonPPEAnalyzer, PPEState
from rules.violation_rules import ViolationType, evaluate_person
from rules.zone_rules import ZoneType
from violations.person_tracker import PersonTracker, iou
from violations.violation_service import ViolationEngine


def det(name, x1, y1, x2, y2, conf=0.9):
    return Detection(name, conf, x1, y1, x2, y2)


def box(x1, y1=100, x2=None, y2=500):
    return det("person", x1, y1, x2 if x2 is not None else x1 + 200, y2)


# ------------------------------------------------------------------ tracker
def test_iou_basics():
    assert iou(box(0), box(0)) == pytest.approx(1.0)
    assert iou(box(0), box(1000)) == 0.0


def test_tracker_keeps_id_when_person_moves_a_little():
    t = PersonTracker()
    (a,) = t.update([box(100)], 0.0)
    (b,) = t.update([box(115)], 0.1)
    assert a == b


def test_tracker_distinguishes_people_and_keeps_ids_when_order_changes():
    t = PersonTracker()
    ids = t.update([box(100), box(600)], 0.0)
    assert ids[0] != ids[1]
    swapped = t.update([box(605), box(105)], 0.1)  # detection тэмдэглэгээний дараалал өөр
    assert swapped == [ids[1], ids[0]]


def test_tracker_new_person_gets_new_id_and_old_expires():
    t = PersonTracker(max_age_seconds=2.0)
    (a,) = t.update([box(100)], 0.0)
    (b,) = t.update([box(100)], 5.0)  # 5 сек сураггүй => шинэ хүн
    assert a != b


# ------------------------------------------------------------------ engine
def evaluation(track_id, helmet=PPEState.NO, zone=ZoneType.FACTORY, conf=0.9):
    person = PersonPPE(
        index=track_id, person=box(100 * track_id), helmet=helmet, uniform=PPEState.YES,
        helmet_evidence=det("no_helmet", 0, 0, 1, 1, conf) if helmet is PPEState.NO else None,
        track_id=track_id,
    )
    return evaluate_person(person, zone)


def engine(**kw):
    kw.setdefault("cooldown_seconds", 10)
    kw.setdefault("confirm_window", 1)
    kw.setdefault("confirm_min_hits", 1)
    return ViolationEngine("Cam-01", **kw)


def test_spec_cooldown_timeline():
    eng = engine()
    emitted = [bool(eng.process([evaluation(1)], t)) for t in (0, 2, 4, 8, 12)]
    assert emitted == [True, False, False, False, True]  # 10:00:00 ... 10:00:12


def test_different_people_have_independent_cooldowns():
    eng = engine()
    first = eng.process([evaluation(1)], 0)
    both = eng.process([evaluation(1), evaluation(2)], 2)
    assert len(first) == 1
    assert [e.track_id for e in both] == [2]  # зөвхөн шинэ хүн


def test_same_person_different_violation_types_are_separate():
    eng = engine()
    person = evaluation(1)
    uniformless = PersonPPE(
        index=1, person=box(100), helmet=PPEState.NO, uniform=PPEState.NO,
        helmet_evidence=person.person.helmet_evidence, uniform_evidence=det("no_work_uniform", 0, 0, 1, 1),
        track_id=1,
    )
    events = eng.process([evaluate_person(uniformless, ZoneType.FACTORY)], 0)
    assert {e.violation_type for e in events} == {ViolationType.NO_HELMET, ViolationType.NO_WORK_UNIFORM}


def test_confirmation_requires_min_hits_in_window():
    eng = engine(confirm_window=5, confirm_min_hits=3)
    outcomes = [
        bool(eng.process([evaluation(1, helmet=h)], t))
        for t, h in enumerate([PPEState.NO, PPEState.YES, PPEState.NO, PPEState.YES, PPEState.NO])
    ]
    assert outcomes == [False, False, False, False, True]  # 3-р зөрчил (5 frame дотор) дээр баталгаажна


def test_single_frame_flicker_never_emits():
    eng = engine(confirm_window=5, confirm_min_hits=3)
    seq = [PPEState.YES, PPEState.NO, PPEState.YES, PPEState.YES, PPEState.YES, PPEState.YES]
    assert not any(eng.process([evaluation(1, helmet=h)], t) for t, h in enumerate(seq))


def test_unknown_and_compliant_never_emit():
    eng = engine()
    assert eng.process([evaluation(1, helmet=PPEState.UNKNOWN), evaluation(2, helmet=PPEState.YES)], 0) == []


def test_office_helmet_is_not_a_violation():
    assert engine().process([evaluation(1, zone=ZoneType.OFFICE)], 0) == []


def test_untracked_person_is_skipped():
    ev = evaluation(1)
    ev.person.track_id = None
    assert engine().process([ev], 0) == []


def test_event_fields_and_confidence_average():
    eng = engine(confirm_window=3, confirm_min_hits=2)
    eng.process([evaluation(1, conf=0.8)], 0)
    (event,) = eng.process([evaluation(1, conf=0.9)], 1)
    assert event.confidence == pytest.approx(0.85)
    assert event.person_id == "T1" and event.camera_name == "Cam-01"
    assert event.zone is ZoneType.FACTORY and event.detected_at.tzinfo is not None
    assert event.inferred is False


def test_invalid_engine_config():
    with pytest.raises(ValueError):
        ViolationEngine("x", 10, confirm_window=2, confirm_min_hits=3)


def test_purge_forgets_long_gone_tracks():
    eng = engine()
    eng.process([evaluation(1)], 0)
    eng.process([evaluation(2)], 100)  # track 1 90 сек харагдаагүй
    assert 1 not in eng._history and (1, ViolationType.NO_HELMET) not in eng._last_emitted


# ------------------------------------------------------------------ frame processor
class ScriptedDetector:
    """Test double: урьдчилан өгсөн detection-уудыг frame бүрт буцаана."""

    last_inference_ms = 1.0
    mapped_classes = {"person", "helmet", "no_helmet", "work_uniform", "no_work_uniform"}

    def __init__(self, script):
        self.script = list(script)

    def detect(self, frame):
        return self.script.pop(0)


def test_processor_end_to_end_two_people_one_violation():
    a, b = box(100), box(500)
    frame_dets = [
        a, b,
        det("helmet", 150, 100, 250, 170), det("no_helmet", 550, 100, 650, 170),
        det("work_uniform", 110, 200, 290, 450), det("work_uniform", 510, 200, 690, 450),
    ]
    processor = FrameProcessor(
        detector=ScriptedDetector([frame_dets] * 3),
        analyzer=PersonPPEAnalyzer(AnalyzerConfig()),
        tracker=PersonTracker(),
        engine=ViolationEngine("Cam-01", cooldown_seconds=10, confirm_window=3, confirm_min_hits=3),
        zone="FACTORY",
    )
    frame = np.zeros((600, 800, 3), np.uint8)
    results = [processor.process(frame, now=i * 0.1) for i in range(3)]

    assert [len(r.events) for r in results] == [0, 0, 1]
    (event,) = results[2].events
    assert event.violation_type is ViolationType.NO_HELMET
    assert event.track_id == results[2].people[1].track_id  # баруун талын хүн
    assert results[0].people[0].track_id != results[0].people[1].track_id
