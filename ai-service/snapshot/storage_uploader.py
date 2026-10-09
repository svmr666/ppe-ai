"""Snapshot JPEG-ийг Supabase Storage руу upload хийнэ.

Path:  violations/YYYY/MM/DD/<camera-id>/<violation-id>.jpg
DB-д зөвхөн `storage_path` хадгална (зургийн binary PostgreSQL-д орохгүй). Bucket PRIVATE: зургийг backend
богино хугацаатай signed URL-ээр, зөвхөн нэвтэрсэн хэрэглэгчид өгнө.

SUPABASE_SERVICE_ROLE_KEY нь зөвхөн сервер талд (ai-service/backend). Frontend-д огт очихгүй.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

from snapshot.snapshot_service import Snapshot
from utils.errors import ErrorCode
from utils.logger import get_logger, log_error
from violations.violation_service import ViolationEvent

logger = get_logger("snapshot.storage")


class StorageUploadError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadResult:
    storage_path: str
    public_url: Optional[str] = None  # bucket private тул байхгүй; backend signed URL үүсгэнэ


def build_storage_path(event: ViolationEvent) -> str:
    camera_part = event.camera_id or re.sub(r"[^A-Za-z0-9_-]+", "-", event.camera_name).strip("-") or "unknown"
    d = event.detected_at
    return f"violations/{d:%Y}/{d:%m}/{d:%d}/{camera_part}/{event.violation_id}.jpg"


class StorageUploader:
    def __init__(self, url: str, service_role_key: str, bucket: str, retries: int = 3) -> None:
        from supabase import create_client  # lazy import

        self._client = create_client(url, service_role_key)
        self._bucket = bucket
        self._retries = retries

    @property
    def bucket(self) -> str:
        return self._bucket

    def upload(self, event: ViolationEvent, snapshot: Snapshot) -> UploadResult:
        path = build_storage_path(event)
        storage = self._client.storage.from_(self._bucket)

        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                storage.upload(
                    path,
                    snapshot.data,
                    file_options={"content-type": snapshot.content_type, "upsert": "true"},
                )
                logger.info("Snapshot uploaded | path=%s", path)
                return UploadResult(storage_path=path)
            except Exception as exc:
                last_error = exc
                logger.warning("Upload attempt %d/%d failed: %s", attempt, self._retries, exc)
                if attempt < self._retries:
                    time.sleep(0.5 * 2 ** (attempt - 1))

        log_error(
            logger, ErrorCode.STORAGE_UPLOAD_ERROR, f"Upload failed after {self._retries} attempts: {last_error}",
            path=path, bucket=self._bucket,
        )
        raise StorageUploadError(str(last_error))

    def delete(self, storage_path: str) -> None:
        self._client.storage.from_(self._bucket).remove([storage_path])
