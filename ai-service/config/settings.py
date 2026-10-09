"""Бүх configuration .env-ээс ирнэ. Код дотор IP / password / path hard-code хийхгүй."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = SERVICE_ROOT / ".env"

# .env.example дахь placeholder утгууд — хэрэглэгч солиогүй бол camera test эхлүүлэхгүй.
_PLACEHOLDERS = {"", "CHANGE_ME", "YOUR_SERVICE_ROLE_KEY"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Camera ----
    camera_source: Literal["rtsp", "mp4"] = "rtsp"
    camera_name: str = "Factory-Camera-01"
    camera_ip: str = ""
    camera_rtsp_port: int = 554
    camera_username: str = ""
    camera_password: str = ""
    camera_channel: int = 1
    camera_subtype: int = 0
    camera_type: Literal["DAHUA", "HIKVISION", "GENERIC_RTSP"] = "DAHUA"
    camera_rtsp_url_template: str = ""
    video_file_path: str = ""

    camera_retry_delay: float = Field(2.0, gt=0)
    camera_max_retry_delay: float = Field(60.0, gt=0)
    camera_open_timeout_ms: int = 10000
    camera_read_timeout_ms: int = 10000

    # ---- AI ----
    model_path: str = "./models/initial_model.pt"
    person_confidence_threshold: float = Field(0.30, ge=0.05, le=1.0)  # хүнийг хүрээлэх нам босго (каск/хувцасны дүгнэлтэд хамаарахгүй)
    confidence_threshold: float = Field(0.50, ge=0, le=1)
    iou_threshold: float = Field(0.45, ge=0, le=1)
    inference_imgsz: int = Field(640, ge=32)
    inference_device: str = ""  # "" = auto, эсвэл cpu / mps / cuda:0
    # Model-ийн class нэр -> системийн canonical class. Формат: "Hardhat:helmet,NO-Hardhat:no_helmet"
    class_mapping: str = ""

    # ---- Zone (Phase 15 хүртэл нэг камерын zone; дараа нь DB-ийн cameras.zone_id-аас) ----
    camera_zone: Literal["FACTORY", "OFFICE"] = "FACTORY"

    # ---- Violation ----
    violation_cooldown_seconds: float = Field(10.0, ge=0)
    # Зөрчил сүүлийн WINDOW frame-ийн дор хаяж MIN_HITS-д илэрвэл баталгаажна
    violation_confirm_window: int = Field(5, ge=1)
    violation_confirm_min_hits: int = Field(3, ge=1)
    track_iou_threshold: float = Field(0.25, ge=0, le=1)
    track_max_age_seconds: float = Field(3.0, gt=0)

    # ---- Snapshot ----
    snapshot_jpeg_quality: int = Field(85, ge=1, le=100)
    snapshot_utc_offset_hours: float = 8.0  # Snapshot дээрх цаг (Монгол = UTC+8)
    snapshot_save_local: bool = True  # Supabase upload-ийн зэрэгцээ локал хуулбар (debug/backup)
    snapshot_local_dir: str = "./snapshots"

    # ---- Сургалтын өгөгдөл цуглуулалт (dashboard → Сургалт хэсгээс асаана) ----
    dataset_dir: str = "../training/datasets"

    # ---- Database / Supabase ----
    database_url: str = ""
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "violation-snapshots"

    # ---- Backend ----
    backend_url: str = "http://localhost:8000"
    internal_api_key: str = ""  # ai-service -> backend дотоод дуудлага
    camera_secret_key: str = ""  # DB дахь камерын нууц үгийг тайлах (backend-тэй ижил)
    camera_sync_interval_seconds: float = Field(10.0, ge=1)
    # MediaMTX (камерын видеог browser-д WebRTC-ээр түгээнэ)
    mediamtx_enabled: bool = True
    mediamtx_api_url: str = "http://127.0.0.1:9997"
    mediamtx_rtsp_url: str = "rtsp://127.0.0.1:8554"
    detections_interval_seconds: float = Field(0.1, ge=0.03)  # AI боксыг dashboard руу илгээх давтамж

    # ---- Logging ----
    log_level: str = "INFO"
    log_dir: str = "./logs"

    # ------------------------------------------------------------------
    @property
    def database_configured(self) -> bool:
        return self.database_url.startswith(("postgresql://", "postgres://")) and "USER:PASSWORD@HOST" not in self.database_url

    @property
    def supabase_configured(self) -> bool:
        return (
            self.supabase_url.startswith("https://")
            and self.supabase_service_role_key not in _PLACEHOLDERS
            and "YOUR_PROJECT" not in self.supabase_url
        )

    @property
    def resolved_model_path(self) -> Path:
        path = Path(self.model_path)
        return path if path.is_absolute() else (SERVICE_ROOT / path).resolve()

    @property
    def parsed_class_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for pair in filter(None, (p.strip() for p in self.class_mapping.split(","))):
            if ":" not in pair:
                raise ValueError(f"Invalid CLASS_MAPPING entry '{pair}' (expected model_class:canonical_class)")
            src, dst = pair.rsplit(":", 1)
            mapping[src.strip()] = dst.strip()
        return mapping

    @property
    def resolved_snapshot_dir(self) -> Path:
        path = Path(self.snapshot_local_dir)
        return path if path.is_absolute() else (SERVICE_ROOT / path).resolve()

    @property
    def resolved_dataset_dir(self) -> Path:
        path = Path(self.dataset_dir)
        return path if path.is_absolute() else (SERVICE_ROOT / path).resolve()

    @property
    def resolved_log_dir(self) -> Path:
        path = Path(self.log_dir)
        return path if path.is_absolute() else (SERVICE_ROOT / path).resolve()

    def camera_config_problems(self) -> list[str]:
        """`.env` дээр хэрэглэгч заавал бөглөх ёстой, гэхдээ бөглөөгүй түлхүүрүүдийг буцаана."""
        problems: list[str] = []
        if self.camera_source == "mp4":
            if not self.video_file_path:
                problems.append("VIDEO_FILE_PATH")
            return problems

        if self.camera_ip in _PLACEHOLDERS:
            problems.append("CAMERA_IP")
        if self.camera_username in _PLACEHOLDERS:
            problems.append("CAMERA_USERNAME")
        if self.camera_password in _PLACEHOLDERS:
            problems.append("CAMERA_PASSWORD")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
