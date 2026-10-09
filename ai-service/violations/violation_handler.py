"""Violation event-ийг боловсруулах дараалал: snapshot -> (локал хуулбар) -> Supabase upload.

Snapshot үүсгэх нь хурдан тул inference thread дотор хийнэ. Upload (сүлжээ) нь удаан/алдаатай байж болох тул
тусдаа thread pool-д явуулж, inference-ийг хаахгүй. Upload амжилтгүй бол snapshot-ийг
`<snapshot_dir>/failed-upload/` руу хадгалж, алдагдуулахгүй.

on_processed(event, upload_result | None) callback-д PostgreSQL бичилт (Phase 11), WebSocket (Phase 14) залгагдана.
Upload амжилтгүй байсан ч violation-ыг DB-д бүртгэнэ (snapshot_url = NULL), учир нь зөрчил үнэхээр болсон.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from config.settings import Settings
from snapshot.snapshot_service import Snapshot, SnapshotError, SnapshotService, local_filename
from snapshot.storage_uploader import StorageUploader, StorageUploadError, UploadResult
from utils.logger import get_logger
from violations.violation_service import ViolationEvent

logger = get_logger("violations.handler")

# Upload дууссаны дараа (амжилттай ч, амжилтгүй ч) дуудагдана. Амжилтгүй бол result = None.
ProcessedCallback = Callable[[ViolationEvent, Optional[UploadResult]], None]


class ViolationHandler:
    def __init__(
        self,
        settings: Settings,
        snapshots: Optional[SnapshotService] = None,
        uploader: Optional[StorageUploader] = None,
        on_processed: Optional[ProcessedCallback] = None,
        workers: int = 2,
    ) -> None:
        self._settings = settings
        self._snapshots = snapshots or SnapshotService(settings.snapshot_jpeg_quality, settings.snapshot_utc_offset_hours)
        self._uploader = uploader
        self._on_processed = on_processed
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="violation-upload")

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> "ViolationHandler":
        uploader = None
        if settings.supabase_configured:
            uploader = StorageUploader(
                settings.supabase_url, settings.supabase_service_role_key, settings.supabase_storage_bucket
            )
        else:
            logger.warning(
                "Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY) — snapshots stay local only"
            )
        return cls(settings, uploader=uploader, **kwargs)

    # ------------------------------------------------------------------
    def __call__(self, event: ViolationEvent, frame: np.ndarray) -> None:
        try:
            snapshot = self._snapshots.create(event, frame)
        except SnapshotError as exc:
            logger.error("Snapshot failed | camera=%s: %s", event.camera_name, exc)
            return

        if self._settings.snapshot_save_local:
            snapshot.save(self._settings.resolved_snapshot_dir / local_filename(event))

        self._pool.submit(self._process, event, snapshot)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)

    # ------------------------------------------------------------------
    def _process(self, event: ViolationEvent, snapshot: Snapshot) -> None:
        result: Optional[UploadResult] = None
        if self._uploader is not None:
            result = self._upload(event, snapshot)

        if self._on_processed:
            try:
                self._on_processed(event, result)
            except Exception:
                logger.exception("on_processed callback failed | camera=%s", event.camera_name)

    def _upload(self, event: ViolationEvent, snapshot: Snapshot) -> Optional[UploadResult]:
        try:
            return self._uploader.upload(event, snapshot)  # type: ignore[union-attr]
        except StorageUploadError:
            fallback = self._settings.resolved_snapshot_dir / "failed-upload" / local_filename(event)
            snapshot.save(fallback)
            logger.warning("Upload failed, snapshot kept locally: %s", fallback)
        except Exception:
            logger.exception("Unexpected upload error | camera=%s", event.camera_name)
        return None
