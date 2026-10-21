import hashlib
import hmac
import os
import secrets
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import ROOT, get_settings

USER_ROLES = (('doctor', 'Dr. Aarav Mehta'), ('nurse', 'Priya Sharma'), ('billing_executive', 'Ravi Kumar'), ('technician', 'Anand Patel'), ('admin', 'System Administrator'))
bearer = HTTPBearer(auto_error=False)
_KEY_LOCK = threading.Lock()


@lru_cache
def password_records():
    records = {}
    settings = get_settings()
    for role, name in USER_ROLES:
        username = getattr(settings, role + '_username').strip()
        password = getattr(settings, role + '_password')
        if not username and not password:
            continue
        if not username or not password:
            raise RuntimeError(f'Configure both username and password for {role}.')
        if username in records:
            raise RuntimeError('Each configured account must have a unique username.')
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        records[username] = (name, role, salt, digest)
    return records


@lru_cache
def signing_key() -> str:
    configured = get_settings().jwt_secret
    if configured:
        if len(configured) < 32:
            raise RuntimeError('JWT_SECRET must contain at least 32 characters.')
        return configured
    with _KEY_LOCK:
        path = ROOT / '.state' / 'session.key'
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            descriptor, temporary_name = tempfile.mkstemp(prefix='.session-', suffix='.key', dir=path.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                    stream.write(secrets.token_urlsafe(48))
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    # Publish an already-complete file without replacing a key
                    # concurrently created by another backend process.
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        key = path.read_text(encoding='utf-8').strip()
        if len(key) < 32:
            raise RuntimeError('Persisted session key is incomplete or invalid. Configure a valid JWT_SECRET before starting the backend.')
        return key


def authenticate(username: str, password: str) -> dict | None:
    records = password_records()
    if not records:
        return None
    record = records.get(username)
    fallback = next(iter(records.values()))
    _, _, salt, expected = record or fallback
    actual = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    if not hmac.compare_digest(actual, expected) or record is None:
        return None
    return {'username': username, 'name': record[0], 'role': record[1]}


def create_token(user: dict) -> str:
    now = int(time.time())
    return jwt.encode({'sub': user['username'], 'role': user['role'], 'iat': now,
                       'exp': now + get_settings().session_hours * 3600,
                       'iss': 'medibot', 'aud': 'medibot-api'}, signing_key(), algorithm='HS256')


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if not credentials:
        raise HTTPException(401, 'Please sign in to continue.', headers={'WWW-Authenticate': 'Bearer'})
    try:
        payload = jwt.decode(credentials.credentials, signing_key(), algorithms=['HS256'],
                             audience='medibot-api', issuer='medibot',
                             options={'require': ['sub', 'role', 'iat', 'exp']})
        record = password_records().get(payload['sub'])
        if record is None or payload['role'] != record[1]:
            raise ValueError('Unknown user or role')
        return {'username': payload['sub'], 'name': record[0], 'role': record[1]}
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(401, 'Your session is invalid or expired. Please sign in again.',
                            headers={'WWW-Authenticate': 'Bearer'}) from None
