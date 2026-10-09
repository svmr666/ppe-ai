from datetime import datetime

import numpy as np

from collection.frame_collector import FrameCollector, safe_name, yolo_lines
from inference.detection_types import Detection

FRAME = np.full((100, 200, 3), 128, dtype=np.uint8)  # h=100, w=200
PERSON = Detection("person", 0.9, 20, 10, 120, 90)
UNIFORM = Detection("work_uniform", 0.8, 40, 30, 100, 80)


def collector(tmp_path, **setting):
    c = FrameCollector(tmp_path)
    c.configure({"enabled": True, "interval_seconds": 10, "max_images": 100, **setting})
    return c


def files(tmp_path, sub, ext):
    d = tmp_path / sub
    return sorted(d.glob(f"*.{ext}")) if d.is_dir() else []


def test_disabled_by_default_saves_nothing(tmp_path):
    c = FrameCollector(tmp_path)
    assert c.maybe_collect("Cam", FRAME, [PERSON], now=100.0) is False
    c.close()
    assert files(tmp_path, "raw", "jpg") == []


def test_saves_frame_and_yolo_prelabels(tmp_path):
    c = collector(tmp_path)
    assert c.maybe_collect("Mac-Camera", FRAME, [PERSON, UNIFORM], now=100.0, timestamp=datetime(2026, 10, 9, 8, 0, 0))
    c.close()
    (img,) = files(tmp_path, "raw", "jpg")
    assert img.name.startswith("Mac-Camera_20261009_080000") and img.stat().st_size > 100
    lines = (tmp_path / "labels_auto" / f"{img.stem}.txt").read_text().splitlines()
    assert lines[0].split()[0] == "0" and lines[1].split()[0] == "3"  # person=0, work_uniform=3
    cx, cy, w, h = (float(v) for v in lines[0].split()[1:])
    assert (cx, cy, w, h) == (0.35, 0.5, 0.5, 0.8)  # 200x100 зураг дээр нормчилсон
    assert not list((tmp_path / "raw").glob("*.tmp"))


def test_interval_and_empty_scene_rate(tmp_path):
    from datetime import timedelta

    t0 = datetime(2026, 10, 9, 8, 0, 0)
    at = lambda sec: dict(now=100.0 + sec, timestamp=t0 + timedelta(seconds=sec))
    c = collector(tmp_path)
    assert c.maybe_collect("Cam", FRAME, [PERSON], **at(0))
    assert not c.maybe_collect("Cam", FRAME, [PERSON], **at(5))        # 10 сек болоогүй
    assert c.maybe_collect("Cam", FRAME, [PERSON], **at(10))
    assert not c.maybe_collect("Cam", FRAME, [], **at(30))             # хоосон frame 5 дахин ховор (50 сек)
    assert c.maybe_collect("Cam", FRAME, [], **at(60))
    assert c.maybe_collect("Other", FRAME, [PERSON], **at(0))          # камер бүр тусдаа тоологдоно
    c.close()
    assert len(files(tmp_path, "raw", "jpg")) == 4


def test_max_images_cap_counts_existing_files(tmp_path):
    (tmp_path / "raw").mkdir()
    for i in range(3):
        (tmp_path / "raw" / f"old{i}.jpg").write_bytes(b"x")
    c = collector(tmp_path, max_images=4)
    assert c.maybe_collect("Cam", FRAME, [PERSON], now=100.0)           # 4 дэх нь
    assert not c.maybe_collect("Cam", FRAME, [PERSON], now=200.0)       # хязгаарт хүрсэн
    c.close()
    assert len(files(tmp_path, "raw", "jpg")) == 4


def test_turning_off_stops_collection(tmp_path):
    c = collector(tmp_path)
    c.configure({"enabled": False})
    assert not c.maybe_collect("Cam", FRAME, [PERSON], now=100.0)
    c.close()


def test_yolo_lines_skip_unknown_and_degenerate_boxes_and_clamp():
    lines = yolo_lines([
        Detection("car", 0.9, 0, 0, 50, 50),              # систем таньдаггүй анги
        Detection("helmet", 0.9, 10, 10, 11, 11),         # хэт жижиг
        Detection("person", 0.9, -20, -10, 400, 200),     # зургаас халилсан => хязгаарлана
    ], 200, 100)
    assert len(lines) == 1 and lines[0] == "0 0.500000 0.500000 1.000000 1.000000"


def test_safe_name_strips_unsafe_characters():
    assert safe_name("Mac Camera/../x") == "Mac-Camera-x" and safe_name("!!!") == "cam"
