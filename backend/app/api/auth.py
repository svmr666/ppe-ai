from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import COOKIE_NAME, get_current_user
from app.config import get_settings
from app.core.rate_limit import login_limiter
from app.core.security import create_access_token, verify_password_constant_time
from app.database.database import get_db
from app.database.models import User
from app.schemas.auth import LoginRequest, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.lower()
    key = f"{request.client.host if request.client else '-'}|{email}"
    locked = login_limiter.is_locked(key)
    if locked:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Хэт олон оролдлого. {locked} секундын дараа дахин оролдоно уу.")

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    ok = verify_password_constant_time(body.password.get_secret_value(), user.password_hash if user else None)
    if not ok or user is None or not user.is_active:
        login_limiter.fail(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")  # аль нь буруу гэдгийг хэлэхгүй

    login_limiter.success(key)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME, create_access_token(str(user.id)), max_age=settings.jwt_expire_minutes * 60,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/",
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
