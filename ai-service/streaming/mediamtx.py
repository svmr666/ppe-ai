"""MediaMTX удирдлага: RTSP камерын path-ийг (source-тэй) бүртгэх / устгах.

RTSP камер (Dahua/Hikvision): MediaMTX өөрөө камераас татаж WebRTC болгон түгээнэ (видео дахин кодлогдохгүй).
Камерын нууц үг зөвхөн MediaMTX-ийн санах ойн тохиргоонд (API-аар) орно; файлд бичигдэхгүй.
API нэвтрэлт: user "ai", password = INTERNAL_API_KEY (MediaMTX -> backend /internal/mediamtx/auth шалгана).
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

import httpx

from config.settings import Settings
from utils.logger import get_logger

logger = get_logger("streaming.mediamtx")


def stream_path(camera_id: str) -> str:
    """backend-ийн camera_service.stream_path-тай ЯГ ИЖИЛ."""
    return f"cam_{camera_id}"


class MediaMTXClient:
    def __init__(self, api_url: str, rtsp_url: str, api_key: str, transport: Optional[httpx.BaseTransport] = None) -> None:
        self._rtsp_base = rtsp_url.rstrip("/")
        self._key = api_key
        self._http = httpx.Client(base_url=api_url.rstrip("/"), auth=("ai", api_key), timeout=httpx.Timeout(4.0), transport=transport)
        self._registered: dict[str, str] = {}  # path -> source (нууц үгтэй; зөвхөн санах ойд)

    @classmethod
    def from_settings(cls, settings: Settings) -> Optional["MediaMTXClient"]:
        if not settings.mediamtx_enabled:
            return None
        if settings.internal_api_key in ("", "CHANGE_ME"):
            logger.warning("INTERNAL_API_KEY тохируулаагүй — MediaMTX streaming унтраалттай")
            return None
        return cls(settings.mediamtx_api_url, settings.mediamtx_rtsp_url, settings.internal_api_key)

    def publish_url(self, path: str) -> str:
        """ffmpeg-ийн publish хаяг (user=ai)."""
        scheme, rest = self._rtsp_base.split("://", 1)
        return f"{scheme}://ai:{quote(self._key, safe='')}@{rest}/{path}"

    def ensure_rtsp_source(self, path: str, source: str) -> bool:
        """MediaMTX-д `path`-ийг `source` RTSP-ээс татдаг болгож бүртгэнэ. Амжилттай бол True."""
        if self._registered.get(path) == source:
            return True
        body = {"source": source, "sourceOnDemand": False, "rtspTransport": "tcp"}
        try:
            action = "replace" if path in self._registered else "add"
            r = self._http.post(f"/v3/config/paths/{action}/{path}", json=body)
            if r.status_code == 400 and "already exists" in r.text:  # өмнөх ажиллагаанаас үлдсэн
                r = self._http.post(f"/v3/config/paths/replace/{path}", json=body)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("MediaMTX path бүртгэж чадсангүй (%s): %s — MediaMTX ажиллаж байгаа эсэхийг шалгана уу", path, type(exc).__name__)
            return False
        self._registered[path] = source
        logger.info("MediaMTX path бүртгэгдлээ | path=%s", path)
        return True

    def remove_path(self, path: str) -> None:
        self._registered.pop(path, None)
        try:
            self._http.delete(f"/v3/config/paths/delete/{path}")
        except httpx.HTTPError:
            pass

    def close(self) -> None:
        self._http.close()
