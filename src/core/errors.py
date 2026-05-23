from __future__ import annotations

from enum import Enum

from .config_store import ServerDialSet
from .log_scrub import scrub_user_paths


class ErrorCode(str, Enum):
    NO_NETWORK = "NO_NETWORK"
    DNS_ERROR = "DNS_ERROR"
    SSL_ERROR = "SSL_ERROR"
    TIMEOUT = "TIMEOUT"
    PROXY_ERROR = "PROXY_ERROR"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"

    NETWORK_BLOCKED = "NETWORK_BLOCKED"

    NO_KEY = "NO_KEY"
    INVALID_KEY = "INVALID_KEY"
    KEY_REVOKED = "KEY_REVOKED"
    AUTH_LOCKED = "AUTH_LOCKED"

    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    TRIAL_EXHAUSTED = "TRIAL_EXHAUSTED"
    SUBSCRIPTION_EXPIRED = "SUBSCRIPTION_EXPIRED"
    DEVICE_LIMIT_EXCEEDED = "DEVICE_LIMIT_EXCEEDED"

    GENERATION_FAILED = "GENERATION_FAILED"
    GENERATION_CANCELLED = "GENERATION_CANCELLED"
    GENERATION_TIMED_OUT = "GENERATION_TIMED_OUT"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    WRITE_ERROR = "WRITE_ERROR"
    IMAGE_FORMAT_UNSUPPORTED = "IMAGE_FORMAT_UNSUPPORTED"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"

    INVALID_CRS = "INVALID_CRS"
    ANTIMERIDIAN = "ANTIMERIDIAN"
    POLAR = "POLAR"
    TOO_LARGE = "TOO_LARGE"
    MAP_ROTATED = "MAP_ROTATED"
    ZONE_TOO_SMALL = "ZONE_TOO_SMALL"

    NO_PIXELS_MATCHED = "NO_PIXELS_MATCHED"
    RASTER_TOO_LARGE = "RASTER_TOO_LARGE"
    INVALID_RASTER = "INVALID_RASTER"
    VECTORIZE_INTERNAL_ERROR = "VECTORIZE_INTERNAL_ERROR"

    SERVER_ERROR = "SERVER_ERROR"
    BAD_REQUEST = "BAD_REQUEST"

    PROMPT_NOT_ALLOWED = "PROMPT_NOT_ALLOWED"

    OUTPUT_DIR_INVALID = "OUTPUT_DIR_INVALID"
    DISK_FULL = "DISK_FULL"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    UNKNOWN = "UNKNOWN"





NETWORK_ERROR_CODES = frozenset(
    {
        ErrorCode.NO_NETWORK.value,
        ErrorCode.DNS_ERROR.value,
        ErrorCode.SSL_ERROR.value,
        ErrorCode.TIMEOUT.value,
        ErrorCode.PROXY_ERROR.value,
        ErrorCode.CONNECTION_REFUSED.value,
        ErrorCode.NETWORK_BLOCKED.value,
    }
)






TRANSIENT_SERVER_ERROR_CODES = ServerDialSet(
    "error_codes.transient_extra",
    {
        ErrorCode.SERVER_ERROR.value,
        "RATE_LIMITED",
        "RATE_LIMITER_DOWN",
        "UPSTREAM_UNAVAILABLE",
    },
    normalize=str.upper,
)













PROMPT_BLOCKED_CODES = ServerDialSet(
    "error_codes.prompt_blocked_extra",
    {
        ErrorCode.PROMPT_NOT_ALLOWED.value,
        "PROMPT_BLOCKED",
        "PROMPT_REJECTED",
        "PROMPT_FORBIDDEN",
        "CONTENT_BLOCKED",
        "CONTENT_NOT_ALLOWED",
        "CONTENT_POLICY",
        "CONTENT_POLICY_VIOLATION",
        "POLICY_VIOLATION",
        "FORBIDDEN_PROMPT",
    },
    normalize=str.upper,
)


def failure_stage(normalized_code: str) -> str:

    if normalized_code == ErrorCode.DOWNLOAD_FAILED.value:
        return "download"
    if normalized_code == ErrorCode.WRITE_ERROR.value:
        return "write"
    if normalized_code in {
        ErrorCode.NO_NETWORK.value, ErrorCode.DNS_ERROR.value,
        ErrorCode.SSL_ERROR.value, ErrorCode.TIMEOUT.value,
        ErrorCode.PROXY_ERROR.value, ErrorCode.CONNECTION_REFUSED.value,
        ErrorCode.TOO_LARGE.value, ErrorCode.BAD_REQUEST.value,
    }:
        return "submit"

    if normalized_code in PROMPT_BLOCKED_CODES:
        return "submit"
    return "poll"


def build_failure_props(stage: str | None, code: str | None, message: str | None) -> dict | None:








    effective_code = (code or "").strip() or ErrorCode.UNKNOWN.value
    normalized = effective_code.upper()
    if normalized == ErrorCode.GENERATION_CANCELLED.value:
        return None
    props = {
        "error_code": effective_code,
        "stage": (stage or "").strip() or failure_stage(normalized),
    }
    msg = (message or "").strip()
    if msg:
        props["error_message"] = scrub_user_paths(msg)[:200]
    return props


class AIEditError(Exception):


    def __init__(self, code: ErrorCode, message: str = "", *, cause: Exception | None = None):
        super().__init__(message or code.value)
        self.code = code
        self.message = message or code.value
        self.__cause__ = cause

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"

    def __repr__(self) -> str:
        return f"AIEditError(code={self.code.value!r}, message={self.message!r})"
