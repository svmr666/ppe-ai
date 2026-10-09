"""Системийн нэгдсэн error code-ууд. Log болон API response-д ашиглана."""

from enum import Enum


class ErrorCode(str, Enum):
    CAMERA_CONNECTION_ERROR = "CAMERA_CONNECTION_ERROR"
    AI_MODEL_ERROR = "AI_MODEL_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    STORAGE_UPLOAD_ERROR = "STORAGE_UPLOAD_ERROR"
    WEBSOCKET_ERROR = "WEBSOCKET_ERROR"
