"""Structured logging.

Формат:  2026-10-08 10:35:22 [INFO] camera.rtsp: Camera connected | camera=Factory-Camera-01

- RTSP URL доторх `user:password@` хэсгийг log-д автоматаар `user:***@` болгоно.
- LOG_LEVEL, LOG_DIR нь .env-ээс удирдагдана.
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from utils.errors import ErrorCode

_CREDENTIALS_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]+):(?P<pw>[^@\s]+)@")
_CONFIGURED = False


def mask_credentials(text: str) -> str:
    """`rtsp://admin:secret@1.2.3.4` -> `rtsp://admin:***@1.2.3.4`"""
    return _CREDENTIALS_RE.sub(r"\g<scheme>\g<user>:***@", text)


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_credentials(record.getMessage())
        record.args = ()
        return True


def setup_logging(level: str = "INFO", log_dir: str | Path | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_dir:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(path / "ai-service.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"))

    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in handlers:
        handler.setFormatter(fmt)
        handler.addFilter(_RedactFilter())
        root.addHandler(handler)

    for noisy in ("httpx", "httpcore"):  # дуудлага бүрийн INFO лог (detections ~10/сек) логийг дүүргэхээс сэргийлнэ
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_error(logger: logging.Logger, code: ErrorCode, message: str, **fields: object) -> None:
    """`[ERROR] ... CAMERA_CONNECTION_ERROR: message | key=value` хэлбэрээр бичнэ."""
    extra = " | " + " ".join(f"{k}={v}" for k, v in fields.items()) if fields else ""
    logger.error("%s: %s%s", code.value, message, extra)
