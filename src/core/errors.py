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
    # The server refused the prompt on content grounds, before any charge.
    PROMPT_NOT_ALLOWED = "PROMPT_NOT_ALLOWED"

    OUTPUT_DIR_INVALID = "OUTPUT_DIR_INVALID"
    DISK_FULL = "DISK_FULL"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    UNKNOWN = "UNKNOWN"


# Connectivity failures the user resolves themselves (their link, proxy, or
# firewall). Single source of truth so the pre-flight check, the inline-only
# message set, and the poll retry list all agree on what counts as "network".
NETWORK_ERROR_CODES = frozenset(
    {
        ErrorCode.NO_NETWORK.value,
        ErrorCode.DNS_ERROR.value,
        ErrorCode.SSL_ERROR.value,
        ErrorCode.TIMEOUT.value,
        ErrorCode.PROXY_ERROR.value,
        ErrorCode.CONNECTION_REFUSED.value,
    }
)

# Server-side transient failures (incident, rate limiting): the backend could
# not answer, which says NOTHING about the stored key. Auth flows must treat
# these like a network blip (keep the session, retry later), never as a key
# rejection that signs the user out. Membership also matches server-added
# extras (error_codes.transient_extra, uppercased), union-only.
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


# The server refused to run this prompt: its content is not allowed. A verdict,
# not a fault, and never retryable as sent - only a different prompt gets past
# it. The refusal happens before the charge, so no credit is ever involved.
#
# The rule that decides a refusal lives entirely on the server (nothing here
# knows or ships what is forbidden, and a client-side blocklist would just hand
# it to anyone who reads the config). All the plugin owes it is a code it
# recognises. Every plausible spelling is shipped up front so a server built
# after this release lands on one of them whatever it picks, and
# error_codes.prompt_blocked_extra adds any it does not, union-only, without a
# plugin release.
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
    """Map an error code to the pipeline stage it failed at."""
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
    # A content refusal answers the submit call itself, before any job exists.
    if normalized_code in PROMPT_BLOCKED_CODES:
        return "submit"
    return "poll"


def build_failure_props(stage: str | None, code: str | None, message: str | None) -> dict | None:
    """Telemetry props for a failure event, or None when none must be emitted.

    Guarantees every failure event ships with a stage (derived from the code
    when the pipeline stage is not explicit) and an error_code (UNKNOWN
    fallback), plus a scrubbed error_message capped at 200 chars when a
    message exists. A user cancel (GENERATION_CANCELLED) returns None:
    generation_cancelled is emitted by the cancel path, never a failure event.
    """
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
    """Stable code + human message so callers can branch without substring matches."""

    def __init__(self, code: ErrorCode, message: str = "", *, cause: Exception | None = None):
        super().__init__(message or code.value)
        self.code = code
        self.message = message or code.value
        self.__cause__ = cause

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"

    def __repr__(self) -> str:
        return f"AIEditError(code={self.code.value!r}, message={self.message!r})"
