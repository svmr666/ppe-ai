"""Pipeline entrypoint-ууд.

  python main.py serve   — PRODUCTION: DB-ийн бүх идэвхтэй камерыг (dashboard-оос нэмсэн) ажиллуулна.
  python main.py run     — .env дахь НЭГ камерыг ажиллуулна (хөгжүүлэлт/шалгалт; CAMERA_SOURCE=mp4 ч болно).

Хоёулаа ижил CameraManager-ийг ашиглана:
  камер -> YOLO -> дүрэм -> violation -> snapshot -> Supabase Storage -> PostgreSQL -> backend -> WebSocket -> dashboard
"""

from __future__ import annotations

import time
from typing import Optional

import cv2

from camera.camera_config import config_from_settings
from camera.camera_manager import CameraManager, db_config_provider
from camera.camera_test import _check_config
from camera.rtsp_camera import request_webcam_permission
from config.settings import Settings
from inference.visualize import draw_detections
from inference.yolo_detector import ModelLoadError, YoloDetector
from snapshot.storage_uploader import UploadResult
from streaming.mediamtx import MediaMTXClient
from utils.backend_client import BackendNotifier
from utils.logger import get_logger
from violations.repository import DatabaseError, Repository
from violations.violation_handler import ViolationHandler
from violations.violation_service import ViolationEvent

logger = get_logger("pipeline")


def _print_violation(event: ViolationEvent) -> None:
    print(
        f"\n⚠️  VIOLATION {event.violation_type.value} | camera={event.camera_name} zone={event.zone.value} "
        f"person={event.person_id} confidence={event.confidence:.0%}{' (inferred)' if event.inferred else ''}"
    )


def build_handler(settings: Settings, repo: Optional[Repository], notifier: BackendNotifier) -> ViolationHandler:
    """Snapshot -> Storage upload -> PostgreSQL -> backend (WebSocket)."""

    def on_processed(event: ViolationEvent, upload: Optional[UploadResult]) -> None:
        if upload:
            print(f"   snapshot uploaded: {upload.storage_path}")
        if repo is None:
            return
        try:
            repo.insert_violation(event, upload)
        except DatabaseError:
            print("   DATABASE_ERROR: violation DB-д хадгалагдсангүй (log-г үз)")
            return
        print(f"   saved to database: {event.violation_id}")
        notifier.violation_created(event.violation_id)  # backend -> WebSocket -> dashboard

    return ViolationHandler.from_settings(settings, on_processed=on_processed)


def _load_detector(settings: Settings) -> Optional[YoloDetector]:
    detector = YoloDetector.from_settings(settings)
    try:
        detector.load()
    except ModelLoadError as exc:
        print(f"\nAI_MODEL_ERROR: {exc}\n")
        return None
    return detector


def _connect_db(settings: Settings, required: bool) -> tuple[Optional[Repository], bool]:
    """(repo, ok). Тохируулсан ч холбогдож чадахгүй бол ok=False (fail fast)."""
    if not settings.database_configured:
        if required:
            print("\nDATABASE_URL тохируулаагүй байна (ai-service/.env).\n")
            return None, False
        print("WARNING: DATABASE_URL тохируулаагүй — violation DB-д хадгалагдахгүй.\n")
        return None, True
    try:
        repo = Repository.from_settings(settings)
        repo.ping()
        return repo, True
    except DatabaseError as exc:
        print(f"\nDATABASE_ERROR: PostgreSQL-д холбогдож чадсангүй ({exc}). DATABASE_URL-ийг шалгана уу.\n")
        return None, False


