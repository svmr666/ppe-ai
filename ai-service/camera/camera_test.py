"""Phase 4: бодит камер дээр RTSP -> OpenCV -> Frame холболтыг шалгах хэрэгсэл.

    python main.py test-camera                 # холбогдох, 10 сек stream уншиж, frame хадгалах
    python main.py test-camera --display       # цонхонд live харуулах (q = гарах)
    python main.py watch-camera                # state өөрчлөлтийг харуулах (сүлжээний утсаа салгаж reconnect шалгана)
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2

from camera.rtsp_camera import CameraState, RTSPCamera
from config.settings import SERVICE_ROOT, Settings

TEMP_DIR = SERVICE_ROOT / "temp"


def _print_failure(settings: Settings, camera: RTSPCamera, reason: str) -> None:
    print("\nCamera connection failed")
    if settings.camera_source == "mp4":
        print(f"File:   {settings.video_file_path}")
    else:
        print(f"IP:     {settings.camera_ip}")
        print(f"Port:   {settings.camera_rtsp_port}")
        print(f"RTSP:   {camera.masked_uri}")
    print(f"Reason: {reason}\n")


def _check_config(settings: Settings) -> bool:
    problems = settings.camera_config_problems()
    if not problems:
        return True
    print("\n.env тохиргоо дутуу байна. ai-service/.env дээр дараах утгыг өөрөө оруулна уу:")
    for key in problems:
        print(f"  {key}=...")
    print("\n(.env.example-г .env болгон хуулж аваад бөглөнө: cp .env.example .env)\n")
    return False


def run_camera_test(settings: Settings, seconds: int = 10, display: bool = False) -> int:
    if not _check_config(settings):
        return 2

    camera = RTSPCamera.from_settings(settings)
    print(f"Connecting to camera '{camera.name}'")
    print(f"RTSP: {camera.masked_uri}")

    result = camera.probe()
    if not result.success or result.info is None:
        _print_failure(settings, camera, result.reason)
        return 1

    info = result.info
    print("\nCamera connected successfully")
    print(f"Resolution: {info.width}x{info.height}")
    print(f"FPS (reported by camera): {info.reported_fps:.0f}" if info.reported_fps > 0 else "FPS (reported by camera): n/a")

    # ---- stream test: background thread-ээр бодит frame урсаж байгааг баталгаажуулна ----
    print(f"\nReading stream for {seconds}s ...")
    camera.start()
    deadline = time.time() + 15
    while camera.get_latest() is None and time.time() < deadline:
        time.sleep(0.1)
    if camera.get_latest() is None:
        camera.stop()
        _print_failure(settings, camera, camera.last_error or "No frames received within 15s")
        return 1

    seen_ids: set[int] = set()
    last_frame = None
    started = time.time()
    next_report = started + 1
    try:
        while time.time() - started < seconds:
            packet = camera.get_latest()
            if packet and packet.frame_id not in seen_ids:
                seen_ids.add(packet.frame_id)
                last_frame = packet.frame
                if display:
                    cv2.imshow(f"{camera.name} (q to quit)", packet.frame)
            if display:
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            else:
                time.sleep(0.005)
            if time.time() >= next_report:
                print(f"  state={camera.state.value} frames={len(seen_ids)} fps={camera.measured_fps:.1f}")
                next_report += 1
    except cv2.error as exc:
        print(f"Display unavailable ({exc}). --display ашиглахгүйгээр дахин ажиллуулна уу.")
    finally:
        measured = camera.measured_fps
        state = camera.state
        camera.stop()
        if display:
            cv2.destroyAllWindows()

    if last_frame is None:
        _print_failure(settings, camera, "Stream opened but frames stopped arriving")
        return 1

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    out: Path = TEMP_DIR / "test_frame.jpg"
    cv2.imwrite(str(out), last_frame)

    print("\nRTSP stream active")
    print(f"State: {state.value}")
    print(f"Resolution: {last_frame.shape[1]}x{last_frame.shape[0]}")
    print(f"FPS: {measured:.1f} (measured)")
    print(f"Test frame saved: {out}\n")
    return 0


def run_camera_watch(settings: Settings, seconds: int = 0) -> int:
    """Камерын state өөрчлөлтийг харуулна. Утсаа салгаад ERROR -> RECONNECTING -> ONLINE шалгана."""
    if not _check_config(settings):
        return 2

    def on_change(name: str, old: CameraState, new: CameraState, reason: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {name}: {old.value} -> {new.value} {reason}")

    camera = RTSPCamera.from_settings(settings, on_state_change=on_change)
    print(f"Watching '{camera.name}' ({camera.masked_uri}). Ctrl+C = stop.")
    print("Камерын сүлжээний утсыг салгаж, дахин залгаад reconnect-ийг шалгана уу.\n")
    camera.start()
    started = time.time()
    try:
        while seconds == 0 or time.time() - started < seconds:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        camera.stop()
    return 0
