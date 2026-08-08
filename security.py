"""Local, single-machine authentication.

index.html's own copy says "Vos jeux et données de configuration sont
conservés dans un fichier JSON local... Aucune donnée n'est envoyée vers un
serveur externe." — this is a local desktop-style control panel, not a
multi-tenant SaaS. Auth is therefore intentionally simple: one (or a few)
local accounts, bcrypt-hashed passwords, and a signed session cookie. There
is no password reset flow / email sending, since there is no mail server in
scope for a local tool.
"""
from __future__ import annotations

import time
import uuid

import bcrypt
from fastapi import Cookie, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from core.config import DATA_DIR, settings
from core.storage import JsonListStore

SESSION_COOKIE_NAME = "obs_dynamics_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # 7 days

users_store = JsonListStore(DATA_DIR / "users.json")
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="obs-dynamics-session")


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_session_token(user_id: str) -> str:
    return _serializer.dumps({"uid": user_id, "iat": time.time()})


def read_session_token(token: str) -> str | None:
    try:
        payload = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    uid = payload.get("uid")
    return uid if isinstance(uid, str) else None


async def get_user_by_email(email: str) -> dict | None:
    email_lower = email.strip().lower()
    for user in await users_store.all():
        if user.get("email", "").lower() == email_lower:
            return user
    return None


async def get_user_by_id(user_id: str) -> dict | None:
    return await users_store.get(user_id)


async def create_user(email: str, password: str) -> dict:
    if await get_user_by_email(email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Un compte existe déjà avec cet email.")
    record = {
        "id": uuid.uuid4().hex,
        "email": email.strip().lower(),
        "password_hash": hash_password(password),
        "created_at": time.time(),
    }
    return await users_store.add(record)


async def authenticate(email: str, password: str) -> dict:
    user = await get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email ou mot de passe invalide.")
    return user


async def get_current_user(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict:
    if not session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Non authentifié.")
    user_id = read_session_token(session_token)
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalide ou expirée.")
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utilisateur introuvable.")
    return user


def public_user(user: dict) -> dict:
    """Never leak the password hash to the client."""
    return {"id": user["id"], "email": user["email"]}
