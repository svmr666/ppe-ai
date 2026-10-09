"""DB-д Fernet-ээр шифрлэгдсэн камерын нууц үгийг тайлах (backend-тэй ижил CAMERA_SECRET_KEY)."""

from __future__ import annotations

from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


def decrypt_secret(token: str, key: str) -> Optional[str]:
    try:
        return Fernet(key.encode("ascii")).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
