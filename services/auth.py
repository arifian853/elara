"""
services/auth.py — JWT authentication helper for Admin users.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
import jwt

from config import settings

logger = logging.getLogger(__name__)

import hashlib
import os

SECRET_KEY = settings.admin_token or "elara_jwt_secret_signing_key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours


def hash_password(password: str, salt: str | None = None) -> str:
    """Hash password using SHA-256 with salt."""
    if not salt:
        salt = os.urandom(16).hex()
    pwd_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    ).hex()
    return f"{salt}${pwd_hash}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored salt$hash."""
    try:
        salt, pwd_hash = hashed.split('$')
        computed = hash_password(password, salt)
        return computed == hashed
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_jwt_token(token: str) -> dict | None:
    """
    Verify JWT access token.
    Returns payload dict if valid, None if invalid or expired.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError as e:
        logger.warning(f"JWT verification failed: {e}")
        return None


from fastapi import Header, HTTPException


def verify_admin_token(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> dict:
    """
    Verify authentication via X-Admin-Token OR Authorization: Bearer <jwt_token>.
    """
    token_candidate = None

    if x_admin_token and x_admin_token.strip():
        token_candidate = x_admin_token.strip()

    if not token_candidate and authorization and authorization.strip():
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token_candidate = parts[1]
        elif len(parts) == 1:
            token_candidate = parts[0]

    if token_candidate:
        # 1. Check configured static ADMIN_TOKEN
        if settings.admin_token and token_candidate == settings.admin_token:
            return {"sub": "admin", "role": "admin"}

        # 2. Check JWT Token signature
        payload = verify_jwt_token(token_candidate)
        if payload and payload.get("sub"):
            return payload

    logger.warning(f"Admin auth rejected: X-Admin-Token={x_admin_token}, Authorization={authorization}")
    raise HTTPException(status_code=401, detail="Unauthorized: Invalid Admin Credentials or Token")
