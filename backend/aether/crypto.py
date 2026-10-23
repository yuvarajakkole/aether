import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from aether.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().secret_key
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
        return Fernet(derived)


def encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored secret cannot be decrypted; AETHER_SECRET_KEY changed?") from exc


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "…"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
