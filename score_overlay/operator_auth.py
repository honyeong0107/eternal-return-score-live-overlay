from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


SIGNATURE_VERSION = "ETERCUT-LIVE-SCORE-V1"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def generate_identity() -> dict[str, str]:
    private_key = Ed25519PrivateKey.generate()
    return {
        "privateKey": _base64url(
            private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        ),
        "publicKey": _base64url(
            private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ),
    }


def sign_headers(
    method: str,
    url: str,
    body: bytes | None,
    private_key_value: str,
    *,
    wall_time=time.time,
    nonce_factory=lambda: secrets.token_urlsafe(18),
) -> dict[str, str]:
    timestamp = str(int(wall_time() * 1000))
    nonce = nonce_factory()
    content_hash = hashlib.sha256(body or b"").hexdigest()
    path = urlsplit(url).path or "/"
    message = "\n".join(
        (SIGNATURE_VERSION, method.upper(), path, timestamp, nonce, content_hash)
    ).encode("utf-8")
    private_key = Ed25519PrivateKey.from_private_bytes(_base64url_decode(private_key_value))
    signature = private_key.sign(message)
    return {
        "X-Live-Score-Timestamp": timestamp,
        "X-Live-Score-Nonce": nonce,
        "X-Live-Score-Content-SHA256": content_hash,
        "X-Live-Score-Signature": _base64url(signature),
    }


def generate_view_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
