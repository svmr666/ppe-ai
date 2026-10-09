import httpx
import numpy as np

from streaming import ffmpeg_publisher
from streaming.ffmpeg_publisher import FfmpegPublisher
from streaming.mediamtx import MediaMTXClient, stream_path


def make_client(handler):
    return MediaMTXClient("http://mtx:9997", "rtsp://127.0.0.1:8554", "k3y-_A", transport=httpx.MockTransport(handler))


def test_stream_path_matches_backend_convention():
    assert stream_path("1234") == "cam_1234"


def test_publish_url_uses_ai_user_and_encodes_key():
    client = make_client(lambda r: httpx.Response(200))
    assert client.publish_url("cam_x") == "rtsp://ai:k3y-_A@127.0.0.1:8554/cam_x"


def test_register_then_replace_and_remove_paths_with_basic_auth():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("authorization", "")[:6]))
        return httpx.Response(200, json={})

    client = make_client(handler)
    assert client.ensure_rtsp_source("cam_a", "rtsp://u:p@10.0.0.5/x")
    assert client.ensure_rtsp_source("cam_a", "rtsp://u:p@10.0.0.5/x")  # өөрчлөлтгүй => дахин дуудахгүй
    assert client.ensure_rtsp_source("cam_a", "rtsp://u:p@10.0.0.6/x")  # source солигдвол replace
    client.remove_path("cam_a")
    assert calls == [
        ("POST", "/v3/config/paths/add/cam_a", "Basic "),
        ("POST", "/v3/config/paths/replace/cam_a", "Basic "),
        ("DELETE", "/v3/config/paths/delete/cam_a", "Basic "),
    ]


def test_existing_path_from_previous_run_is_replaced():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(400, text="path already exists") if "/add/" in request.url.path else httpx.Response(200)

    assert make_client(handler).ensure_rtsp_source("cam_b", "rtsp://x")
    assert seen == ["/v3/config/paths/add/cam_b", "/v3/config/paths/replace/cam_b"]


def test_mediamtx_down_returns_false_without_raising():
    def handler(request):
        raise httpx.ConnectError("down")

    assert make_client(handler).ensure_rtsp_source("cam_c", "rtsp://x") is False


def test_ffmpeg_publisher_builds_h264_rtsp_command(monkeypatch):
    captured = {}

    class FakeProc:
        stdin = None

        def poll(self):
            return None

    monkeypatch.setattr(ffmpeg_publisher.subprocess, "Popen", lambda cmd, **kw: captured.setdefault("cmd", cmd) and FakeProc())
    pub = FfmpegPublisher("rtsp://ai:k@127.0.0.1:8554/cam_z", "Mac")
    pub._spawn(960, 540)
    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg" and "960x540" in cmd and "libx264" in cmd and "zerolatency" in cmd
    assert cmd[-1] == "rtsp://ai:k@127.0.0.1:8554/cam_z" and "rtsp" in cmd


def test_publisher_keeps_only_latest_frame():
    pub = FfmpegPublisher("rtsp://x", "n")
    first, second = np.zeros((4, 4, 3), np.uint8), np.ones((4, 4, 3), np.uint8)
    pub.submit(first)
    pub.submit(second)
    assert pub._latest is second  # дараалалгүй: хамгийн сүүлийнх
