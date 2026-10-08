









from __future__ import annotations

import hashlib
import secrets
import string
import time

from .activation_manager import _KEY_RE

PAIRING_TTL_S = 1800


USER_CODE_ALPHABET = "".join(
    ch for ch in string.digits + string.ascii_uppercase if ch not in "01IO"
)
USER_CODE_LENGTH = 6

_START_FAILURES_TO_LEGACY = 3
_START_RETRY_WAIT_S = 1.0

_RETRY_AFTER_CAP_S = 10.0

_CLAIM_TRIES = 3
_CLAIM_RETRY_WAIT_S = 1.0


def make_pairing_secret() -> str:

    return secrets.token_urlsafe(32)


def pairing_secret_hash(secret: str) -> str:

    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def normalize_user_code(text: str) -> str:


    raw = "".join(ch for ch in str(text or "").upper() if ch not in " -\t")
    if len(raw) != USER_CODE_LENGTH or any(ch not in USER_CODE_ALPHABET for ch in raw):
        return ""
    return raw


def format_user_code(text: str) -> str:

    raw = "".join(ch for ch in str(text or "").upper() if ch.isalnum())[:USER_CODE_LENGTH]
    return raw if len(raw) <= 3 else f"{raw[:3]}-{raw[3:]}"


def _retry_after(result: dict, fallback: float) -> float:
    try:
        value = float(result.get("retry_after"))
    except (TypeError, ValueError, OverflowError):
        return fallback
    return min(max(value, 0.0), _RETRY_AFTER_CAP_S) if value == value else fallback


def _is_cancelled(result) -> bool:
    return isinstance(result, dict) and str(result.get("code") or "").upper() == "CANCELLED"


def start_pairing_session(client, secret_hash: str, loopback_port: int | None, sleep=time.sleep) -> dict:






    failures = 0
    rate_limited_once = False
    while True:
        result = client.start_pairing(secret_hash, loopback_port, PAIRING_TTL_S)
        if _is_cancelled(result):
            return {"outcome": "cancelled"}
        if isinstance(result, dict) and "error" not in result:
            code, url = result.get("code"), result.get("connect_url")
            if isinstance(code, str) and code and isinstance(url, str) and url.startswith(("https://", "http://")):
                try:
                    expires_in = int(result.get("expires_in") or PAIRING_TTL_S)
                except (TypeError, ValueError):
                    expires_in = PAIRING_TTL_S
                return {"outcome": "started", "code": code, "connect_url": url,
                        "expires_in": max(60, min(expires_in, PAIRING_TTL_S))}
            failures += 1
        else:
            status = result.get("http_status") if isinstance(result, dict) else None
            if status == 400:
                return {"outcome": "legacy", "reason": "invalid_request"}
            if status == 429 and not rate_limited_once:
                rate_limited_once = True
                sleep(_retry_after(result, _START_RETRY_WAIT_S))
                continue
            if status == 429:
                return {"outcome": "legacy", "reason": "rate_limited"}
            failures += 1
        if failures >= _START_FAILURES_TO_LEGACY:
            return {"outcome": "legacy", "reason": "start_failed"}
        sleep(_START_RETRY_WAIT_S)


def claim_pairing_key(client, code: str, secret: str, grant: str = "", user_code: str = "",
                      sleep=time.sleep) -> dict:






    for attempt in range(_CLAIM_TRIES):
        result = client.claim_pairing(code, secret, grant=grant, user_code=user_code)
        if _is_cancelled(result):
            return {"outcome": "cancelled_request"}
        if not isinstance(result, dict):
            result = {"error": "bad answer", "code": "SERVER_ERROR"}
        status = str(result.get("status") or "")
        http = result.get("http_status")
        if "error" not in result:
            if status == "ready":
                key = str(result.get("activation_key") or "").strip()
                if _KEY_RE.fullmatch(key):
                    return {"outcome": "ready", "key": key, "product_id": str(result.get("product_id") or "")}
                return {"outcome": "not_found"}
            if status in ("pending", "no_plan", "cancelled"):
                return {"outcome": status}
            return {"outcome": "retry_later"}
        if http == 403 and status in ("invalid", "locked"):
            return {"outcome": status}
        if http == 404:
            return {"outcome": "not_found"}
        if http == 400:
            return {"outcome": "invalid_request"}

        if attempt + 1 < _CLAIM_TRIES:
            sleep(_retry_after(result, _CLAIM_RETRY_WAIT_S))
    return {"outcome": "retry_later"}
