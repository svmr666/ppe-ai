"""Камерын CRUD туслахууд, RTSP URL үүсгэх, холболт шалгах (TEST CONNECTION).

Камерын нууц үг DB-д Fernet-ээр шифрлэгдэж хадгалагдана; API response, log-д хэзээ ч гарахгүй.
"""

from __future__ import annotations

import os

# TCP transport (UDP-ээс найдвартай). cv2 import-оос ӨМНӨ.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import logging
import socket
from typing import Optional
from urllib.parse import quote

import cv2

from app.core.security import decrypt_secret
from app.database.models import Camera, CameraType
from app.schemas.camera import CameraOut, ConnectionTestResult

logger = logging.getLogger("backend.camera")

OPEN_TIMEOUT_MS = 8000
READ_TIMEOUT_MS = 8000


def build_rtsp_url(
    username: str, password: str, ip: str, port: int, channel: int, subtype: int,
    camera_type: str = "DAHUA",
) -> str:
    """DAHUA: rtsp://user:pass@ip:554/cam/realmonitor?channel=1&subtype=0 (нууц үгийг URL-encode хийнэ)."""
    user, pwd = quote(username, safe=""), quote(password, safe="")
    kind = camera_type.upper()
    if kind == "DAHUA":
        return f"rtsp://{user}:{pwd}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}"
    if kind == "HIKVISION":
        return f"rtsp://{user}:{pwd}@{ip}:{port}/Streaming/Channels/{channel * 100 + subtype + 1}"
    raise ValueError(f"{camera_type} төрлийн камерын RTSP хаягийг автоматаар үүсгэх боломжгүй")


def stream_path(camera_id) -> str:
    """MediaMTX path нэр. ai-service-тэй ЯГ ИЖИЛ байх ёстой."""
    return f"cam_{camera_id}"


def to_out(camera: Camera) -> CameraOut:
    # `zone` нь ORM-д relationship тул from_attributes ашиглахгүй, талбар бүрийг тодорхой зааж өгнө
    return CameraOut(
        id=camera.id, name=camera.name, ip_address=camera.ip_address, rtsp_port=camera.rtsp_port,
        username=camera.username, camera_type=camera.camera_type, channel=camera.channel, subtype=camera.subtype,
        zone_id=camera.zone_id, zone=camera.zone.name if camera.zone else None, status=camera.status,
        mirror=camera.mirror, ai_enabled=camera.ai_enabled, enabled=camera.enabled, last_connected_at=camera.last_connected_at, created_at=camera.created_at,
        updated_at=camera.updated_at,
    )


def probe_rtsp(
    *, username: str, password: str, ip: str, port: int, channel: int, subtype: int, camera_type: str
) -> ConnectionTestResult:
    """Блоклодог функц — threadpool дотор дуудна. Нууц үг буцаах message-д ОРОХГҮЙ."""
    try:
        url = build_rtsp_url(username, password, ip, port, channel, subtype, camera_type)
    except ValueError as exc:
        return ConnectionTestResult(success=False, status="ERROR", message="Unable to connect to RTSP stream", reason=str(exc))

    def fail(reason: str) -> ConnectionTestResult:
        logger.warning("Camera connection test failed | ip=%s port=%s reason=%s", ip, port, reason)
        return ConnectionTestResult(success=False, status="ERROR", message="Unable to connect to RTSP stream", reason=reason)

    try:
        with socket.create_connection((ip, port), timeout=3):
            pass
    except socket.gaierror:
        return fail(f"'{ip}' хаягийг олсонгүй (IP/hostname буруу байна)")
    except socket.timeout:
        return fail(f"{ip}:{port} руу TCP холболт хугацаа хэтэрлээ (IP буруу, камер унтраалттай, эсвэл өөр сүлжээнд байна)")
    except OSError as exc:
        return fail(f"{ip}:{port} руу TCP холболт амжилтгүй: {exc.strerror or type(exc).__name__} (порт буруу эсвэл камерын RTSP хаалттай байж магадгүй)")

    cap = cv2.VideoCapture(
        url, cv2.CAP_FFMPEG,
        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS, cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS],
    )
    try:
        if not cap.isOpened():
            return fail("Порт нээлттэй боловч RTSP stream нээгдсэнгүй (хэрэглэгчийн нэр/нууц үг, channel/subtype-ээ шалгана уу)")
        frame = None
        for _ in range(5):
            ok, frame = cap.read()
            if ok and frame is not None:
                break
        if frame is None:
            return fail("RTSP stream нээгдсэн ч зураг (frame) ирсэнгүй (codec/stream тохиргоог шалгана уу)")
        h, w = frame.shape[:2]
        return ConnectionTestResult(
            success=True, status="ONLINE", message="Camera connected successfully", resolution=f"{w}x{h}"
        )
    finally:
        cap.release()


def probe_webcam(index: int) -> ConnectionTestResult:
    """Mac/ноутбукийн камер. Блоклодог — threadpool дотор дуудна."""
    import sys

    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION) if sys.platform == "darwin" else cv2.VideoCapture(index)
    try:
        if not cap.isOpened():
            reason = (
                f"Камер #{index} нээгдсэнгүй. macOS: System Settings → Privacy & Security → Camera хэсэгт "
                "backend ажиллаж буй програмд (Terminal/Claude) зөвшөөрөл өгнө үү; дугаар буруу байж болно."
            )
            logger.warning("Webcam test failed | index=%s", index)
            return ConnectionTestResult(success=False, status="ERROR", message="Unable to connect to RTSP stream", reason=reason)
        frame = None
        for _ in range(15):  # вэбкамер эхний хэдэн frame-д гэрэлтүүлгээ тохируулдаг
            ok, frame = cap.read()
            if ok and frame is not None:
                break
        if frame is None:
            return ConnectionTestResult(
                success=False, status="ERROR", message="Unable to connect to RTSP stream",
                reason=f"Камер #{index} нээгдсэн ч зураг ирсэнгүй (өөр програм ашиглаж байж магадгүй)",
            )
        h, w = frame.shape[:2]
        return ConnectionTestResult(success=True, status="ONLINE", message="Camera connected successfully", resolution=f"{w}x{h}")
    finally:
        cap.release()


def probe_stored_camera(camera: Camera) -> ConnectionTestResult:
    if camera.camera_type is CameraType.LOCAL_WEBCAM:
        return probe_webcam(int(camera.ip_address) if camera.ip_address.isdigit() else 0)
    password = decrypt_secret(camera.password_encrypted) if camera.password_encrypted else None
    if password is None:
        return ConnectionTestResult(
            success=False, status="ERROR", message="Unable to connect to RTSP stream",
            reason="Камерын нууц үг хадгалагдаагүй эсвэл тайлж чадсангүй (камерыг засаж нууц үгийг дахин оруулна уу)",
        )
    return probe_rtsp(
        username=camera.username, password=password, ip=camera.ip_address, port=camera.rtsp_port,
        channel=camera.channel, subtype=camera.subtype, camera_type=camera.camera_type.value,
    )
