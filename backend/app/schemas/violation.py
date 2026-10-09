from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.database.models import ViolationStatus, ViolationType, ZoneType


class ViolationOut(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    camera_name: str
    zone_id: Optional[uuid.UUID]
    zone: Optional[ZoneType]
    violation_type: ViolationType
    person_id: Optional[str]
    confidence: Optional[float]
    snapshot_url: Optional[str]  # богино хугацаатай signed URL (bucket private)
    detected_at: datetime
    status: ViolationStatus
    resolved_at: Optional[datetime]
    resolved_by: Optional[str]
    created_at: datetime


class ViolationPage(BaseModel):
    items: list[ViolationOut]
    total: int
    page: int
    page_size: int
