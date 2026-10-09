"""Нэг камерын тохиргоо (DB-ээс эсвэл .env-ээс) ба түүгээр RTSPCamera үүсгэх factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from camera.rtsp_camera import RTSPCamera, StateCallback, build_rtsp_url
from config.settings import Settings
from utils.secrets_util import decrypt_secret


@dataclass(frozen=True)
class CameraConfig:
    id: str
    name: str
    zone: str
    ip: str = ""
    port: int = 554
    username: str = ""
    password: str = ""
    camera_type: str = "DAHUA"
    channel: int = 1
    subtype: int = 0
    file_path: Optional[str] = None  # зөвхөн dev: mp4 adapter
    mirror: bool = False  # вэбкамер: толин тусгал (selfie)
    ai_enabled: bool = True  # false: зөвхөн шууд видео — YOLO ажиллахгүй (restart шаардахгүй, шууд солигдоно)

    def __repr__(self) -> str:  # нууц үг log/traceback-д хэзээ ч гарахгүй
        return f"CameraConfig(id={self.id}, name={self.name}, zone={self.zone}, ip={self.ip}:{self.port}, type={self.camera_type})"

    def fingerprint(self) -> tuple:
        """Тохиргоо өөрчлөгдсөн эсэхийг мэдэх (өөрчлөгдвөл worker-ийг дахин эхлүүлнэ)."""
        return (self.name, self.zone, self.ip, self.port, self.username, self.password,
                self.camera_type, self.channel, self.subtype, self.file_path)

    def is_rtsp(self) -> bool:
        return not self.file_path and self.camera_type in ("DAHUA", "HIKVISION")

    def rtsp_url(self) -> str:
        """Камерын жинхэнэ RTSP хаяг (нууц үгтэй) — MediaMTX-д л дамжуулна, log-д гаргахгүй."""
        return build_rtsp_url(self.username, self.password, self.ip, self.port, self.channel, self.subtype, self.camera_type)

    def build_camera(self, settings: Settings, on_state_change: Optional[StateCallback] = None) -> RTSPCamera:
        common = dict(
            retry_delay=settings.camera_retry_delay,
            max_retry_delay=settings.camera_max_retry_delay,
            open_timeout_ms=settings.camera_open_timeout_ms,
            read_timeout_ms=settings.camera_read_timeout_ms,
            on_state_change=on_state_change,
        )
        if self.file_path:
            return RTSPCamera(self.name, self.file_path, is_file=True, **common)
        if self.camera_type == "LOCAL_WEBCAM":
            index = int(self.ip) if self.ip.isdigit() else 0
            return RTSPCamera(self.name, f"webcam:{index}", device_index=index, **common)
        url = build_rtsp_url(self.username, self.password, self.ip, self.port, self.channel, self.subtype, self.camera_type)
        return RTSPCamera(self.name, url, ip=self.ip, port=self.port, **common)


class CameraConfigError(ValueError):
    pass


def config_from_db_row(row: dict, secret_key: str) -> CameraConfig:
    """DB-ийн камерын мөрөөс config. Нууц үгийг тайлна; боломжгүй бол CameraConfigError."""
    if not row.get("zone"):
        raise CameraConfigError("камерт zone оноогоогүй байна")
    if row["camera_type"] == "LOCAL_WEBCAM":  # вэбкамерт нууц үг хэрэггүй
        return CameraConfig(
            id=row["id"], name=row["name"], zone=row["zone"], ip=row["ip"], camera_type="LOCAL_WEBCAM",
            mirror=bool(row.get("mirror")), ai_enabled=row.get("ai_enabled") is not False,
        )
    if not row.get("password_encrypted"):
        raise CameraConfigError("камерын нууц үг хадгалагдаагүй байна")
    if not secret_key or secret_key == "CHANGE_ME":
        raise CameraConfigError("CAMERA_SECRET_KEY тохируулаагүй")
    password = decrypt_secret(row["password_encrypted"], secret_key)
    if password is None:
        raise CameraConfigError("камерын нууц үгийг тайлж чадсангүй (CAMERA_SECRET_KEY backend-тэй ижил үү?)")
    if row["camera_type"] == "GENERIC_RTSP":
        raise CameraConfigError("GENERIC_RTSP камер одоогоор DB-ээс дэмжигдэхгүй (DAHUA/HIKVISION ашиглана уу)")
    return CameraConfig(
        id=row["id"], name=row["name"], zone=row["zone"], ip=row["ip"], port=row["port"],
        username=row["username"], password=password, camera_type=row["camera_type"],
        channel=row["channel"], subtype=row["subtype"], ai_enabled=row.get("ai_enabled") is not False,
    )


def config_from_settings(settings: Settings, camera_id: str, zone: str) -> CameraConfig:
    """`.env`-ийн нэг камер (`run` команд). CAMERA_SOURCE=mp4 бол dev adapter."""
    if settings.camera_source == "mp4":
        return CameraConfig(id=camera_id, name=settings.camera_name, zone=zone, file_path=settings.video_file_path)
    return CameraConfig(
        id=camera_id, name=settings.camera_name, zone=zone, ip=settings.camera_ip, port=settings.camera_rtsp_port,
        username=settings.camera_username, password=settings.camera_password, camera_type=settings.camera_type,
        channel=settings.camera_channel, subtype=settings.camera_subtype,
    )
