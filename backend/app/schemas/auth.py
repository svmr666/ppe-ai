from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

from app.core.security import MIN_PASSWORD_LENGTH
from app.database.models import UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: Optional[str]
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=100)
    password: SecretStr = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)
    role: UserRole = UserRole.HUB_EMPLOYEE


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(None, max_length=100)
    password: Optional[SecretStr] = Field(None, min_length=MIN_PASSWORD_LENGTH, max_length=200)
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
