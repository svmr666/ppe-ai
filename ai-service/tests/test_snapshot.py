from datetime import datetime, timezone

import cv2
import numpy as np
import pytest

from inference.detection_types import Detection
from rules.violation_rules import ViolationType
from rules.zone_rules import ZoneType
from snapshot.snapshot_service import SnapshotError, SnapshotService, local_filename
from violations.violation_service import ViolationEvent


def make_event(evidence=True, camera="Factory-Camera-01", person_box=None):
    return ViolationEvent(
        camera_name=camera, camera_id=None, zone=ZoneType.FACTORY,
        violation_type=ViolationType.NO_HELMET, track_id=3, confidence=0.91, inferred=False,
        detected_at=datetime(2026, 10, 8, 10, 35, 22, tzinfo=timezone.utc),
        person_box=person_box or Detection("person", 0.9, 700, 300, 900, 800),
        evidence_box=Detection("no_helmet", 0.91, 740, 300, 860, 400) if evidence else None,
    )


def frame(w=1280, h=720, value=128):
    return np.full((h, w, 3), value, np.uint8)


def decode(snapshot):
    return cv2.imdecode(np.frombuffer(snapshot.data, np.uint8), cv2.IMREAD_COLOR)


def test_produces_valid_jpeg_with_same_size():
    snap = SnapshotService().create(make_event(), frame())
    assert snap.data[:2] == b"\xff\xd8" and snap.content_type == "image/jpeg"
    img = decode(snap)
    assert img.shape == (720, 1280, 3) and (snap.width, snap.height) == (1280, 720)


def test_does_not_modify_original_frame():
    original = frame()
    before = original.copy()
    SnapshotService().create(make_event(), original)
    assert np.array_equal(original, before)


def test_draws_red_person_box_chip_and_dark_panel():
    img = decode(SnapshotService(jpeg_quality=100).create(make_event(), frame()))
    b, g, r = img[550, 701].astype(int)  # хайрцгийн зүүн ирмэг
    assert r > 170 and b < 120 and g < 120
    assert img[10:15, 24:60].mean() < 90  # зүүн дээд жижиг самбар бараан (текстгүй дотоод зай)
    assert img[200:260, 900:1000].mean() > 90  # самбар бусад зургийг дарахгүй (жижиг)


def test_chip_text_is_mongolian_and_cyrillic_font_available():
    from snapshot.snapshot_service import cyrillic_supported, ppe_label

    assert ppe_label("NO", "NO") == "Каскгүй · Ажлын хувцасгүй"
    assert ppe_label("YES", "UNKNOWN") == "Каскатай"
    assert ppe_label("UNKNOWN", "UNKNOWN") == ""
    assert cyrillic_supported()  # Монгол текст зөв зурагдах фонт олдсон


def test_inferred_violation_without_evidence_box_works():
    SnapshotService().create(make_event(evidence=False), frame())


def test_boxes_partially_outside_frame_do_not_crash():
    event = make_event(person_box=Detection("person", 0.9, -50, -20, 1400, 900))
    assert decode(SnapshotService().create(event, frame())).shape == (720, 1280, 3)


def test_works_on_small_and_large_frames():
    for w, h in ((320, 240), (1920, 1080), (3840, 2160)):
        img = decode(SnapshotService().create(make_event(), frame(w, h)))
        assert img.shape == (h, w, 3)


def test_non_ascii_camera_name_does_not_crash():
    SnapshotService().create(make_event(camera="Үйлдвэр-Камер-01"), frame())


def test_invalid_frame_raises():
    with pytest.raises(SnapshotError):
        SnapshotService().create(make_event(), np.zeros((10, 10), np.uint8))


def test_local_filename_is_safe_and_descriptive():
    assert local_filename(make_event()) == "20261008_103522_Factory-Camera-01_NO_HELMET_T3.jpg"
    assert "/" not in local_filename(make_event(camera="A/B ../c"))
