"""Credential vault. Broker API key/secret/token are encrypted at rest with
Fernet (AES-128-CBC + HMAC). The key comes from the VAULT_KEY env var — never
committed, never logged. Plaintext secrets exist only transiently in memory when
a customer's own broker adapter needs them; they are never persisted or printed."""
from __future__ import annotations
import os
from cryptography.fernet import Fernet, InvalidToken


class VaultError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.environ.get("VAULT_KEY", "")
    if not key:
        raise VaultError("VAULT_KEY not set — cannot encrypt/decrypt credentials")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:  # malformed key
        raise VaultError("VAULT_KEY is not a valid Fernet key") from e


def generate_key() -> str:
    """One-time key generation for ops (store in env/secret manager)."""
    return Fernet.generate_key().decode()


def encrypt(plaintext: str) -> bytes:
    if plaintext is None:
        plaintext = ""
    return _fernet().encrypt(plaintext.encode())


def decrypt(token: bytes) -> str:
    try:
        return _fernet().decrypt(token).decode()
    except InvalidToken as e:
        raise VaultError("credential decryption failed (wrong key or corrupt data)") from e
