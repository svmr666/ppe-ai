from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.config import get_settings
from app.core.security import create_stream_token, encrypt_secret
from app.database.database import get_db
from app.database.models import Camera, CameraStatus, CameraType, User, Violation, Zone
from app.schemas.camera import CameraCreate, CameraOut, CameraTestRequest, CameraUpdate, ConnectionTestResult
from app.services import camera_service
from app.websocket.manager import manager

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


def _get_or_404(db: Session, camera_id: uuid.UUID) -> Camera:
    camera = db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    return camera


def _check_zone(db: Session, zone_id: uuid.UUID | None) -> None:
    if zone_id is not None and db.get(Zone, zone_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown zone_id")


async def _announce(camera: Camera) -> None:
    await manager.broadcast("CAMERA_UPDATED", camera_service.to_out(camera).model_dump(mode="json"))


@router.get("", response_model=list[CameraOut])
def list_cameras(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    cameras = db.execute(select(Camera).order_by(Camera.name)).unique().scalars().all()
    return [camera_service.to_out(c) for c in cameras]


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
async def create_camera(body: CameraCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    _check_zone(db, body.zone_id)
    data = body.model_dump(exclude={"password"})
    password = encrypt_secret(body.password.get_secret_value()) if body.password else None
    if body.camera_type is not CameraType.LOCAL_WEBCAM:
        data["mirror"] = False  # толин тусгал зөвхөн вэбкамерт; IP камер үргэлж анхдагч (эргүүлэхгүй)
    camera = Camera(**data, password_encrypted=password, status=CameraStatus.OFFLINE)
    db.add(camera)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Энэ нэртэй камер аль хэдийн байна")
    db.refresh(camera)
    await _announce(camera)
    return camera_service.to_out(camera)


@router.post("/test-connection", response_model=ConnectionTestResult)
async def test_unsaved(body: CameraTestRequest, _: User = Depends(require_admin)):
    """Камерыг хадгалахаас өмнө (Add form дээр) холболтыг шалгана."""
    if body.camera_type.value == "LOCAL_WEBCAM":
        return await run_in_threadpool(camera_service.probe_webcam, int(body.ip_address))
    return await run_in_threadpool(
        camera_service.probe_rtsp, username=body.username, password=body.password.get_secret_value(),
        ip=body.ip_address, port=body.rtsp_port, channel=body.channel, subtype=body.subtype,
        camera_type=body.camera_type.value,
    )


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return camera_service.to_out(_get_or_404(db, camera_id))


@router.put("/{camera_id}", response_model=CameraOut)
async def update_camera(camera_id: uuid.UUID, body: CameraUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    camera = _get_or_404(db, camera_id)
    changes = body.model_dump(exclude_unset=True, exclude={"password"})
    if "zone_id" in changes:
        _check_zone(db, changes["zone_id"])
    for key, value in changes.items():
        setattr(camera, key, value)
    if camera.camera_type is not CameraType.LOCAL_WEBCAM:
        camera.mirror = False
    if body.password is not None:
        camera.password_encrypted = encrypt_secret(body.password.get_secret_value())
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Энэ нэртэй камер аль хэдийн байна")
    db.refresh(camera)
    await _announce(camera)
    return camera_service.to_out(camera)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(camera_id: uuid.UUID, force: bool = False, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    camera = _get_or_404(db, camera_id)
    count = db.execute(select(func.count()).select_from(Violation).where(Violation.camera_id == camera_id)).scalar_one()
    if count and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Энэ камерт {count} violation бүртгэлтэй. Нотолгоо устахаас сэргийлж устгахгүй; "
            "Disconnect хийж орхино уу, эсвэл ?force=true-ээр violation-уудын хамт устгана уу.",
        )
    db.delete(camera)
    db.commit()
    await manager.broadcast("CAMERA_DELETED", {"id": str(camera_id)})


@router.post("/{camera_id}/test-connection", response_model=ConnectionTestResult)
async def test_connection(camera_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    camera = _get_or_404(db, camera_id)
    return await run_in_threadpool(camera_service.probe_stored_camera, camera)


@router.post("/{camera_id}/connect", response_model=CameraOut)
async def connect_camera(camera_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """AI service идэвхтэй (enabled) камеруудыг DB-ээс уншиж ажиллуулна."""
    camera = _get_or_404(db, camera_id)
    camera.enabled = True
    if camera.status is CameraStatus.OFFLINE:
        camera.status = CameraStatus.CONNECTING
    db.commit()
    db.refresh(camera)
    await _announce(camera)
    return camera_service.to_out(camera)


@router.post("/{camera_id}/disconnect", response_model=CameraOut)
async def disconnect_camera(camera_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    camera = _get_or_404(db, camera_id)
    camera.enabled = False
    camera.status = CameraStatus.OFFLINE
    db.commit()
    db.refresh(camera)
    await _announce(camera)
    return camera_service.to_out(camera)


@router.get("/{camera_id}/webrtc")
def webrtc_access(camera_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Шууд видеоны WebRTC (WHEP) хаяг + 2 минутын токен. Raw RTSP URL browser-д огт очихгүй."""
    camera = _get_or_404(db, camera_id)
    path = camera_service.stream_path(camera.id)
    settings = get_settings()
    return {
        "whep_url": f"{settings.mediamtx_public_url.rstrip('/')}/{path}/whep",
        "token": create_stream_token(path),
        "expires_in": settings.stream_token_expire_seconds,
    }
