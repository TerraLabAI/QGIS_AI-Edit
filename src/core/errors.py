from __future__ import annotations

from enum import Enum


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

    SERVER_ERROR = "SERVER_ERROR"
    BAD_REQUEST = "BAD_REQUEST"

    OUTPUT_DIR_INVALID = "OUTPUT_DIR_INVALID"
    DISK_FULL = "DISK_FULL"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    UNKNOWN = "UNKNOWN"


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
