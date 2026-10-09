from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_staff
from app.database.database import get_db
from app.database.models import User, Violation, ViolationStatus, ViolationType, ZoneType
from app.schemas.violation import ViolationOut, ViolationPage
from app.services import violation_service as svc
from app.websocket.manager import manager

router = APIRouter(prefix="/api/violations", tags=["violations"])


def _get_or_404(db: Session, violation_id: uuid.UUID) -> Violation:
    violation = db.get(Violation, violation_id)
    if not violation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Violation not found")
    return violation


@router.get("", response_model=ViolationPage)
async def list_violations(
    status_: Optional[ViolationStatus] = Query(None, alias="status"),
    zone: Optional[ZoneType] = None,
    violation_type: Optional[ViolationType] = None,
    camera_id: Optional[uuid.UUID] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=svc.MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    def work():
        rows, total = svc.list_violations(
            db, status=status_, zone=zone, violation_type=violation_type, camera_id=camera_id, page=page, page_size=page_size
        )
        return ViolationPage(items=svc.serialize_many(rows), total=total, page=page, page_size=page_size)

    return await run_in_threadpool(work)


@router.get("/{violation_id}", response_model=ViolationOut)
async def get_violation(violation_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    def work():
        return svc.serialize_many([_get_or_404(db, violation_id)])[0]

    return await run_in_threadpool(work)


async def _transition(db: Session, violation_id: uuid.UUID, action) -> ViolationOut:
    def work():
        violation = _get_or_404(db, violation_id)
        try:
            action(violation)
        except svc.InvalidTransition as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        db.refresh(violation)
        return svc.serialize_many([violation])[0]

    out = await run_in_threadpool(work)
    await manager.broadcast("VIOLATION_UPDATED", out.model_dump(mode="json"))
    return out


@router.patch("/{violation_id}/acknowledge", response_model=ViolationOut)
async def acknowledge(violation_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require_staff)):
    return await _transition(db, violation_id, lambda v: svc.acknowledge(db, v))


@router.patch("/{violation_id}/resolve", response_model=ViolationOut)
async def resolve(violation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    return await _transition(db, violation_id, lambda v: svc.resolve(db, v, user))
