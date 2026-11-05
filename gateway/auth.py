"""Mot de passe local du banc d'essai."""

from __future__ import annotations

import hashlib
import secrets

from gateway.models import Settings


def set_password(settings: Settings, password: str) -> None:
    settings.password_salt = secrets.token_hex(16)
    settings.password_hash = _digest(password, settings.password_salt)


def ensure_password(settings: Settings) -> bool:
    if settings.password_hash:
        return False
    set_password(settings, "meshpass")
    return True


def check_password(password: str, settings: Settings) -> bool:
    if not settings.password_hash or not settings.password_salt:
        return False
    got = _digest(password, settings.password_salt)
    return secrets.compare_digest(got, settings.password_hash)


def _digest(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 80_000).hex()
