import pytest

from app.api.deps import verify_internal_key
from app.core.rate_limit import LoginLimiter
from app.core.security import (
    create_access_token, decode_access_token, hash_password, verify_password, verify_password_constant_time,
)
from app.schemas.camera import CameraCreate, CameraOut
from app.services.camera_service import build_rtsp_url


def test_password_hashing():
    h = hash_password("correct horse battery")
    assert h != "correct horse battery" and verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    long_pw = "x" * 500  # bcrypt-ийн 72 byte хязгаарт гацахгүй
    assert verify_password(long_pw, hash_password(long_pw)) and not verify_password(long_pw + "y", hash_password(long_pw))
    assert verify_password_constant_time("anything", None) is False


def test_jwt_roundtrip_and_tamper(monkeypatch):
    token = create_access_token("user-1")
    assert decode_access_token(token) == "user-1"
    assert decode_access_token(token[:-2] + "xx") is None
    assert decode_access_token("garbage") is None


def test_fernet_encrypt_roundtrip():
    from app.core.security import decrypt_secret, encrypt_secret

    token = encrypt_secret("p@ss:w/rd")
    assert "p@ss" not in token and decrypt_secret(token) == "p@ss:w/rd"
    assert decrypt_secret("not-a-token") is None


def test_rtsp_url_builder_encodes_credentials():
    assert build_rtsp_url("admin", "p@ss:w/rd", "192.168.1.100", 554, 1, 0) == (
        "rtsp://admin:p%40ss%3Aw%2Frd@192.168.1.100:554/cam/realmonitor?channel=1&subtype=0"
    )
    assert build_rtsp_url("a", "b", "h", 554, 2, 1, "HIKVISION").endswith("/Streaming/Channels/202")
    with pytest.raises(ValueError):
        build_rtsp_url("a", "b", "h", 554, 1, 0, "GENERIC_RTSP")


def test_camera_schemas_never_expose_password_or_url():
    assert not {"password", "password_encrypted", "rtsp_url"} & set(CameraOut.model_fields)
    c = CameraCreate(name="c", ip_address="192.168.1.5", username="u", password="secret")
    assert "secret" not in repr(c) and "secret" not in c.model_dump_json()


@pytest.mark.parametrize("bad", ["", "a b", "1.2.3.4; rm -rf /", "http://x", "x" * 300])
def test_camera_host_validation(bad):
    with pytest.raises(ValueError):
        CameraCreate(name="c", ip_address=bad, username="u", password="p")


def test_login_limiter():
    limiter = LoginLimiter(max_failures=3, window_seconds=60)
    for _ in range(3):
        assert limiter.is_locked("k") == 0
        limiter.fail("k")
    assert limiter.is_locked("k") > 0 and limiter.is_locked("other") == 0
    limiter.success("k")
    assert limiter.is_locked("k") == 0


def test_stream_token_is_scoped_to_one_path_and_is_not_a_session_token():
    from app.core.security import create_stream_token, decode_access_token, verify_stream_token

    token = create_stream_token("cam_abc")
    assert verify_stream_token(token, "cam_abc")
    assert not verify_stream_token(token, "cam_other")
    assert not verify_stream_token("garbage", "cam_abc")
    assert decode_access_token(token) is None  # stream токен нэвтрэлтийн session болж чадахгүй
    assert not verify_stream_token(create_access_token("user-1"), "cam_abc")  # session токен stream болж чадахгүй


def test_ws_ticket_is_not_a_session_or_stream_token():
    from app.core.security import create_stream_token, create_ws_ticket, decode_access_token, decode_ws_ticket

    ticket = create_ws_ticket("user-1")
    assert decode_ws_ticket(ticket) == "user-1"
    assert decode_access_token(ticket) is None  # ticket-ээр API руу нэвтэрч болохгүй
    assert decode_ws_ticket(create_access_token("user-1")) is None
    assert decode_ws_ticket(create_stream_token("cam_abc")) is None
    assert decode_ws_ticket("garbage") is None
