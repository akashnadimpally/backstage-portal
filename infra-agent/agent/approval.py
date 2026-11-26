"""Human approval tokens for ``terraform apply``.

A token is an HMAC-signed, single-use, expiring credential bound to:
  * the request id
  * the SHA-256 of the exact saved plan file (tfplan)
  * the approver identity
If the plan is regenerated (e.g. after a repair) every earlier token is void.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import timedelta
from pathlib import Path

from .models import utcnow
from .settings import ApprovalSettings

MIN_SECRET_LENGTH = 32


class ApprovalError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _secret(settings: ApprovalSettings) -> bytes:
    value = os.environ.get(settings.secret_env, "")
    if len(value) < MIN_SECRET_LENGTH:
        raise ApprovalError(
            f"environment variable {settings.secret_env} must be set to a secret of at least "
            f"{MIN_SECRET_LENGTH} characters before approvals can be issued or verified"
        )
    return value.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(request_id: str, plan_sha256: str, approver: str, settings: ApprovalSettings) -> tuple[str, dict]:
    payload = {
        "rid": request_id,
        "plan": plan_sha256,
        "by": approver,
        "exp": int((utcnow() + timedelta(minutes=settings.token_ttl_minutes)).timestamp()),
        "nonce": secrets.token_hex(16),
    }
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_secret(settings), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", payload


def verify_token(
    token: str,
    request_id: str,
    plan_sha256: str,
    used_nonces: list[str],
    settings: ApprovalSettings,
) -> dict:
    try:
        body, sig = token.strip().split(".", 1)
    except ValueError as exc:
        raise ApprovalError("malformed approval token") from exc
    expected = _b64(hmac.new(_secret(settings), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise ApprovalError("approval token signature is invalid")
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApprovalError("approval token payload is invalid") from exc
    if payload.get("rid") != request_id:
        raise ApprovalError("approval token was issued for a different request")
    if payload.get("plan") != plan_sha256:
        raise ApprovalError("approval token does not match the current plan; the plan changed after approval")
    if int(payload.get("exp", 0)) < int(utcnow().timestamp()):
        raise ApprovalError("approval token has expired")
    if payload.get("nonce") in used_nonces:
        raise ApprovalError("approval token has already been used")
    return payload