def _loop(manager: CameraManager, seconds: int, display: bool) -> None:
    started = time.time()
    next_status = started + 10
    webcam_permission_asked = False
    try:
        while seconds == 0 or time.time() - started < seconds:
            # macOS: вэбкамерын зөвшөөрлийг үндсэн thread дээр нэг удаа асууна
            if not webcam_permission_asked and manager.has_webcam():
                webcam_permission_asked = True
                print("Вэбкамерын зөвшөөрөл шалгаж байна (macOS цонх гарвал «OK/Allow» дарна уу)...")
                if not request_webcam_permission():
                    print("Анхаар: камерын зөвшөөрөл олгогдоогүй. System Settings → Privacy & Security → Camera-д Terminal-д зөвшөөрөл өгөөд AI service-ийг дахин асаана уу.")
            if display:
                workers = manager.workers
                snap = workers[0].latest() if workers else None
                if snap:
                    cv2.imshow("AI Security (q to quit)", draw_detections(snap.packet.frame, snap.result.detections, snap.result.people))
                if cv2.waitKey(30) & 0xFF == ord("q"):
                    break
            else:
                time.sleep(0.2)
            if time.time() >= next_status:
                next_status += 10
                for w in manager.workers:
                    print(
                        f"[status] camera={w.name} state={w.camera.state.value} inference={w.inference_fps:.1f} fps "
                        f"violations={w.violations_emitted}"
                    )
                if not manager.workers:
                    print("[status] идэвхтэй камер алга (dashboard дээр камер нэмж Connect хийнэ үү)")
    except KeyboardInterrupt:
        pass
    except cv2.error as exc:
        print(f"Display unavailable ({exc}). --display ашиглахгүйгээр дахин ажиллуулна уу.")
    finally:
        if display:
            cv2.destroyAllWindows()


def _run(settings: Settings, provider_factory, repo, seconds: int, display: bool, notifier) -> None:
    detector = _load_detector(settings)
    if detector is None:
        return
    handler = build_handler(settings, repo, notifier)
    manager = CameraManager(
        settings, detector, repo, notifier, provider_factory(), handler, on_violation_print=_print_violation,
        streams=MediaMTXClient.from_settings(settings),
    )
    manager.start()
    print("Ctrl+C = stop\n")
    try:
        _loop(manager, seconds, display)
    finally:
        manager.stop()
        handler.shutdown(wait=True)  # дуусаагүй upload/DB бичилтүүдийг хүлээнэ
        if repo:
            repo.close()
        notifier.close()
        frames = sum(w.frames_processed for w in manager.workers)
        print(f"\nStopped. Frames processed (active workers): {frames}")


def run_pipeline(settings: Settings, seconds: int = 0, display: bool = False, zone: Optional[str] = None) -> int:
    """`.env`-ийн нэг камер."""
    if not _check_config(settings):
        return 2
    zone_name = (zone or settings.camera_zone).upper()
    repo, ok = _connect_db(settings, required=False)
    if not ok:
        return 1

    camera_id = "env-camera"
    if repo:
        try:
            camera_id = repo.ensure_camera(
                name=settings.camera_name, ip=settings.camera_ip or "mp4-file", port=settings.camera_rtsp_port,
                username=settings.camera_username if settings.camera_source == "rtsp" else "-",
                camera_type=settings.camera_type, channel=settings.camera_channel, subtype=settings.camera_subtype,
                zone=zone_name,
            ).id
        except DatabaseError as exc:
            print(f"\nDATABASE_ERROR: камер бүртгэж чадсангүй ({exc})\n")
            return 1

    config = config_from_settings(settings, camera_id, zone_name)
    print(f"Camera: {config.name} | zone={zone_name} | cooldown={settings.violation_cooldown_seconds:.0f}s | "
          f"confirm={settings.violation_confirm_min_hits}/{settings.violation_confirm_window} frames")
    _run(settings, lambda: (lambda: [config]), repo, seconds, display, BackendNotifier.from_settings(settings))
    return 0


def run_serve(settings: Settings, seconds: int = 0, display: bool = False) -> int:
    """DB-ийн бүх идэвхтэй камер (production)."""
    repo, ok = _connect_db(settings, required=True)
    if not ok or repo is None:
        return 1
    notifier = BackendNotifier.from_settings(settings)
    print(f"Serving cameras from database (sync every {settings.camera_sync_interval_seconds:.0f}s)")
    _run(settings, lambda: db_config_provider(repo, settings.camera_secret_key, notifier), repo, seconds, display, notifier)
    return 0
