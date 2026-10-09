"""ai-service -> backend (FastAPI) дотоод дуудлага. Backend нь WebSocket-ээр dashboard-д дамжуулна.

AI SERVICE -> FASTAPI -> WEBSOCKET -> NEXT.JS

- Бүх дуудлага X-Internal-Key-тэй. Алдаа гарвал AI pipeline-ийг ХЭЗЭЭ Ч зогсоохгүй (WEBSOCKET_ERROR log).
- Дуудлагуудыг тусдаа thread-д явуулна (inference/камерын thread-ийг хаахгүй).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from config.settings import Settings
from utils.errors import ErrorCode
from utils.logger import get_logger, log_error

logger = get_logger("backend.client")

_LOG_EVERY_SECONDS = 30.0


class BackendNotifier:
    def __init__(self, base_url: str, internal_key: str) -> None:
        self._enabled = bool(internal_key) and internal_key != "CHANGE_ME"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers={"X-Internal-Key": internal_key}, timeout=httpx.Timeout(4.0)
        )
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="backend-notify")
        self._last_error_log = 0.0
        self._frame_inflight: set[str] = set()
        self._lock = threading.Lock()
        if not self._enabled:
            logger.warning("INTERNAL_API_KEY is not set — dashboard real-time notifications are disabled")

    @classmethod
    def from_settings(cls, settings: Settings) -> "BackendNotifier":
        return cls(settings.backend_url, settings.internal_api_key)

    # ------------------------------------------------------------------
    def violation_created(self, violation_id: str) -> None:
        self._submit("POST", f"/internal/violations/{violation_id}/created")

    def camera_status(self, camera_id: str) -> None:
        self._submit("POST", f"/internal/cameras/{camera_id}/status")

    def heartbeat(self) -> None:
        self._submit("POST", "/internal/heartbeat")

    def push_detections(self, camera_id: str, payload: dict) -> None:
        """AI боксыг dashboard руу (backend -> WebSocket). Өмнөх нь дуусаагүй бол шинийг алгасна."""
        with self._lock:
            if camera_id in self._frame_inflight:
                return
            self._frame_inflight.add(camera_id)

        def done(_):
            with self._lock:
                self._frame_inflight.discard(camera_id)

        self._submit("PUT", f"/internal/cameras/{camera_id}/detections", json=payload, on_done=done)

    def close(self) -> None:
        self._pool.shutdown(wait=True)
        self._client.close()

    # ------------------------------------------------------------------
    def _submit(self, method: str, path: str, on_done=None, **kwargs) -> None:
        if not self._enabled:
            if on_done:
                on_done(None)
            return
        future = self._pool.submit(self._request, method, path, **kwargs)
        if on_done:
            future.add_done_callback(on_done)

    def _request(self, method: str, path: str, **kwargs) -> None:
        try:
            response = self._client.request(method, path, **kwargs)
            if response.status_code >= 400:
                raise httpx.HTTPStatusError(f"HTTP {response.status_code}", request=response.request, response=response)
        except Exception as exc:
            now = time.time()
            if now - self._last_error_log > _LOG_EVERY_SECONDS:
                self._last_error_log = now
                log_error(logger, ErrorCode.WEBSOCKET_ERROR, f"Backend notify failed: {type(exc).__name__}", path=path)
