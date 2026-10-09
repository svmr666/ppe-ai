"""RTSP биш камер (Mac вэбкамер, mp4) -> MediaMTX.

ai-service камераас аль хэдийн задалсан frame-үүдийг ffmpeg-ийн stdin руу шахаж, H.264 болгон MediaMTX руу RTSP-ээр
publish хийнэ. (Камерыг хоёр дахь удаа нээхгүй тул вэбкамерын "өөр програм ашиглаж байна" асуудал гарахгүй.)
Frame-ийг дарааллалгүй: хамгийн сүүлийнхийг л хадгална (удаан үед саатал хуримтлагдахгүй).
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from typing import Optional

import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger("streaming.ffmpeg")

TARGET_WIDTH = 960
FPS = 25


class FfmpegPublisher:
    def __init__(self, publish_url: str, name: str) -> None:
        self._url = publish_url
        self._name = name
        self._proc: Optional[subprocess.Popen] = None
        self._size: Optional[tuple[int, int]] = None
        self._latest: Optional[np.ndarray] = None
        self._cond = threading.Condition()
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        self._last_start_attempt = 0.0

    @staticmethod
    def available() -> bool:
        return shutil.which("ffmpeg") is not None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"ffmpeg-{self._name}", daemon=True)
        self._thread.start()

    def submit(self, frame: np.ndarray) -> None:
        """Камерын thread-ээс дуудагдана — хурдан, блоклохгүй."""
        with self._cond:
            self._latest = frame
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=5)
        self._kill()

    # ------------------------------------------------------------------
    def _spawn(self, width: int, height: int) -> None:
        cmd = [
            "ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
            "-use_wallclock_as_timestamps", "1", "-i", "-", "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
            "-g", str(FPS), "-bf", "0", "-r", str(FPS), "-f", "rtsp", "-rtsp_transport", "tcp", self._url,
        ]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._size = (width, height)
        logger.info("ffmpeg publisher эхэллээ | camera=%s %dx%d", self._name, width, height)

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._latest is None and not self._stop:
                    self._cond.wait(0.5)
                if self._stop:
                    return
                frame, self._latest = self._latest, None

            h, w = frame.shape[:2]
            tw = min(TARGET_WIDTH, w)
            th = int(h * tw / w) // 2 * 2  # yuv420p: тэгш хэмжээ
            tw = tw // 2 * 2
            if (tw, th) != (w, h):
                frame = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)

            if self._proc is not None and (self._proc.poll() is not None or self._size != (tw, th)):
                self._kill()
            if self._proc is None:
                if time.time() - self._last_start_attempt < 2.0:
                    continue  # MediaMTX унтраалттай үед давтан оролдлогыг хязгаарлана
                self._last_start_attempt = time.time()
                try:
                    self._spawn(tw, th)
                except OSError as exc:
                    logger.error("ffmpeg эхлүүлж чадсангүй: %s", exc)
                    continue

            try:
                assert self._proc and self._proc.stdin
                self._proc.stdin.write(np.ascontiguousarray(frame).tobytes())
            except (BrokenPipeError, OSError):
                self._kill()  # дараагийн frame дээр дахин эхлүүлнэ
