"""Phase 5: YOLO model-ийг бодит камер frame (эсвэл зураг) дээр шалгах хэрэгсэл.

    python main.py test-yolo                       # камерын live frame дээр 15 сек inference
    python main.py test-yolo --seconds 30 --display
    python main.py test-yolo --image path/to/photo.jpg   # камергүйгээр, нэг зураг дээр
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from camera.rtsp_camera import RTSPCamera
from config.settings import SERVICE_ROOT, Settings
from inference.detection_types import Detection, PPEClass
from inference.person_ppe import AnalyzerConfig, PersonPPEAnalyzer
from inference.visualize import draw_detections
from rules.violation_rules import PersonEvaluation, evaluate_people
from inference.yolo_detector import ModelLoadError, YoloDetector, _preview

TEMP_DIR = SERVICE_ROOT / "temp"

def _print_detections(index: int, detector: YoloDetector, detections: list[Detection]) -> None:
    counts = Counter(d.class_name for d in detections)
    summary = ", ".join(f"{name} x{n}" for name, n in sorted(counts.items())) or "nothing detected"
    print(f"[frame {index}] {detector.last_inference_ms:.0f} ms | {summary}")
    for det in sorted(detections, key=lambda d: (d.class_name, -d.confidence)):
        label = det.class_name.replace("_", " ").title()
        if det.is_a(PPEClass.PERSON):
            print(f"  {label} detected (confidence: {det.confidence:.2f})")
        else:
            print(f"  {label} confidence: {det.confidence:.2f}")


def _print_people(evaluations: list[PersonEvaluation]) -> None:
    for ev in evaluations:
        person = ev.person
        print(f"  {person.label}")
        print(f"    Helmet:  {person.helmet.value}")
        print(f"    Uniform: {person.uniform.value}")
        marker = "  <-- VIOLATION" if ev.violations else ""
        print(f"    Zone {ev.zone.value} => {ev.summary}{marker}")


def _load_detector(settings: Settings) -> Optional[YoloDetector]:
    detector = YoloDetector.from_settings(settings)
    try:
        detector.load()
    except ModelLoadError as exc:
        print(f"\nAI_MODEL_ERROR: {exc}\n")
        return None
    print(f"\nModel loaded: {detector.model_path.name}")
    print("Model classes: " + _preview(list(detector.model_class_names.values())))
    print("Mapped to:     " + (", ".join(sorted(detector.mapped_classes)) or "NONE"))
    if detector.missing_classes:
        print("MISSING:       " + ", ".join(detector.missing_classes) + "  (CLASS_MAPPING эсвэл custom model хэрэгтэй)")
    print()
    return detector


def run_yolo_image_test(settings: Settings, image_path: Path, zone: Optional[str] = None) -> int:
    frame = cv2.imread(str(image_path))
    if frame is None:
        print(f"Cannot read image: {image_path}")
        return 1
    detector = _load_detector(settings)
    if detector is None:
        return 1

    detector.detect(frame)  # warm-up: эхний inference удаан байдаг
    detections = detector.detect(frame)
    _print_detections(1, detector, detections)
    analyzer = PersonPPEAnalyzer(AnalyzerConfig.from_mapped_classes(detector.mapped_classes))
    people = analyzer.analyze(detections)
    evaluations = evaluate_people(people, zone or settings.camera_zone)
    _print_people(evaluations)

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TEMP_DIR / "yolo_test.jpg"
    cv2.imwrite(str(out), draw_detections(frame, detections, people))
    print(f"\nAnnotated image saved: {out}\n")
    return 0


def run_yolo_camera_test(
    settings: Settings, seconds: int = 15, display: bool = False, zone: Optional[str] = None
) -> int:
    from camera.camera_test import _check_config  # config шалгалт дахин ашиглана

    if not _check_config(settings):
        return 2
    detector = _load_detector(settings)
    if detector is None:
        return 1

    camera = RTSPCamera.from_settings(settings)
    print(f"Connecting to camera '{camera.name}' ({camera.masked_uri})")
    camera.start()

    deadline = time.time() + 20
    while camera.get_latest() is None and time.time() < deadline:
        time.sleep(0.1)
    if camera.get_latest() is None:
        camera.stop()
        print(f"\nCamera connection failed\nReason: {camera.last_error or 'No frames received within 20s'}\n")
        return 1

    detector.detect(camera.get_latest().frame)  # warm-up
    analyzer = PersonPPEAnalyzer(AnalyzerConfig.from_mapped_classes(detector.mapped_classes))
    print(f"AI inference started (zone: {(zone or settings.camera_zone).upper()})\n")

    last_id = -1
    processed = 0
    last_annotated: Optional[np.ndarray] = None
    started = time.time()
    try:
        while time.time() - started < seconds:
            packet = camera.get_latest()
            if packet is None or packet.frame_id == last_id:
                time.sleep(0.01)
                continue
            last_id = packet.frame_id
            detections = detector.detect(packet.frame)
            processed += 1
            _print_detections(processed, detector, detections)
            people = analyzer.analyze(detections)
            evaluations = evaluate_people(people, zone or settings.camera_zone)
            _print_people(evaluations)
            last_annotated = draw_detections(packet.frame, detections, people)
            if display:
                cv2.imshow("YOLO test (q to quit)", last_annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    except cv2.error as exc:
        print(f"Display unavailable ({exc}). --display ашиглахгүйгээр дахин ажиллуулна уу.")
    finally:
        camera.stop()
        if display:
            cv2.destroyAllWindows()

    elapsed = max(time.time() - started, 1e-6)
    print(f"\nProcessed {processed} frames in {elapsed:.1f}s ({processed / elapsed:.1f} inference FPS)")
    if last_annotated is not None:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        out = TEMP_DIR / "yolo_test.jpg"
        cv2.imwrite(str(out), last_annotated)
        print(f"Annotated frame saved: {out}\n")
    return 0 if processed else 1
