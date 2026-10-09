"""Dahua (болон бусад) RTSP камертай холбогдох давхарга.

DAHUA IP CAMERA --RTSP--> cv2.VideoCapture --> latest frame

- build_rtsp_url()  : URL-ийг config-оос динамикаар үүсгэнэ (hard-code байхгүй).
- RTSPCamera.probe(): нэг удаагийн холболтын шалгалт + ойлгомжтой алдааны шалтгаан.
- RTSPCamera.start(): background thread-ээр frame уншиж, тасарвал exponential backoff-оор reconnect хийнэ.

Camera state:
    CONNECTING -> ONLINE -> STREAMING
    STREAMING -> ERROR -> RECONNECTING -> ONLINE
"""

from __future__ import annotations

import os

# RTSP-г UDP-ээр биш TCP-ээр авна: packet алдагдал, "smeared" frame бага. cv2 import хийхээс ӨМНӨ тохируулна.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import socket
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional
from urllib.parse import quote

import cv2
import numpy as np

from config.settings import Settings
from utils.errors import ErrorCode
from utils.logger import get_logger, log_error, mask_credentials

logger = get_logger("camera.rtsp")


# ----------------------------------------------------------------------
# RTSP URL
# ----------------------------------------------------------------------
def build_rtsp_url(
    username: str,
    password: str,
    ip: str,
    port: int,
    channel: int,
    subtype: int,
    camera_type: str = "DAHUA",
    url_template: str = "",
) -> str:
    """Camera-ийн тохиргооноос RTSP URL үүсгэнэ.

    DAHUA : rtsp://user:pass@ip:554/cam/realmonitor?channel=1&subtype=0
    HIKVISION: rtsp://user:pass@ip:554/Streaming/Channels/101  (channel*100 + subtype + 1)
    GENERIC_RTSP: url_template ({username} {password} {ip} {port} {channel} {subtype}) шаардана.

    Username/password-д `@`, `:`, `/` зэрэг тусгай тэмдэгт байж болох тул URL-encode хийнэ.
    """
    user = quote(username, safe="")
    pwd = quote(password, safe="")
    kind = camera_type.upper()

    if kind == "DAHUA":
        return f"rtsp://{user}:{pwd}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}"

    if kind == "HIKVISION":
        return f"rtsp://{user}:{pwd}@{ip}:{port}/Streaming/Channels/{channel * 100 + subtype + 1}"

    if kind == "GENERIC_RTSP":
        if not url_template:
            raise ValueError("GENERIC_RTSP camera requires CAMERA_RTSP_URL_TEMPLATE")
        return url_template.format(
            username=user, password=pwd, ip=ip, port=port, channel=channel, subtype=subtype
        )

    raise ValueError(f"Unsupported camera type: {camera_type}")


def mask_rtsp_url(url: str) -> str:
    return mask_credentials(url)


# ----------------------------------------------------------------------
# Types
# ----------------------------------------------------------------------
def request_webcam_permission(index: int = 0) -> bool:
    """macOS: камерын зөвшөөрлийн цонхыг ҮНДСЭН thread дээр гаргана (OpenCV worker thread-ээс асууж чадахгүй).

    Зөвшөөрөл олгогдсоны дараа worker thread-үүд камерыг хэвийн нээнэ. macOS-оос бусад дээр үргэлж True.
    """
    if sys.platform != "darwin":
        return True
    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    granted = cap.isOpened()
    cap.release()
    return granted


class CameraState(str, Enum):
    OFFLINE = "OFFLINE"
    CONNECTING = "CONNECTING"
    ONLINE = "ONLINE"
    STREAMING = "STREAMING"
    ERROR = "ERROR"
    RECONNECTING = "RECONNECTING"


@dataclass(frozen=True)
class StreamInfo:
    width: int
    height: int
    reported_fps: float  # camera-ийн мэдэгдсэн FPS (RTSP дээр 0 эсвэл буруу байж болно)


@dataclass
class ConnectionResult:
    success: bool
    reason: str = ""
    info: Optional[StreamInfo] = None
    first_frame: Optional[np.ndarray] = None


@dataclass(frozen=True)
class FramePacket:
    frame: np.ndarray  # read-only гэж үз. Зурах бол .copy() хий.
    frame_id: int
    timestamp: float  # time.time()


StateCallback = Callable[[str, CameraState, CameraState, str], None]


