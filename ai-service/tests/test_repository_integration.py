"""Бодит Supabase PostgreSQL дээр ажиллана. Зөвхөн `RUN_DB_TESTS=1` үед. Өөрийн үүсгэсэн мөрүүдийг устгана."""

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from config.settings import get_settings
from inference.detection_types import Detection
from rules.violation_rules import ViolationType
from rules.zone_rules import ZoneType
from snapshot.storage_uploader import UploadResult
from violations.repository import DatabaseError, Repository
from violations.violation_service import ViolationEvent

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1" or not get_settings().database_configured,
    reason="set RUN_DB_TESTS=1 with a configured DATABASE_URL",
)


@pytest.fixture
def repo():
    r = Repository.from_settings(get_settings())
    yield r
    r.close()


def test_camera_violation_roundtrip_and_no_password_stored(repo):
    name = f"pytest-camera-{uuid.uuid4().hex[:8]}"
    cam = repo.ensure_camera(name=name, ip="10.0.0.9", port=554, username="u", camera_type="DAHUA",
                             channel=1, subtype=0, zone="FACTORY")
    try:
        again = repo.ensure_camera(name=name, ip="10.0.0.10", port=554, username="u", camera_type="DAHUA",
                                   channel=1, subtype=0, zone="OFFICE")
        assert again.id == cam.id  # нэрээр upsert, давхардахгүй

        event = ViolationEvent(
            camera_name=name, camera_id=cam.id, zone=ZoneType.OFFICE, violation_type=ViolationType.NO_WORK_UNIFORM,
            track_id=7, confidence=0.8765, inferred=True, detected_at=datetime.now(timezone.utc),
            person_box=Detection("person", 0.9, 0, 0, 1, 1), evidence_box=None,
        )
        repo.insert_violation(event, UploadResult("violations/test/x.jpg"))
        repo.set_camera_status(cam.id, "ONLINE", connected=True)
        repo._status_pool.shutdown(wait=True)

        with repo._engine.begin() as c:
            v = c.execute(text("select violation_type::text, person_id, status::text, snapshot_url, storage_path, "
                               "confidence from violations where id = cast(:i as uuid)"), {"i": event.violation_id}).one()
            cam_row = c.execute(text("select ip_address, zone_id is not null, status::text, last_connected_at is not null, "
                                     "password_encrypted from cameras where id = cast(:i as uuid)"), {"i": cam.id}).one()
        assert v[:5] == ("NO_WORK_UNIFORM", "T7", "OPEN", None, "violations/test/x.jpg")
        assert float(v[5]) == pytest.approx(0.8765)
        assert cam_row[0] == "10.0.0.10" and cam_row[1] and cam_row[2] == "ONLINE" and cam_row[3]
        assert cam_row[4] is None  # нууц үг DB-д огт бичигдэхгүй
    finally:
        with repo._engine.begin() as c:
            c.execute(text("delete from cameras where id = cast(:i as uuid)"), {"i": cam.id})  # violations cascade


def test_violation_for_unregistered_camera_is_rejected(repo):
    event = ViolationEvent(
        camera_name="x", camera_id=None, zone=ZoneType.FACTORY, violation_type=ViolationType.NO_HELMET, track_id=1,
        confidence=0.9, inferred=False, detected_at=datetime.now(timezone.utc),
        person_box=Detection("person", 0.9, 0, 0, 1, 1), evidence_box=None,
    )
    with pytest.raises(DatabaseError):
        repo.insert_violation(event, None)


def test_unknown_zone_is_rejected(repo):
    with pytest.raises(DatabaseError, match="Zone"):
        repo.zone_id("WAREHOUSE")
