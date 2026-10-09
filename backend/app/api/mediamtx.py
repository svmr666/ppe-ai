"""MediaMTX -> backend HTTP нэвтрэлт. MediaMTX publish/read/api хүсэлт бүрт энд асууна (200 = зөвшөөрөв).

- publish, api : ai-service. user=ai, password=INTERNAL_API_KEY
- read         : dashboard. Bearer токен (create_stream_token) — тухайн path-д л хүчинтэй, 2 минут
- бусад (playback, metrics, pprof) : хориглоно
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.config import get_settings
from app.core.security import constant_time_equals, verify_stream_token

router = APIRouter(prefix="/internal/mediamtx", tags=["mediamtx"])
logger = logging.getLogger("backend.mediamtx")
_PATH_RE = re.compile(r"^cam_[0-9a-fA-F-]{36}$")


class AuthRequest(BaseModel):
    user: str = ""
    password: str = ""
    token: str = ""
    ip: str = ""
    action: str = ""
    path: str = ""
    protocol: str = ""
    id: str | None = None
    query: str = ""


@router.post("/auth")
def mediamtx_auth(body: AuthRequest):
    key = get_settings().internal_api_key
    deny = HTTPException(status.HTTP_401_UNAUTHORIZED, "denied")

    if body.action in ("publish", "api"):
        if key and constant_time_equals(body.password, key):
            return {}
        logger.warning("MediaMTX %s denied (bad credentials) from %s", body.action, body.ip)
        raise deny

    if body.action == "read":
        if _PATH_RE.match(body.path) and body.token and verify_stream_token(body.token, body.path):
            return {}
        logger.warning("MediaMTX read denied for path=%s from %s", body.path, body.ip)
        raise deny

    raise deny
