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

    LAYER_IN_EDIT_MODE = "LAYER_IN_EDIT_MODE"

    SERVER_ERROR = "SERVER_ERROR"



    RESULT_UNCONFIRMED = "RESULT_UNCONFIRMED"
    BAD_REQUEST = "BAD_REQUEST"

    PROMPT_NOT_ALLOWED = "PROMPT_NOT_ALLOWED"

    OUTPUT_DIR_INVALID = "OUTPUT_DIR_INVALID"
    DISK_FULL = "DISK_FULL"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    UNKNOWN = "UNKNOWN"


class ErrorCodeTrait(str, Enum):





    NETWORK = "NETWORK"

    QUOTA = "QUOTA"

    SUBMIT_STAGE = "SUBMIT_STAGE"

    SUBMIT_RETRY = "SUBMIT_RETRY"


    SUBMIT_UNCONFIRMED = "SUBMIT_UNCONFIRMED"

    POLL_RETRY = "POLL_RETRY"


    REPORT_NONE = "REPORT_NONE"
    REPORT_LINK = "REPORT_LINK"
    REPORT_DIALOG = "REPORT_DIALOG"

    CREDIT_NOTE = "CREDIT_NOTE"

    NOT_MODEL_FAILURE = "NOT_MODEL_FAILURE"

    BUSY = "BUSY"

    ACCOUNT_LINK = "ACCOUNT_LINK"


    SERVER_INTERNAL = "SERVER_INTERNAL"


    SIGN_IN_REFUSED = "SIGN_IN_REFUSED"


    PLAN_STOPPED = "PLAN_STOPPED"


def _trait_row(*names: str) -> frozenset[ErrorCodeTrait]:

    return frozenset(ErrorCodeTrait[name] for name in names)




_NETWORK_FAMILY = _trait_row(
    "NETWORK", "SUBMIT_RETRY", "POLL_RETRY", "SUBMIT_STAGE", "NOT_MODEL_FAILURE"
)



_NETWORK_BLOCKED_WITHHELD = _trait_row("SUBMIT_STAGE")
_QUOTA_FAMILY = _trait_row("QUOTA", "REPORT_NONE", "NOT_MODEL_FAILURE")
_GATEWAY_FAMILY = _trait_row("SUBMIT_RETRY", "SUBMIT_UNCONFIRMED", "POLL_RETRY")








