from datetime import datetime, timezone

import numpy as np

from config.settings import Settings
from inference.detection_types import Detection
from rules.violation_rules import ViolationType
from rules.zone_rules import ZoneType
from snapshot.storage_uploader import StorageUploader, StorageUploadError, UploadResult, build_storage_path
from violations.violation_handler import ViolationHandler
from violations.violation_service import ViolationEvent


def make_event(camera_id="cam-uuid-1"):
    return ViolationEvent(
        camera_name="Factory-Camera-01", camera_id=camera_id, zone=ZoneType.FACTORY,
        violation_type=ViolationType.NO_HELMET, track_id=1, confidence=0.9, inferred=False,
        detected_at=datetime(2026, 10, 8, 10, 35, 22, tzinfo=timezone.utc),
        person_box=Detection("person", 0.9, 10, 10, 100, 200), evidence_box=None, violation_id="vid-123",
    )


def test_storage_path_format():
    assert build_storage_path(make_event()) == "violations/2026/10/08/cam-uuid-1/vid-123.jpg"


def test_storage_path_falls_back_to_camera_name():
    assert "/Factory-Camera-01/vid-123.jpg" in build_storage_path(make_event(camera_id=None))


def test_violation_ids_are_unique():
    assert make_event().violation_id == "vid-123"
    from dataclasses import fields
    kwargs = {f.name: getattr(make_event(), f.name) for f in fields(ViolationEvent) if f.name != "violation_id"}
    assert ViolationEvent(**kwargs).violation_id != ViolationEvent(**kwargs).violation_id


class FakeUploader:
    def __init__(self, fail=False):
        self.fail, self.uploaded = fail, []

    def upload(self, event, snapshot):
        if self.fail:
            raise StorageUploadError("boom")
        self.uploaded.append(build_storage_path(event))
        return UploadResult(build_storage_path(event))


def settings_for(tmp_path):
    return Settings(_env_file=None, snapshot_local_dir=str(tmp_path), snapshot_save_local=True)


FRAME = np.full((480, 640, 3), 100, np.uint8)


def test_handler_saves_local_and_uploads(tmp_path):
    uploaded = []
    handler = ViolationHandler(
        settings_for(tmp_path), uploader=FakeUploader(), on_processed=lambda e, r: uploaded.append(r)
    )
    handler(make_event(), FRAME)
    handler.shutdown()
    assert len(list(tmp_path.glob("*.jpg"))) == 1
    assert uploaded and uploaded[0].storage_path.startswith("violations/")


def test_failed_upload_keeps_snapshot_in_fallback_dir(tmp_path):
    called = []
    handler = ViolationHandler(
        settings_for(tmp_path), uploader=FakeUploader(fail=True), on_processed=lambda e, r: called.append(r)
    )
    handler(make_event(), FRAME)
    handler.shutdown()
    assert len(list((tmp_path / "failed-upload").glob("*.jpg"))) == 1
    assert called == [None]  # violation DB-д snapshot_url=NULL-тай ч бүртгэгдэнэ


def test_no_uploader_means_local_only(tmp_path):
    handler = ViolationHandler(settings_for(tmp_path), uploader=None)
    handler(make_event(), FRAME)
    handler.shutdown()
    assert len(list(tmp_path.glob("*.jpg"))) == 1


def test_supabase_configured_flag():
    assert not Settings(_env_file=None).supabase_configured
    assert not Settings(_env_file=None, supabase_url="https://YOUR_PROJECT.supabase.co",
                        supabase_service_role_key="YOUR_SERVICE_ROLE_KEY").supabase_configured
    assert Settings(_env_file=None, supabase_url="https://abc.supabase.co",
                    supabase_service_role_key="eyJ.real.key").supabase_configured