# ----------------------------------------------------------------------
# Camera
# ----------------------------------------------------------------------
class RTSPCamera:
    MAX_CONSECUTIVE_READ_FAILURES = 3

    def __init__(
        self,
        name: str,
        source_uri: str,
        *,
        ip: str = "",
        port: int = 554,
        is_file: bool = False,
        device_index: Optional[int] = None,
        mirror: bool = False,
        retry_delay: float = 2.0,
        max_retry_delay: float = 60.0,
        open_timeout_ms: int = 10000,
        read_timeout_ms: int = 10000,
        on_state_change: Optional[StateCallback] = None,
    ) -> None:
        self.name = name
        self._uri = source_uri
        self._ip = ip
        self._port = port
        self._is_file = is_file
        self._device_index = device_index  # Mac/ноутбукийн камер (LOCAL_WEBCAM)
        self._mirror = mirror
        self.frame_sink: Optional[Callable[[np.ndarray], None]] = None  # MediaMTX руу дамжуулах (вэбкамер/mp4)  # толин тусгал (selfie) харагдац: вэбкамерт өөрийгөө «зөв» харахад
        self._retry_delay = retry_delay
        self._max_retry_delay = max_retry_delay
        self._open_timeout_ms = open_timeout_ms
        self._read_timeout_ms = read_timeout_ms
        self._on_state_change = on_state_change

        self._state = CameraState.OFFLINE
        self._state_reason = ""
        self._state_lock = threading.Lock()

        self._latest: Optional[FramePacket] = None
        self._frame_lock = threading.Lock()
        self._frame_counter = 0
        self._frame_times: deque[float] = deque(maxlen=60)

        self._info: Optional[StreamInfo] = None
        self.last_connected_at: Optional[float] = None
        self.last_error: str = ""

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------------- factories ----------------
    @classmethod
    def from_settings(cls, settings: Settings, on_state_change: Optional[StateCallback] = None) -> "RTSPCamera":
        common = dict(
            retry_delay=settings.camera_retry_delay,
            max_retry_delay=settings.camera_max_retry_delay,
            open_timeout_ms=settings.camera_open_timeout_ms,
            read_timeout_ms=settings.camera_read_timeout_ms,
            on_state_change=on_state_change,
        )
        if settings.camera_source == "mp4":
            # Зөвхөн development/test adapter. Production source нь RTSP.
            return cls(settings.camera_name, settings.video_file_path, is_file=True, **common)

        url = build_rtsp_url(
            username=settings.camera_username,
            password=settings.camera_password,
            ip=settings.camera_ip,
            port=settings.camera_rtsp_port,
            channel=settings.camera_channel,
            subtype=settings.camera_subtype,
            camera_type=settings.camera_type,
            url_template=settings.camera_rtsp_url_template,
        )
        return cls(
            settings.camera_name, url, ip=settings.camera_ip, port=settings.camera_rtsp_port, **common
        )

    # ---------------- properties ----------------
    @property
    def state(self) -> CameraState:
        return self._state

    @property
    def masked_uri(self) -> str:
        return mask_rtsp_url(self._uri)

    @property
    def stream_info(self) -> Optional[StreamInfo]:
        return self._info

    @property
    def measured_fps(self) -> float:
        with self._frame_lock:
            times = list(self._frame_times)
        if len(times) < 2 or times[-1] <= times[0]:
            return 0.0
        return (len(times) - 1) / (times[-1] - times[0])

    def get_latest(self) -> Optional[FramePacket]:
        with self._frame_lock:
            return self._latest

    # ---------------- one-shot probe ----------------
    def probe(self) -> ConnectionResult:
        """Нэг удаа холбогдож, эхний frame-ийг уншиж, шалтгаантай үр дүн буцаана. Thread эхлүүлэхгүй."""
        cap, result = self._open()
        if cap is not None:
            cap.release()
        return result

    # ---------------- background streaming ----------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._read_timeout_ms / 1000 + 3)
        self._set_state(CameraState.OFFLINE, "stopped")

    # ---------------- internals ----------------
    def _set_state(self, new: CameraState, reason: str = "") -> None:
        with self._state_lock:
            old = self._state
            if old == new:
                return
            self._state = new
            self._state_reason = reason
        level = logger.warning if new in (CameraState.ERROR, CameraState.RECONNECTING) else logger.info
        level("Camera %s: %s -> %s%s", self.name, old.value, new.value, f" ({reason})" if reason else "")
        if self._on_state_change:
            try:
                self._on_state_change(self.name, old, new, reason)
            except Exception:  # callback алдаа stream-ийг зогсоож болохгүй
                logger.exception("State change callback failed")

    def _tcp_reachable(self) -> Optional[str]:
        """RTSP port нээлттэй эсэхийг шалгана. Алдаа байвал шалтгааныг буцаана."""
        try:
            with socket.create_connection((self._ip, self._port), timeout=3):
                return None
        except socket.gaierror:
            return f"Cannot resolve host '{self._ip}' (CAMERA_IP буруу байна)"
        except socket.timeout:
            return (
                f"TCP connection to {self._ip}:{self._port} timed out "
                "(IP буруу, камер унтраалттай, эсвэл өөр сүлжээнд байна)"
            )
        except OSError as exc:
            return f"TCP connection to {self._ip}:{self._port} failed: {exc} (port буруу эсвэл камер RTSP-г хаасан)"

    def _open_webcam(self) -> tuple[Optional[cv2.VideoCapture], ConnectionResult]:
        index = self._device_index or 0
        cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION) if sys.platform == "darwin" else cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            return None, ConnectionResult(
                False,
                f"Вэбкамер #{index} нээгдсэнгүй. macOS: System Settings → Privacy & Security → Camera хэсэгт "
                "энэ програмд (Terminal) зөвшөөрөл өгнө үү; төхөөрөмжийн дугаар буруу байж болно.",
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        frame = None
        for _ in range(20):  # вэбкамер эхний frame-үүдэд гэрэлтүүлгээ тохируулдаг
            ok, frame = cap.read()
            if ok and frame is not None:
                break
        if frame is None:
            cap.release()
            return None, ConnectionResult(False, f"Вэбкамер #{index} нээгдсэн ч зураг ирсэнгүй (macOS камерын зөвшөөрөл байхгүй — System Settings → Privacy & Security → Camera дотор энэ терминалыг ажиллуулж буй програмыг (VS Code / Terminal) асаана уу; эсвэл өөр програм камерыг ашиглаж байна)")
        h, w = frame.shape[:2]
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        return cap, ConnectionResult(True, info=StreamInfo(width=w, height=h, reported_fps=fps), first_frame=frame)

    def _open(self) -> tuple[Optional[cv2.VideoCapture], ConnectionResult]:
        if self._device_index is not None:
            return self._open_webcam()
        if not self._is_file:
            tcp_error = self._tcp_reachable()
            if tcp_error:
                return None, ConnectionResult(False, tcp_error)

        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self._open_timeout_ms,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, self._read_timeout_ms,
        ]
        try:
            cap = cv2.VideoCapture(self._uri, cv2.CAP_FFMPEG, params)
        except Exception as exc:
            return None, ConnectionResult(False, f"OpenCV error: {exc}")

        if not cap.isOpened():
            cap.release()
            return None, ConnectionResult(
                False,
                "Port is reachable but the RTSP stream could not be opened "
                "(username/password, channel/subtype, эсвэл камерын RTSP тохиргоог шалга)",
            )

        ok, frame = False, None
        for _ in range(5):  # эхний хэдэн frame алдагдаж болно (keyframe хүлээх)
            ok, frame = cap.read()
            if ok and frame is not None:
                break
        if not ok or frame is None:
            cap.release()
            return None, ConnectionResult(
                False, "RTSP stream opened but no frames were received (codec/stream тохиргоог шалга)"
            )

        height, width = frame.shape[:2]
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        info = StreamInfo(width=width, height=height, reported_fps=fps)
        return cap, ConnectionResult(True, info=info, first_frame=frame)

    def _publish(self, frame: np.ndarray) -> None:
        if self._mirror:
            frame = cv2.flip(frame, 1)
        if self.frame_sink is not None:
            self.frame_sink(frame)
        now = time.time()
        self._frame_counter += 1
        with self._frame_lock:
            self._latest = FramePacket(frame=frame, frame_id=self._frame_counter, timestamp=now)
            self._frame_times.append(now)

    def _run(self) -> None:
        retry_count = 0
        first_attempt = True

        while not self._stop.is_set():
            self._set_state(CameraState.CONNECTING if first_attempt else CameraState.RECONNECTING)
            first_attempt = False

            cap, result = self._open()
            if cap is None or not result.success:
                self.last_error = result.reason
                log_error(
                    logger, ErrorCode.CAMERA_CONNECTION_ERROR, result.reason,
                    camera=self.name, url=self.masked_uri,
                )
                self._set_state(CameraState.ERROR, result.reason)
                delay = min(self._retry_delay * (2 ** retry_count), self._max_retry_delay)
                retry_count += 1
                logger.info("Reconnecting camera %s in %.0fs (attempt %d)", self.name, delay, retry_count)
                if self._stop.wait(delay):
                    break
                continue

            retry_count = 0
            self._info = result.info
            self.last_connected_at = time.time()
            self.last_error = ""
            self._set_state(CameraState.ONLINE)
            if result.first_frame is not None:
                self._publish(result.first_frame)

            try:
                self._read_loop(cap)
            finally:
                cap.release()

            if self._stop.is_set():
                break
            logger.warning("Camera disconnected: %s", self.name)
            self._set_state(CameraState.ERROR, "stream lost")
            if self._stop.wait(self._retry_delay):
                break

    def _read_loop(self, cap: cv2.VideoCapture) -> None:
        failures = 0
        file_fps = (self._info.reported_fps if self._info else 0.0) or 25.0
        while not self._stop.is_set():
            ok, frame = cap.read()
            if ok and frame is not None:
                failures = 0
                self._publish(frame)
                if self._state != CameraState.STREAMING:
                    self._set_state(CameraState.STREAMING)
                if self._is_file:
                    time.sleep(1.0 / file_fps)  # файлыг бодит хурдаар нь тоглуулна
                continue

            failures += 1
            if failures >= self.MAX_CONSECUTIVE_READ_FAILURES:
                return
            if self._is_file:  # dev adapter: файл дуусвал эхнээс нь давтана
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