ERROR_CODE_TRAITS: dict[str, frozenset[ErrorCodeTrait]] = {

    "NO_NETWORK": _NETWORK_FAMILY | _trait_row("REPORT_LINK"),
    "DNS_ERROR": _NETWORK_FAMILY | _trait_row("REPORT_LINK"),
    "SSL_ERROR": _NETWORK_FAMILY | _trait_row("REPORT_LINK"),
    "PROXY_ERROR": _NETWORK_FAMILY | _trait_row("REPORT_LINK"),
    "CONNECTION_REFUSED": _NETWORK_FAMILY | _trait_row("REPORT_LINK"),


    "TIMEOUT": _NETWORK_FAMILY | _trait_row("SUBMIT_UNCONFIRMED", "REPORT_DIALOG"),
    "NETWORK_BLOCKED": (_NETWORK_FAMILY - _NETWORK_BLOCKED_WITHHELD) | _trait_row("REPORT_LINK"),

    "QUOTA_EXCEEDED": _QUOTA_FAMILY,
    "LIMIT_REACHED": _QUOTA_FAMILY,
    "USAGE_LIMIT_REACHED": _QUOTA_FAMILY,
    "MONTHLY_LIMIT_REACHED": _QUOTA_FAMILY,

    "AUTH_ERROR": _trait_row("REPORT_NONE", "NOT_MODEL_FAILURE"),
    "NO_KEY": _trait_row("REPORT_NONE", "NOT_MODEL_FAILURE"),
    "INVALID_KEY": _trait_row("REPORT_NONE", "NOT_MODEL_FAILURE", "ACCOUNT_LINK", "SIGN_IN_REFUSED"),
    "KEY_REVOKED": _trait_row("REPORT_NONE", "NOT_MODEL_FAILURE", "ACCOUNT_LINK", "SIGN_IN_REFUSED"),

    "NO_AUTH": _trait_row("SIGN_IN_REFUSED"),
    "AUTH_LOCKED": _trait_row("REPORT_NONE", "NOT_MODEL_FAILURE"),
    "TRIAL_EXHAUSTED": _trait_row("REPORT_NONE", "NOT_MODEL_FAILURE"),


    "SUBSCRIPTION_EXPIRED": _trait_row("REPORT_NONE", "NOT_MODEL_FAILURE", "ACCOUNT_LINK", "PLAN_STOPPED"),
    "SUBSCRIPTION_INACTIVE": _trait_row("REPORT_NONE", "NOT_MODEL_FAILURE", "ACCOUNT_LINK", "PLAN_STOPPED"),
    "FREE_TIER_EXPIRED": _trait_row("REPORT_NONE", "NOT_MODEL_FAILURE", "ACCOUNT_LINK"),
    "DEVICE_LIMIT_EXCEEDED": _trait_row("REPORT_NONE"),
    "GENERATION_CANCELLED": _trait_row("REPORT_NONE", "NOT_MODEL_FAILURE"),
    "REFERENCE_LIMIT": _trait_row("REPORT_NONE"),
    "WRONG_PRODUCT": _trait_row("REPORT_NONE"),
    "NO_ACCOUNT": _trait_row("REPORT_NONE"),
    "AUTH_MIGRATION_REQUIRED": _trait_row("REPORT_NONE"),
    "RESOLUTION_NOT_ALLOWED": _trait_row("REPORT_NONE"),

    "INVALID_CRS": _trait_row("REPORT_NONE"),
    "ANTIMERIDIAN": _trait_row("REPORT_NONE"),
    "POLAR": _trait_row("REPORT_NONE"),
    "MAP_ROTATED": _trait_row("REPORT_NONE"),
    "ZONE_TOO_SMALL": _trait_row("REPORT_NONE"),
    "TOO_LARGE": _trait_row("REPORT_NONE", "SUBMIT_STAGE"),
    "BAD_REQUEST": _trait_row("REPORT_NONE", "SUBMIT_STAGE"),
    "PAYLOAD_TOO_LARGE": _trait_row("REPORT_NONE"),
    "BAD_INPUT": _trait_row("REPORT_NONE"),
    "INVALID_INPUT": _trait_row("REPORT_NONE"),

    "RATE_LIMITED": _trait_row("REPORT_LINK", "POLL_RETRY", "CREDIT_NOTE", "BUSY"),
    "NOT_READY": _trait_row("REPORT_LINK", "CREDIT_NOTE"),
    "NOT_AVAILABLE": _trait_row("REPORT_LINK", "CREDIT_NOTE"),
    "UPLOAD_TOKEN_INVALID": _trait_row("REPORT_LINK"),
    "UPLOAD_TOKEN_MISMATCH": _trait_row("REPORT_LINK"),

    "SERVER_ERROR": _trait_row("SUBMIT_UNCONFIRMED", "POLL_RETRY", "REPORT_DIALOG", "CREDIT_NOTE"),
    "UPSTREAM_UNAVAILABLE": _GATEWAY_FAMILY
    | _trait_row("REPORT_DIALOG", "CREDIT_NOTE", "SERVER_INTERNAL"),
    "RATE_LIMITER_DOWN": _trait_row("POLL_RETRY", "REPORT_DIALOG", "CREDIT_NOTE", "SERVER_INTERNAL"),
    "STORAGE_UNAVAILABLE": _trait_row("REPORT_DIALOG", "CREDIT_NOTE", "SERVER_INTERNAL"),
    "UPSTREAM_EMPTY": _trait_row("REPORT_DIALOG", "CREDIT_NOTE", "SERVER_INTERNAL"),
    "PROVIDER_BAD_RESPONSE": _trait_row("REPORT_DIALOG", "CREDIT_NOTE", "SERVER_INTERNAL"),
    "PROVIDER_ERROR": _trait_row("REPORT_DIALOG", "CREDIT_NOTE"),
    "SIGN_FAILED": _trait_row("REPORT_DIALOG", "SERVER_INTERNAL"),
    "GENERATION_TIMED_OUT": _trait_row("REPORT_DIALOG"),
    "RESULT_UNCONFIRMED": _trait_row("REPORT_DIALOG"),

    "BAD_GATEWAY": _GATEWAY_FAMILY,
    "SERVICE_UNAVAILABLE": _GATEWAY_FAMILY,
    "GATEWAY_TIMEOUT": _GATEWAY_FAMILY,

    "GENERATION_FAILED": _trait_row("CREDIT_NOTE"),
    "EMPTY_RESPONSE": _trait_row("CREDIT_NOTE"),
    "IMAGE_FORMAT_UNSUPPORTED": _trait_row("CREDIT_NOTE"),
    "MISCONFIGURED": _trait_row("CREDIT_NOTE", "SERVER_INTERNAL"),
    "DB_ERROR": _trait_row("CREDIT_NOTE", "SERVER_INTERNAL"),

    "JOB_INSERT_FAILED": _trait_row("SERVER_INTERNAL"),
    "NOT_CONFIGURED": _trait_row("SERVER_INTERNAL"),
    "REFUND_CHECK_FAILED": _trait_row("SERVER_INTERNAL"),
    "CANCEL_UPSTREAM_FAILED": _trait_row("SERVER_INTERNAL"),
    "WRONG_STATUS": _trait_row("SERVER_INTERNAL"),
    "NOT_SEEDED": _trait_row("SERVER_INTERNAL"),

    "RESOURCE_EXHAUSTED": _trait_row("BUSY"),
    "PROVIDER_BUSY": _trait_row("BUSY"),
    "SERVICE_BUSY": _trait_row("BUSY"),
}


_REPORT_TIERS = frozenset(
    {ErrorCodeTrait.REPORT_NONE, ErrorCodeTrait.REPORT_LINK, ErrorCodeTrait.REPORT_DIALOG}
)


for _code, _traits in ERROR_CODE_TRAITS.items():
    if len(_traits & _REPORT_TIERS) > 1:
        raise ValueError(f"{_code} carries more than one report tier")


def codes_with_trait(trait: ErrorCodeTrait) -> frozenset[str]:




    return frozenset(code for code, traits in ERROR_CODE_TRAITS.items() if trait in traits)





NETWORK_ERROR_CODES = codes_with_trait(ErrorCodeTrait.NETWORK)



QUOTA_ERROR_CODES = codes_with_trait(ErrorCodeTrait.QUOTA)



SIGN_IN_REFUSED_CODES = codes_with_trait(ErrorCodeTrait.SIGN_IN_REFUSED)


PLAN_STOPPED_CODES = codes_with_trait(ErrorCodeTrait.PLAN_STOPPED)


_SUBMIT_STAGE_CODES = codes_with_trait(ErrorCodeTrait.SUBMIT_STAGE)






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
    if normalized_code in _SUBMIT_STAGE_CODES:
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
