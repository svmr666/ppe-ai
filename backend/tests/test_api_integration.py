"""Бодит Supabase DB дээр, зөвхөн RUN_DB_TESTS=1 үед. Өөрийн үүсгэсэн өгөгдлийг устгана."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import get_settings
from app.core.rate_limit import login_limiter
from app.core.security import hash_password
from app.database.database import new_session
from app.database.models import Camera, User, UserRole, Violation

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1" or get_settings().startup_problems(),
    reason="set RUN_DB_TESTS=1 with a fully configured backend/.env",
)

ORIGIN = get_settings().cors_origin_list[0]
PASSWORD = f"Pw-{uuid.uuid4().hex}"  # тестийн түр нууц үг
KEY = {"X-Internal-Key": get_settings().internal_api_key}


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app, base_url="http://localhost") as c:
        yield c


@pytest.fixture(scope="module")
def users():
    created = {}
    with new_session() as db:
        for role in UserRole:
            u = User(email=f"pytest-{role.value.lower()}-{uuid.uuid4().hex[:6]}@example.com",
                     password_hash=hash_password(PASSWORD), role=role)
            db.add(u)
            created[role] = u.email
        db.commit()
    yield created
    with new_session() as db:
        db.execute(delete(User).where(User.email.in_(created.values())))
        db.commit()


def login(client, email, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers={"Origin": ORIGIN})


def session_for(client, email) -> TestClient:
    from app.main import app

    c = TestClient(app, base_url="http://localhost", headers={"Origin": ORIGIN})
    assert login(c, email).status_code == 200
    return c


@pytest.fixture(scope="module")
def admin(client, users):
    return session_for(client, users[UserRole.ADMIN])


def test_everything_requires_login(client):
    fake = uuid.uuid4()
    for method, path in [("get", "/api/cameras"), ("get", "/api/violations"), ("get", "/api/dashboard/stats"),
                         ("get", f"/api/cameras/{fake}/webrtc"), ("get", "/api/users"), ("get", "/api/auth/me")]:
        assert getattr(client, method)(path).status_code == 401, path


def test_health_is_public_and_minimal(client):
    body = client.get("/health").json()
    assert set(body) == {"status", "database", "ai", "camera"} and body["database"] == "connected"


def test_login_sets_httponly_cookie_and_me(client, users):
    r = login(client, users[UserRole.HUB_EMPLOYEE])
    assert r.status_code == 200 and "password" not in r.text
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/auth/me").json()["role"] == "HUB_EMPLOYEE"
    client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    client.cookies.clear()


def test_wrong_password_is_generic_and_rate_limited(client, users):
    email = users[UserRole.HUB_EMPLOYEE]
    login_limiter._failures.clear()
    codes = [login(client, email, "wrong-password").status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert login(client, "nobody@example.com", "x").json()["detail"] == "Invalid email or password"
    login_limiter._failures.clear()


def test_bad_origin_is_rejected_on_unsafe_methods(client, users):
    r = client.post("/api/auth/login", json={"email": users[UserRole.HUB_EMPLOYEE], "password": PASSWORD}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_role_permissions(client, users):
    hub = session_for(client, users[UserRole.HUB_EMPLOYEE])
    body = {"name": f"pytest-{uuid.uuid4().hex[:6]}", "ip_address": "10.255.255.1", "username": "u", "password": "p"}
    assert hub.post("/api/cameras", json=body).status_code == 403
    assert hub.get("/api/cameras").status_code == 200
    assert hub.get("/api/users").status_code == 403


def test_camera_with_zone_serializes_zone_name(admin):
    zone = admin.get("/api/zones").json()[0]
    r = admin.post("/api/cameras", json={"name": f"pytest-{uuid.uuid4().hex[:6]}", "ip_address": "127.0.0.1",
                                         "username": "u", "password": "p", "zone_id": zone["id"]})
    assert r.status_code == 201, r.text
    cam = r.json()
    try:
        assert cam["zone"] == zone["name"] and cam["zone_id"] == zone["id"]
        assert [c["zone"] for c in admin.get("/api/cameras").json() if c["id"] == cam["id"]] == [zone["name"]]
    finally:
        admin.delete(f"/api/cameras/{cam['id']}")


def test_camera_lifecycle_never_leaks_password(admin):
    name = f"pytest-{uuid.uuid4().hex[:6]}"
    r = admin.post("/api/cameras", json={"name": name, "ip_address": "127.0.0.1", "rtsp_port": 1, "username": "admin", "password": "S3cret!pw"})
    assert r.status_code == 201, r.text
    cam = r.json()
    try:
        assert "S3cret" not in r.text and "password" not in cam and "rtsp" not in str(cam).lower().replace("rtsp_port", "")
        with new_session() as db:
            stored = db.get(Camera, uuid.UUID(cam["id"])).password_encrypted
        assert stored and "S3cret" not in stored  # DB-д шифрлэгдсэн

        assert admin.post("/api/cameras", json={"name": name, "ip_address": "127.0.0.1", "username": "u", "password": "p"}).status_code == 409
        assert "S3cret" not in admin.get(f"/api/cameras/{cam['id']}").text

        upd = admin.put(f"/api/cameras/{cam['id']}", json={"ip_address": "127.0.0.2"})
        assert upd.json()["ip_address"] == "127.0.0.2"
        assert admin.put(f"/api/cameras/{cam['id']}", json={"mirror": True}).json()["mirror"] is False  # IP камер эргэхгүй
        assert cam["ai_enabled"] is True and upd.json()["ai_enabled"] is True  # анхдагчаар AI-тай холбогдсон
        off = admin.put(f"/api/cameras/{cam['id']}", json={"ai_enabled": False})
        assert off.status_code == 200 and off.json()["ai_enabled"] is False and off.json()["ip_address"] == "127.0.0.2"
        assert admin.get(f"/api/cameras/{cam['id']}").json()["ai_enabled"] is False
        with new_session() as db:
            assert db.get(Camera, uuid.UUID(cam["id"])).password_encrypted == stored  # нууц үг өгөөгүй бол хэвээр

        t = admin.post(f"/api/cameras/{cam['id']}/test-connection").json()
        assert t["success"] is False and t["status"] == "ERROR" and t["message"] == "Unable to connect to RTSP stream"
        assert "S3cret" not in str(t)

        assert admin.post(f"/api/cameras/{cam['id']}/disconnect").json()["enabled"] is False
        assert admin.post(f"/api/cameras/{cam['id']}/connect").json()["enabled"] is True
        assert admin.post("/api/cameras", json={"name": "x", "ip_address": "bad host!", "username": "u", "password": "p"}).status_code == 422
    finally:
        assert admin.delete(f"/api/cameras/{cam['id']}").status_code == 204


def _make_violation(camera_id):
    with new_session() as db:
        v = Violation(camera_id=camera_id, violation_type="NO_HELMET", person_id="T1", confidence=0.9)
        db.add(v)
        db.commit()
        return v.id


def test_violation_workflow_and_delete_protection(client, admin, users):
    cam = admin.post("/api/cameras", json={"name": f"pytest-{uuid.uuid4().hex[:6]}", "ip_address": "127.0.0.1", "username": "u", "password": "p"}).json()
    vid = _make_violation(uuid.UUID(cam["id"]))
    hub = session_for(client, users[UserRole.HUB_EMPLOYEE])
    try:
        got = hub.get(f"/api/violations/{vid}").json()
        assert got["status"] == "OPEN" and got["camera_name"] == cam["name"] and got["snapshot_url"] is None
        assert hub.get("/api/violations?status=OPEN&page_size=5").json()["total"] >= 1

        assert hub.patch(f"/api/violations/{vid}/acknowledge").json()["status"] == "ACKNOWLEDGED"
        assert hub.patch(f"/api/violations/{vid}/acknowledge").status_code == 409
        r = hub.patch(f"/api/violations/{vid}/resolve").json()
        assert r["status"] == "RESOLVED" and r["resolved_by"] == users[UserRole.HUB_EMPLOYEE] and r["resolved_at"]
        assert hub.patch(f"/api/violations/{vid}/resolve").status_code == 409

        assert admin.delete(f"/api/cameras/{cam['id']}").status_code == 409  # нотолгоо устахаас хамгаална
        stats = hub.get("/api/dashboard/stats").json()
        assert stats["violations_total"] >= stats["violations_open"] >= 0 and stats["ai_status"] in ("ONLINE", "OFFLINE")
        assert stats["violations_total"] >= stats["violations_today"] and stats["violations_total"] >= 1
    finally:
        assert admin.delete(f"/api/cameras/{cam['id']}?force=true").status_code == 204


def test_internal_endpoints_and_websocket(client, admin):
    from app.main import app

    cam = admin.post("/api/cameras", json={"name": f"pytest-{uuid.uuid4().hex[:6]}", "ip_address": "127.0.0.1", "username": "u", "password": "p"}).json()
    vid = _make_violation(uuid.UUID(cam["id"]))
    try:
        assert client.post("/internal/heartbeat").status_code == 401
        assert client.post("/internal/heartbeat", headers={"X-Internal-Key": "wrong"}).status_code == 401

        with pytest.raises(Exception):  # cookie-гүй WebSocket татгалзагдана
            with client.websocket_connect("/ws"):
                pass
        with pytest.raises(Exception):  # итгэмжлэгдээгүй Origin
            with admin.websocket_connect("/ws", headers={"Origin": "https://evil.example", "Cookie": f"access_token={admin.cookies.get('access_token')}"}):
                pass

        cookie = {"Cookie": f"access_token={admin.cookies.get('access_token')}"}  # TestClient WS-д cookie jar дамжуулдаггүй
        with admin.websocket_connect("/ws", headers=cookie) as ws:
            assert ws.receive_json()["type"] == "CONNECTED"
            assert client.post(f"/internal/violations/{vid}/created", headers=KEY).status_code == 204
            event = ws.receive_json()
            assert event["type"] == "VIOLATION_CREATED"
            assert {"id", "camera_id", "camera_name", "zone", "violation_type", "confidence", "snapshot_url", "detected_at"} <= set(event["data"])
            assert event["data"]["id"] == str(vid)
            ws.send_text("ping")
            assert ws.receive_text() == "pong"

        # WebRTC хандалт: токен + WHEP хаяг; MediaMTX нэвтрэлт
        access = admin.get(f"/api/cameras/{cam['id']}/webrtc").json()
        path = f"cam_{cam['id']}"
        assert access["whep_url"].endswith(f"/{path}/whep") and access["token"] and "rtsp" not in str(access).lower()
        auth = lambda **kw: client.post("/internal/mediamtx/auth", json={"action": "read", "path": path, "protocol": "webrtc", **kw}).status_code
        assert auth(token=access["token"]) == 200
        assert auth(token="") == 401 and auth(token="garbage") == 401
        assert auth(token=access["token"], path="cam_" + str(uuid.uuid4())) == 401  # өөр камерын path-д хүчингүй
        assert client.post("/internal/mediamtx/auth", json={"action": "publish", "path": path, "password": "wrong"}).status_code == 401
        assert client.post("/internal/mediamtx/auth", json={"action": "publish", "path": path, "user": "ai", "password": get_settings().internal_api_key}).status_code == 200
        assert client.post("/internal/mediamtx/auth", json={"action": "api", "password": ""}).status_code == 401
        assert client.post("/internal/mediamtx/auth", json={"action": "playback", "path": path}).status_code == 401

        # AI боксын дамжуулалт
        with admin.websocket_connect("/ws", headers=cookie) as ws2:
            ws2.receive_json()
            body = {"people": [{"id": 1, "box": [0.1, 0.1, 0.5, 0.9], "helmet": "NO", "uniform": "YES", "violation": True}]}
            assert client.put(f"/internal/cameras/{cam['id']}/detections", headers=KEY, json=body).status_code == 204
            ev = ws2.receive_json()
            assert ev["type"] == "DETECTIONS" and ev["data"]["camera_id"] == cam["id"] and ev["data"]["people"][0]["violation"] is True
        assert client.put(f"/internal/cameras/{cam['id']}/detections", headers=KEY, json={"people": "bad"}).status_code == 400
    finally:
        admin.delete(f"/api/cameras/{cam['id']}?force=true")


def test_training_api_is_admin_only_and_roundtrips(client, admin, users, tmp_path, monkeypatch):
    from app.api import training
    from app.database.models import AppSetting

    monkeypatch.setattr(training, "_root", lambda: tmp_path)  # жинхэнэ сургалтын хавтсанд хүрэхгүй
    raw = tmp_path / "datasets" / "raw"
    raw.mkdir(parents=True)
    for name in ("pytest_a.jpg", "pytest_b.jpg"):
        (raw / name).write_bytes(b"\xff\xd8\xff\xd9")

    hub = session_for(client, users[UserRole.HUB_EMPLOYEE])
    assert client.get("/api/training/status").status_code == 401
    for method, url in (("get", "/api/training/status"), ("get", "/api/training/images"), ("delete", "/api/training/images"),
                        ("post", "/api/training/train/stop"), ("put", "/api/training/collection")):
        assert getattr(hub, method)(url).status_code == 403, url

    with new_session() as db:
        prior = db.get(AppSetting, "dataset_collection")
        prior_value = dict(prior.value) if prior else None
    try:
        r = admin.put("/api/training/collection", json={"enabled": True, "interval_seconds": 15})
        assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["interval_seconds"] == 15
        assert admin.put("/api/training/collection", json={"enabled": True, "interval_seconds": 1}).status_code == 422
        status = admin.get("/api/training/status").json()
        assert status["collection"]["interval_seconds"] == 15 and status["image_count"] == 2 and status["labeled_count"] == 0
    finally:
        with new_session() as db:
            row = db.get(AppSetting, "dataset_collection")
            if prior_value is None and row:
                db.delete(row)
            elif row:
                row.value = prior_value
            db.commit()

    listing = admin.get("/api/training/images", params={"filter": "unlabeled"}).json()
    assert listing["total"] == 2
    img = admin.get("/api/training/images/pytest_a.jpg")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert admin.get("/api/training/images/..%2F..%2F.env").status_code in (400, 404)
    assert admin.get("/api/training/images/missing.jpg").status_code == 404

    box = {"class_name": "helmet", "x1": 0.2, "y1": 0.1, "x2": 0.4, "y2": 0.3}
    assert admin.put("/api/training/images/pytest_a.jpg/labels", json={"boxes": [{**box, "class_name": "cat"}]}).status_code == 400
    assert admin.put("/api/training/images/pytest_a.jpg/labels", json={"boxes": [{**box, "x2": 1.5}]}).status_code == 422
    assert admin.put("/api/training/images/pytest_a.jpg/labels", json={"boxes": [box]}).json() == {"saved": 1}
    got = admin.get("/api/training/images/pytest_a.jpg/labels").json()
    assert got["source"] == "manual" and got["boxes"][0]["class_name"] == "helmet"
    assert admin.get("/api/training/status").json()["labeled_count"] == 1

    r = admin.post("/api/training/train", json={"epochs": 20, "base": "n"})
    assert r.status_code == 400  # суурь жин/шошгын тоо хүрэлцэхгүй => зөв татгалзана
    assert admin.delete("/api/training/images").json() == {"deleted": 2}
    assert admin.get("/api/training/status").json()["image_count"] == 0
