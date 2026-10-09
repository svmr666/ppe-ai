import base64
import os

import pytest
from cryptography.fernet import Fernet

from camera import camera_manager
from camera.camera_config import CameraConfig, CameraConfigError, config_from_db_row
from camera.camera_manager import CameraManager
from config.settings import Settings

KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()


def row(**over):
    base = dict(id="c1", name="Cam-1", ip="10.0.0.5", port=554, username="admin",
                password_encrypted=Fernet(KEY.encode()).encrypt(b"p@ss").decode(), camera_type="DAHUA",
                channel=1, subtype=0, enabled=True, zone="FACTORY", status="OFFLINE")
    base.update(over)
    return base


def test_config_from_db_row_decrypts_and_hides_password():
    cfg = config_from_db_row(row(), KEY)
    assert cfg.password == "p@ss" and "p@ss" not in repr(cfg) and "p@ss" not in str(cfg)


@pytest.mark.parametrize("over,key", [
    ({"password_encrypted": None}, KEY), ({"zone": None}, KEY), ({}, "CHANGE_ME"),
    ({"password_encrypted": "garbage"}, KEY), ({"camera_type": "GENERIC_RTSP"}, KEY),
])
def test_bad_db_rows_are_rejected(over, key):
    with pytest.raises(CameraConfigError):
        config_from_db_row(row(**over), key)


def test_fingerprint_changes_with_config():
    a = config_from_db_row(row(), KEY)
    assert a.fingerprint() == config_from_db_row(row(), KEY).fingerprint()
    assert a.fingerprint() != config_from_db_row(row(ip="10.0.0.6"), KEY).fingerprint()


class FakeWorker:
    started = []

    def __init__(self, camera, processor, on_violation=None):
        self.camera, self.stopped = camera, False

    def start(self):
        FakeWorker.started.append(self)

    def stop(self):
        self.stopped = True

    def latest(self):
        return None


class FakeNotifier:
    def push_frame(self, *a): pass
    def heartbeat(self): pass
    def camera_status(self, *a): pass


def test_sync_starts_stops_and_restarts_workers(monkeypatch):
    monkeypatch.setattr(camera_manager, "CameraWorker", FakeWorker)
    monkeypatch.setattr(camera_manager.FrameProcessor, "from_settings", classmethod(lambda cls, *a, **k: object()))
    monkeypatch.setattr(CameraConfig, "build_camera", lambda self, settings, on_state_change=None: object())
    FakeWorker.started.clear()

    configs = [config_from_db_row(row(), KEY), config_from_db_row(row(id="c2", name="Cam-2"), KEY)]
    manager = CameraManager(Settings(_env_file=None), detector=None, repo=None, notifier=FakeNotifier(),
                            config_provider=lambda: list(configs), handler=lambda e, f: None)

    manager.sync_now()
    assert sorted(manager._running) == ["c1", "c2"] and len(FakeWorker.started) == 2

    manager.sync_now()  # өөрчлөлтгүй => дахин эхлүүлэхгүй
    assert len(FakeWorker.started) == 2

    first = manager._running["c1"].worker
    configs[0] = config_from_db_row(row(ip="10.9.9.9"), KEY)  # тохиргоо өөрчлөгдөв
    removed = manager._running["c2"].worker
    configs.pop(1)  # Disconnect хийгдэв
    manager.sync_now()
    assert first.stopped and removed.stopped
    assert list(manager._running) == ["c1"] and manager._running["c1"].worker is not first


def test_webcam_config_needs_no_password_and_builds_device_camera():
    cfg = config_from_db_row(row(camera_type="LOCAL_WEBCAM", ip="0", password_encrypted=None, username="-"), "CHANGE_ME")
    camera = cfg.build_camera(Settings(_env_file=None))
    assert cfg.camera_type == "LOCAL_WEBCAM" and camera._device_index == 0 and camera.masked_uri == "webcam:0"


def test_ai_enabled_defaults_true_and_follows_db_row():
    assert config_from_db_row(row(), KEY).ai_enabled is True
    assert config_from_db_row(row(ai_enabled=False), KEY).ai_enabled is False
    webcam = dict(camera_type="LOCAL_WEBCAM", ip="0", password_encrypted=None, username="-")
    assert config_from_db_row(row(ai_enabled=False, **webcam), "CHANGE_ME").ai_enabled is False
    # AI унтраах/асаах нь worker-ийг дахин эхлүүлэх шалтгаан БИШ (вэбкамерыг дахин нээхгүй)
    assert config_from_db_row(row(), KEY).fingerprint() == config_from_db_row(row(ai_enabled=False), KEY).fingerprint()


def test_ai_toggle_applies_live_without_restarting_worker(monkeypatch):
    monkeypatch.setattr(camera_manager, "CameraWorker", FakeWorker)
    monkeypatch.setattr(camera_manager.FrameProcessor, "from_settings", classmethod(lambda cls, *a, **k: object()))
    monkeypatch.setattr(CameraConfig, "build_camera", lambda self, settings, on_state_change=None: object())
    FakeWorker.started.clear()

    configs = [config_from_db_row(row(ai_enabled=False), KEY)]
    manager = CameraManager(Settings(_env_file=None), detector=None, repo=None, notifier=FakeNotifier(),
                            config_provider=lambda: list(configs), handler=lambda e, f: None)
    manager.sync_now()
    worker = manager._running["c1"].worker
    assert worker.ai_enabled is False  # эхлэхдээ DB-ийн утгыг авна

    configs[0] = config_from_db_row(row(ai_enabled=True), KEY)  # "AI-тай холбох" асаалаа
    manager.sync_now()
    assert manager._running["c1"].worker is worker and not worker.stopped and worker.ai_enabled is True
    assert len(FakeWorker.started) == 1


def test_worker_does_not_run_inference_when_ai_disabled():
    import time
    from types import SimpleNamespace

    from camera.camera_worker import CameraWorker

    class Camera:
        name = "cam"

        def __init__(self): self.n = 0
        def start(self): pass
        def stop(self): pass

        def get_latest(self):
            self.n += 1
            return SimpleNamespace(frame=None, frame_id=self.n, timestamp=0.0)

    class Processor:
        zone = SimpleNamespace(value="FACTORY")
        calls = 0

        def process(self, frame, ts):
            Processor.calls += 1
            raise RuntimeError("AI унтраалттай үед дуудагдах ёсгүй")

    worker = CameraWorker(Camera(), Processor())
    worker.ai_enabled = False
    worker.start()
    time.sleep(0.3)
    worker.stop()
    assert Processor.calls == 0 and worker.latest() is None and worker.frames_processed == 0
