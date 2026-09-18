"""Pure response validation shared by the blocking API and transfer paths."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit


def json_body(value) -> bytes:
    """Encode compact, standards-compliant UTF-8 without escaping human text."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def valid_http_url(value: str) -> bool:
    """Keep local-file schemes, credentials and control characters out of transfers."""
    if not isinstance(value, str) or not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme.lower() in {"http", "https"} and parsed.hostname
            and parsed.username is None and parsed.password is None
            and not parsed.fragment and (parsed.port is None or parsed.port > 0)
        )
    except ValueError:
        return False


def transfer_timeout(value, fallback: int) -> int:
    """Qt expects a positive signed 32-bit millisecond count; zero disables it."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    try:
        return int(value) if math.isfinite(value) and 1 <= value <= 2_147_483_647 else fallback
    except (OverflowError, ValueError):
        return fallback


def response_object(raw: bytes) -> dict | None:
    """Only a UTF-8 JSON object is an API response; reject scalars and NaN."""
    def invalid_constant(_value):
        raise ValueError("Non-finite JSON value")

    try:
        value = json.loads(raw.decode("utf-8-sig"), parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) and _finite_json(value) else None


def _finite_json(value) -> bool:
    """Reject exponent overflow too, which parse_constant does not see."""
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite_json(key) and _finite_json(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_finite_json(item) for item in value)
    return True


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def retry_after_seconds(value, now: datetime | None = None) -> float | None:
    """Accept the delta-seconds and HTTP-date forms of Retry-After."""
    if not value:
        return None
    try:
        raw = bytes(value).decode("ascii").strip() if not isinstance(value, str) else value.strip()
        seconds = float(raw)
    except (ValueError, TypeError, UnicodeError):
        try:
            when = parsedate_to_datetime(raw)
            if when.tzinfo is None:
                return None
            seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
        except (ValueError, TypeError, OverflowError, UnboundLocalError):
            return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def http_failure(parsed: dict | None, status: int, retry_after=None) -> dict:
    """Keep additive server details while guaranteeing an actionable error shape."""
    result = dict(parsed) if isinstance(parsed, dict) else {}
    if not isinstance(result.get("error"), str) or not result["error"].strip():
        result["error"] = f"Server error (HTTP {status})"
    code = result.get("code")
    if not isinstance(code, str) or not code.strip():
        result["code"] = {
            408: "TIMEOUT", 413: "PAYLOAD_TOO_LARGE", 429: "RATE_LIMITED",
        }.get(status, "SERVER_ERROR")
    result["http_status"] = status
    hint = retry_after_seconds(retry_after)
    # Body values are the application's more specific contract. A malformed
    # one must not suppress the standard HTTP hint.
    body_hint = result.get("retry_after")
    if not _finite_number(body_hint) or body_hint < 0:
        body_hint = None
    if body_hint is None and hint is not None:
        result["retry_after"] = hint
    return result


def valid_headers(headers) -> bool:
    """Validate header types and line boundaries before passing them to Qt."""
    if not isinstance(headers, dict):
        return False
    separators = set('()<>@,;:\\"/[]?={} \t')
    for key, value in headers.items():
        if not isinstance(key, str) or not key or not isinstance(value, str):
            return False
        if any(ord(c) < 33 or ord(c) > 126 or c in separators for c in key):
            return False
        if any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in value):
            return False
    return True
