"""PostgreSQL (Supabase) руу бичих давхарга: камер бүртгэх, камерын төлөв шинэчлэх, violation хадгалах.

Нууцлал
- SQL бүхэлдээ parameterized (SQL injection-гүй). DATABASE_URL хэзээ ч log-д орохгүй.
- Supabase руу SSL заавал (localhost-оос бусад үед `sslmode=require`).
- Камерын нууц үгийг энэ модуль DB-д ХЭЗЭЭ Ч бичихгүй. `cameras.password_encrypted` баганыг зөвхөн
  backend (Fernet шифрлэлтээр) удирдана; ensure_camera() түүнийг өөрчлөхгүй.
- Зургийн binary DB-д орохгүй: зөвхөн snapshot_url ба storage_path.
"""

from __future__ import annotations

import json
import threading
from typing import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError

from config.settings import Settings
from snapshot.storage_uploader import UploadResult
from utils.errors import ErrorCode
from utils.logger import get_logger, log_error
from violations.violation_service import ViolationEvent

logger = get_logger("violations.repository")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class DatabaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class CameraRecord:
    id: str
    zone_id: Optional[str]


def create_db_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.drivername in ("postgresql", "postgres"):
        url = url.set(drivername="postgresql+psycopg2")  # driver-ийг тодорхой зааж өгнө (psycopg2-binary)
    connect_args: dict = {"connect_timeout": 10}
    if url.host not in _LOCAL_HOSTS and "sslmode" not in url.query:
        connect_args["sslmode"] = "require"
    return create_engine(
        url, pool_size=3, max_overflow=2, pool_pre_ping=True, pool_recycle=1800, connect_args=connect_args
    )


