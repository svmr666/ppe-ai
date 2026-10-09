"""CameraManager: олон камерыг нэг дор удирдана (камер бүр тусдаа CameraWorker + FrameProcessor).

    DB (cameras, enabled) --sync--> start / stop / restart workers
    worker --violation--> snapshot -> Supabase Storage -> PostgreSQL -> backend (WebSocket)
    worker --last frame--> backend (dashboard-ийн live харагдац)
    camera state --> PostgreSQL + backend (dashboard-д шууд)

Камер нэмэх/засах/Connect/Disconnect хийхэд код өөрчлөх шаардлагагүй: manager DB-г тогтмол уншиж өөрийгөө тохируулна.
YOLO model нэг, камеруудын хооронд хуваалцагдана (inference дарааллаар). 4 камерт CPU дээр FPS хуваагдана — GPU зөвлөнө.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from camera.camera_config import CameraConfig, CameraConfigError, config_from_db_row
from camera.camera_worker import CameraWorker
from camera.frame_processor import FrameProcessor
from camera.rtsp_camera import CameraState
from collection.frame_collector import FrameCollector
from config.settings import Settings
from inference.person_ppe import PPEState
from inference.yolo_detector import YoloDetector
from streaming.ffmpeg_publisher import FfmpegPublisher
from streaming.mediamtx import MediaMTXClient, stream_path
from utils.backend_client import BackendNotifier
from utils.logger import get_logger
from violations.repository import DatabaseError, Repository
from violations.violation_handler import ViolationHandler
from violations.violation_service import ViolationEvent

logger = get_logger("camera.manager")

HEARTBEAT_SECONDS = 5.0

ConfigProvider = Callable[[], list[CameraConfig]]


@dataclass
class _Running:
    config: CameraConfig
    worker: CameraWorker
    last_det_frame_id: int = -1
    last_det_at: float = 0.0
    stream_ok: bool = False


class CameraManager:
    def __init__(
        self,
        settings: Settings,
        detector: YoloDetector,
        repo: Optional[Repository],
        notifier: BackendNotifier,
        config_provider: ConfigProvider,
        handler: ViolationHandler,
        on_violation_print: Optional[Callable[[ViolationEvent], None]] = None,
        streams: Optional[MediaMTXClient] = None,
    ) -> None:
        self._settings = settings
        self._detector = detector
        self._repo = repo
        self._notifier = notifier
        self._provider = config_provider
        self._handler = handler
        self._announce = on_violation_print

        self._streams = streams
        self._collector = FrameCollector(settings.resolved_dataset_dir)
        self._publishers: dict[str, FfmpegPublisher] = {}
        self._running: dict[str, _Running] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="camera-manager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)
        for item in list(self._running.values()):
            self._stop_worker(item)
        self._running.clear()
        self._collector.close()
        if self._streams:
            self._streams.close()

    def has_webcam(self) -> bool:
        return any(r.config.camera_type == "LOCAL_WEBCAM" for r in list(self._running.values()))

    @property
    def workers(self) -> list[CameraWorker]:
        return [r.worker for r in self._running.values()]

    # ------------------------------------------------------------------ main loop
    def _loop(self) -> None:
        next_sync = 0.0
        next_beat = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now >= next_sync:
                next_sync = now + self._settings.camera_sync_interval_seconds
                self._sync()
            if now >= next_beat:
                next_beat = now + HEARTBEAT_SECONDS
                self._notifier.heartbeat()
            self._push_detections()
            self._stop.wait(0.01)

    def sync_now(self) -> None:
        self._sync()

    def _sync(self) -> None:
        try:
            configs = {c.id: c for c in self._provider()}
        except DatabaseError:
            return  # _run аль хэдийн DATABASE_ERROR log хийсэн; одоо ажиллаж буй камерууд үргэлжилнэ

        self._sync_collection()

        for camera_id in list(self._running):
            item = self._running[camera_id]
            wanted = configs.get(camera_id)
            if wanted is None:
                logger.info("Camera disabled/removed, stopping: %s", item.config.name)
                self._stop_worker(item)
                del self._running[camera_id]
            elif wanted.fingerprint() != item.config.fingerprint():
                logger.info("Camera config changed, restarting: %s", wanted.name)
                self._stop_worker(item)
                del self._running[camera_id]
            elif wanted.ai_enabled != item.worker.ai_enabled:  # restart хэрэггүй, шууд солино
                logger.info("AI %s | camera=%s", "холбогдлоо" if wanted.ai_enabled else "салгагдлаа (зөвхөн видео)", wanted.name)
                item.worker.ai_enabled = wanted.ai_enabled
                item.config = wanted

        for camera_id, config in configs.items():
            if camera_id not in self._running:
                self._start_worker(config)

        # MediaMTX түр унтарсан/дараа ассан үед RTSP path-ийг дахин бүртгэнэ
        for item in self._running.values():
            if self._streams and item.config.is_rtsp() and not item.stream_ok:
                item.stream_ok = self._streams.ensure_rtsp_source(stream_path(item.config.id), item.config.rtsp_url())

    def _sync_collection(self) -> None:
        if not self._repo:
            return
        try:
            self._collector.configure(self._repo.load_dataset_setting())
        except DatabaseError:
            pass  # app_settings байхгүй/DB түр тасарсан — одоогийн тохиргоо хэвээр

    # ------------------------------------------------------------------ workers
    def _start_worker(self, config: CameraConfig) -> None:
        def on_state(name: str, old: CameraState, new: CameraState, reason: str) -> None:
            if self._repo:
                self._repo.set_camera_status(
                    config.id, new.value, connected=new is CameraState.ONLINE,
                    on_done=lambda: self._notifier.camera_status(config.id),
                )

        try:
            camera = config.build_camera(self._settings, on_state_change=on_state)
            processor = FrameProcessor.from_settings(
                self._settings, self._detector, config.name, config.zone, camera_id=config.id
            )
        except Exception as exc:
            logger.error("Cannot start camera %s: %s", config.name, exc)
            if self._repo:
                self._repo.set_camera_status(config.id, "ERROR", on_done=lambda: self._notifier.camera_status(config.id))
            return

        def on_violation(event: ViolationEvent, frame) -> None:
            if self._announce:
                self._announce(event)
            self._handler(event, frame)

        worker = CameraWorker(camera, processor, on_violation=on_violation)
        worker.ai_enabled = config.ai_enabled
        item = _Running(config, worker)
        self._attach_stream(config, camera, item)
        worker.start()
        self._running[config.id] = item
        logger.info("Camera started | name=%s zone=%s", config.name, config.zone)

    def _attach_stream(self, config: CameraConfig, camera, item: _Running) -> None:
        """Видеог MediaMTX-д гаргана: RTSP камер -> MediaMTX өөрөө татна; вэбкамер/mp4 -> ffmpeg-ээр түлхэнэ."""
        if not self._streams:
            return
        path = stream_path(config.id)
        if config.is_rtsp():
            item.stream_ok = self._streams.ensure_rtsp_source(path, config.rtsp_url())
        elif FfmpegPublisher.available():
            publisher = FfmpegPublisher(self._streams.publish_url(path), config.name)
            publisher.start()
            camera.frame_sink = publisher.submit
            self._publishers[config.id] = publisher
        else:
            logger.warning("ffmpeg олдсонгүй — %s камерын шууд видео dashboard-д гарахгүй (brew install ffmpeg)", config.name)

    def _stop_worker(self, item: _Running) -> None:
        try:
            item.worker.stop()  # RTSPCamera.stop() -> OFFLINE state callback
        except Exception:
            logger.exception("Error stopping camera %s", item.config.name)
        publisher = self._publishers.pop(item.config.id, None)
        if publisher:
            publisher.stop()
        if self._streams and item.config.is_rtsp():
            self._streams.remove_path(stream_path(item.config.id))

    # ------------------------------------------------------------------ AI боксууд (dashboard-ийн давхарга)
    def _push_detections(self) -> None:
        now = time.time()
        for camera_id, item in self._running.items():
            if now - item.last_det_at < self._settings.detections_interval_seconds:
                continue
            snap = item.worker.latest()
            if snap is None or snap.packet.frame_id == item.last_det_frame_id:
                continue
            item.last_det_at = now
            item.last_det_frame_id = snap.packet.frame_id
            self._collector.maybe_collect(item.config.name, snap.packet.frame, snap.result.detections, now)
            h, w = snap.packet.frame.shape[:2]
            people = []
            for ev in snap.result.evaluations:
                p = ev.person
                d = p.person
                hb = p.helmet_evidence  # helmet (каск) эсвэл no_helmet (каскгүй толгой) detection
                people.append({
                    "id": p.track_id,
                    "box": [round(d.x1 / w, 4), round(d.y1 / h, 4), round(d.x2 / w, 4), round(d.y2 / h, 4)],
                    "helmet_box": None if hb is None else [
                        round(max(0.0, hb.x1) / w, 4), round(max(0.0, hb.y1) / h, 4),
                        round(min(float(w), hb.x2) / w, 4), round(min(float(h), hb.y2) / h, 4)],
                    "helmet": p.helmet.value,
                    "uniform": p.uniform.value,
                    "violation": bool(ev.violations),
                })
            self._notifier.push_detections(camera_id, {"people": people})


def db_config_provider(repo: Repository, secret_key: str, notifier: Optional[BackendNotifier] = None) -> ConfigProvider:
    """DB-ийн `enabled` камеруудаас config үүсгэнэ. Буруу тохиргоотой камерыг ERROR болгож алгасна."""

    def provide() -> list[CameraConfig]:
        configs: list[CameraConfig] = []
        for row in repo.load_cameras():
            if not row["enabled"]:
                continue
            try:
                configs.append(config_from_db_row(row, secret_key))
            except CameraConfigError as exc:
                logger.error("Camera %s skipped: %s", row["name"], exc)
                if row["status"] != "ERROR":
                    repo.set_camera_status(
                        row["id"], "ERROR", on_done=(lambda cid=row["id"]: notifier.camera_status(cid)) if notifier else None
                    )
        return configs

    return provide
