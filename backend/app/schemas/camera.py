from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.database.models import CameraStatus, CameraType

_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def _validate_host(value: str) -> str:
    value = value.strip()
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass
    if not _HOST_RE.match(value):
        raise ValueError("ip_address must be a valid IP address or hostname")
    return value


class CameraBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    ip_address: str  # LOCAL_WEBCAM үед төхөөрөмжийн дугаар ("0", "1")
    rtsp_port: int = Field(554, ge=1, le=65535)
    username: Optional[str] = Field(None, max_length=100)
    camera_type: CameraType = CameraType.DAHUA
    channel: int = Field(1, ge=1, le=256)
    subtype: int = Field(0, ge=0, le=9)
    zone_id: Optional[uuid.UUID] = None
    mirror: bool = False  # LOCAL_WEBCAM: толин тусгал (selfie) харагдац
    ai_enabled: bool = True  # AI-тай холбох (хүн/каск/хувцас илрүүлж зөрчил бүртгэнэ)

    @field_validator("ip_address")
    @classmethod
    def _host(cls, v: str) -> str:
        return _validate_host(v)

    @field_validator("name", "username")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v is not None else v

    @model_validator(mode="after")
    def _webcam_or_rtsp(self):
        if self.camera_type is CameraType.LOCAL_WEBCAM:
            if not self.ip_address.isdigit() or len(self.ip_address) > 2:
                raise ValueError("LOCAL_WEBCAM: ip_address талбарт камерын төхөөрөмжийн дугаар (0, 1, ...) оруулна")
            self.username = "-"
        elif not self.username:
            raise ValueError("username шаардлагатай")
        return self


class CameraCreate(CameraBase):
    password: Optional[SecretStr] = Field(None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _password_for_rtsp(self):
        if self.camera_type is not CameraType.LOCAL_WEBCAM and self.password is None:
            raise ValueError("password шаардлагатай")
        return self


class CameraUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    ip_address: Optional[str] = None
    rtsp_port: Optional[int] = Field(None, ge=1, le=65535)
    username: Optional[str] = Field(None, min_length=1, max_length=100)
    password: Optional[SecretStr] = Field(None, min_length=1, max_length=200)  # өгөхгүй бол хуучин нууц үг хэвээр
    camera_type: Optional[CameraType] = None
    channel: Optional[int] = Field(None, ge=1, le=256)
    subtype: Optional[int] = Field(None, ge=0, le=9)
    zone_id: Optional[uuid.UUID] = None
    mirror: Optional[bool] = None
    ai_enabled: Optional[bool] = None

    @field_validator("ip_address")
    @classmethod
    def _host(cls, v: Optional[str]) -> Optional[str]:
        return _validate_host(v) if v is not None else v


class CameraTestRequest(CameraCreate):
    pass


class CameraOut(BaseModel):
    """Нууц үг, RTSP URL-ийг ХЭЗЭЭ Ч агуулахгүй."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    ip_address: str
    rtsp_port: int
    username: str
    camera_type: CameraType
    channel: int
    subtype: int
    zone_id: Optional[uuid.UUID]
    zone: Optional[str] = None
    status: CameraStatus
    mirror: bool
    ai_enabled: bool
    enabled: bool
    last_connected_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ConnectionTestResult(BaseModel):
    success: bool
    status: str  # ONLINE | ERROR
    message: str
    reason: Optional[str] = None  # алдааны тодорхой шалтгаан (нууц үг агуулахгүй)
    resolution: Optional[str] = None