class Repository:
    def __init__(self, engine: Engine, fallback_dir: Optional[Path] = None) -> None:
        self._engine = engine
        self._fallback_dir = fallback_dir
        self._zone_ids: dict[str, str] = {}
        self._zone_lock = threading.Lock()
        # Камерын төлөв шинэчлэлтийг камерын thread-ийг хаахгүйн тулд дарааллаар, тусдаа thread-д бичнэ
        self._status_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera-status")

    @classmethod
    def from_settings(cls, settings: Settings) -> "Repository":
        return cls(create_db_engine(settings.database_url), fallback_dir=settings.resolved_snapshot_dir / "failed-db")

    def ping(self) -> None:
        self._run("ping", lambda conn: conn.execute(text("select 1")))

    def close(self) -> None:
        self._status_pool.shutdown(wait=True)  # дараалалд үлдсэн төлөв бичилтүүдийг дуустал хүлээнэ
        self._engine.dispose()

    # ------------------------------------------------------------------ cameras
    def zone_id(self, zone_name: str) -> str:
        with self._zone_lock:
            if zone_name not in self._zone_ids:
                rows = self._run("load zones", lambda c: c.execute(text("select id::text, name from zones")).all())
                self._zone_ids = {name: zid for zid, name in rows}
            if zone_name not in self._zone_ids:
                raise DatabaseError(f"Zone '{zone_name}' not found in database (database/schema.sql ажиллуулсан уу?)")
            return self._zone_ids[zone_name]

    def ensure_camera(
        self, *, name: str, ip: str, port: int, username: str, camera_type: str,
        channel: int, subtype: int, zone: str,
    ) -> CameraRecord:
        """Камерыг нэрээр нь бүртгэнэ (байвал шинэчилнэ). Нууц үгийг ХАРАХГҮЙ, БИЧИХГҮЙ."""
        zone_id = self.zone_id(zone)
        row = self._run(
            "register camera",
            lambda c: c.execute(
                text(
                    """
                    insert into cameras (name, ip_address, rtsp_port, username, camera_type, channel, subtype, zone_id)
                    values (:name, :ip, :port, :username, cast(:ctype as camera_type), :channel, :subtype,
                            cast(:zone_id as uuid))
                    on conflict (name) do update set
                        ip_address = excluded.ip_address, rtsp_port = excluded.rtsp_port,
                        username = excluded.username, camera_type = excluded.camera_type,
                        channel = excluded.channel, subtype = excluded.subtype, zone_id = excluded.zone_id
                    returning id::text
                    """
                ),
                dict(name=name, ip=ip, port=port, username=username, ctype=camera_type,
                     channel=channel, subtype=subtype, zone_id=zone_id),
            ).one(),
        )
        logger.info("Camera registered in database | camera=%s id=%s zone=%s", name, row[0], zone)
        return CameraRecord(id=row[0], zone_id=zone_id)

    def set_camera_status(
        self, camera_id: str, status: str, connected: bool = False, on_done: Optional[Callable[[], None]] = None
    ) -> None:
        """Камерын төлөвийг (ONLINE/STREAMING/ERROR/...) дараалалд оруулж асинхрон бичнэ."""

        def job() -> None:
            try:
                self._run(
                    "update camera status",
                    lambda c: c.execute(
                        text(
                            """
                            update cameras set status = cast(:status as camera_status),
                                   last_connected_at = case when :connected then now() else last_connected_at end
                            where id = cast(:id as uuid)
                            """
                        ),
                        dict(status=status, connected=connected, id=camera_id),
                    ),
                )
            except DatabaseError:
                return  # _run аль хэдийн DATABASE_ERROR log хийсэн
            if on_done:
                on_done()

        self._status_pool.submit(job)

    def load_cameras(self) -> list[dict]:
        """Бүх камерыг (шифрлэгдсэн нууц үгтэй) уншина. Нууц үгийг тайлах нь CameraManager-ийн үүрэг."""
        rows = self._run(
            "load cameras",
            lambda c: c.execute(
                text(
                    """
                    select c.id::text, c.name, c.ip_address, c.rtsp_port, c.username, c.password_encrypted,
                           c.camera_type::text, c.channel, c.subtype, c.enabled, z.name, c.status::text, c.mirror, c.ai_enabled
                    from cameras c left join zones z on z.id = c.zone_id
                    """
                )
            ).all(),
        )
        keys = ("id", "name", "ip", "port", "username", "password_encrypted", "camera_type", "channel", "subtype", "enabled", "zone", "status", "mirror", "ai_enabled")
        return [dict(zip(keys, row)) for row in rows]

    def load_dataset_setting(self) -> Optional[dict]:
        """Сургалтын өгөгдөл цуглуулалтын тохиргоо (backend бичнэ). Байхгүй бол None."""
        rows = self._run(
            "load dataset setting",
            lambda c: c.execute(text("select value from app_settings where key = 'dataset_collection'")).all(),
        )
        return dict(rows[0][0]) if rows and rows[0][0] else None

    def mark_camera_error(self, camera_id: str) -> None:
        self.set_camera_status(camera_id, "ERROR")

    # ------------------------------------------------------------------ violations
    def insert_violation(self, event: ViolationEvent, upload: Optional[UploadResult]) -> None:
        if not event.camera_id:
            raise DatabaseError("Violation has no camera_id (camera is not registered in database)")
        params = dict(
            id=event.violation_id,
            camera_id=event.camera_id,
            zone_id=self.zone_id(event.zone.value),
            vtype=event.violation_type.value,
            person_id=event.person_id,
            confidence=round(event.confidence, 4),
            snapshot_url=None,  # bucket private: URL-ийг backend хүсэлт бүрт signed URL-ээр үүсгэнэ
            storage_path=upload.storage_path if upload else None,
            detected_at=event.detected_at,
        )
        try:
            self._run(
                "insert violation",
                lambda c: c.execute(
                    text(
                        """
                        insert into violations (id, camera_id, zone_id, violation_type, person_id, confidence,
                                                snapshot_url, storage_path, detected_at, status)
                        values (cast(:id as uuid), cast(:camera_id as uuid), cast(:zone_id as uuid),
                                cast(:vtype as violation_type), :person_id, :confidence,
                                :snapshot_url, :storage_path, :detected_at, 'OPEN')
                        """
                    ),
                    params,
                ),
            )
        except DatabaseError:
            self._write_fallback(params)
            raise
        logger.info("Violation saved | id=%s type=%s camera=%s", event.violation_id, event.violation_type.value,
                    event.camera_name)

    # ------------------------------------------------------------------
    def _run(self, action: str, fn):
        try:
            with self._engine.begin() as conn:
                return fn(conn)
        except SQLAlchemyError as exc:
            # exc-ийн мессеж DSN агуулж болзошгүй тул зөвхөн төрлийг + эхний мөрийг (нууцгүй) log хийнэ
            detail = str(getattr(exc, "orig", exc)).strip().splitlines()[0][:200]
            log_error(logger, ErrorCode.DATABASE_ERROR, f"{action} failed: {type(exc).__name__}: {detail}")
            raise DatabaseError(f"{action} failed") from None

    def _write_fallback(self, params: dict) -> None:
        if not self._fallback_dir:
            return
        try:
            self._fallback_dir.mkdir(parents=True, exist_ok=True)
            path = self._fallback_dir / f"{params['id']}.json"
            path.write_text(json.dumps(params, default=str, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.warning("Violation could not be saved to DB; stored for manual replay: %s", path)
        except OSError:
            logger.exception("Could not write DB fallback file")
