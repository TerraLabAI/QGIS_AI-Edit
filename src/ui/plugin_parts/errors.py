from __future__ import annotations

import html

from ...core.auth.activation_manager import (
    build_utm_url,
    get_dashboard_url,
    get_server_url,
)
from ...core.config_store import (
    ServerDialSet,
    get_export_copy,
    get_export_dial,
    get_export_dial_list,
    get_export_text,
)
from ...core.errors import NETWORK_ERROR_CODES, PROMPT_BLOCKED_CODES
from ...core.i18n import tr


__all__ = [
    "_CREDIT_REASSURE_CODES",
    "_enrich_error_message",
    "_is_model_failure",
    "_is_prompt_blocked",
    "_is_safety_block",
    "_is_service_busy",
    "_localize_server_error",
    "_prompt_blocked_message",
    "_report_policy",
    "_resolve_class_label",
    "content_policy_url",
    "DASHBOARD_ERROR_URL",
    "dashboard_error_url",
    "SUBSCRIBE_ERROR_URL",
    "subscribe_error_url",
]



_MAX_ERROR_CHARS = 240
_MAX_CTA_CHARS = 120





DASHBOARD_ERROR_URL = build_utm_url("/dashboard", "dashboard_error")
SUBSCRIBE_ERROR_URL = build_utm_url("/dashboard/ai-edit", "subscribe")


def dashboard_error_url() -> str:

    return get_server_url("dashboard_error_url", DASHBOARD_ERROR_URL)


def subscribe_error_url() -> str:

    return get_server_url("subscribe_error_url", SUBSCRIBE_ERROR_URL)


def content_policy_url() -> str:






    return get_server_url("content_policy_url", "")


def _localize_server_error(error: str, code: str) -> str:

















    if not code:
        return html.escape(error or "")
    served = get_export_text(
        "error_messages", code, get_export_dial("flows.errors.max_error_chars", _MAX_ERROR_CHARS)
    )
    if served is not None:
        return html.escape(served, quote=False)
    mapping = {



        "NO_NETWORK": tr("No internet connection."),
        "DNS_ERROR": tr("Cannot reach the server."),
        "TIMEOUT": tr("The request timed out."),
        "SSL_ERROR": tr("Secure connection failed."),
        "PROXY_ERROR": tr("Proxy connection failed."),
        "CONNECTION_REFUSED": tr("Could not connect to the service."),
        "AUTH_ERROR": tr("Authentication failed. Check your activation key."),


        "NO_KEY": tr("No activation key. Enter your key to use AI Edit."),
        "INVALID_KEY": tr("Invalid activation key."),
        "KEY_REVOKED": tr("This activation key has been revoked."),
        "SUBSCRIPTION_EXPIRED": tr("Your subscription has expired."),
        "SUBSCRIPTION_INACTIVE": tr("Your subscription is inactive."),
        "FREE_TIER_EXPIRED": tr("Your free trial has ended."),
        "DEVICE_LIMIT_EXCEEDED": tr(
            "This license is already in use on the maximum number of computers."
            " Free one in your account, or wait for an inactive one to expire."
        ),
        "RATE_LIMITED": tr("Too many requests, please wait a moment."),
        "RATE_LIMITER_DOWN": tr("Service temporarily unavailable, please retry shortly."),
        "STORAGE_UNAVAILABLE": tr("Storage temporarily unavailable, please retry shortly."),
        "SIGN_FAILED": tr("Could not prepare upload, please retry shortly."),
        "UPLOAD_TOKEN_INVALID": tr("Upload session expired, please retry."),
        "UPLOAD_TOKEN_MISMATCH": tr("Upload session does not match your account."),
        "WRONG_PRODUCT": tr("This activation key is for a different product."),
        "WRONG_REQUEST": tr("Unknown or unauthorized request."),
        "AUTH_MIGRATION_REQUIRED": tr("Account migration required, please re-login from the website."),
        "NOT_READY": tr("Result not ready yet."),
        "NOT_AVAILABLE": tr("Result not available."),
        "UPSTREAM_UNAVAILABLE": tr("Result temporarily unavailable, please retry shortly."),
        "UPSTREAM_EMPTY": tr("Result temporarily unavailable, please retry shortly."),
        "PROVIDER_BAD_RESPONSE": tr("The generation service returned an unexpected response, please retry."),
        "PROVIDER_ERROR": tr("Generation failed, please try again."),
        "MISCONFIGURED": tr("Service not configured. Please contact support."),
        "SERVER_ERROR": tr("Service temporarily unavailable, please retry shortly."),
        "DB_ERROR": tr("Database error, please retry shortly."),
        "BAD_REQUEST": tr("Invalid request. Check your prompt and the selected area, then try again."),
        "BAD_INPUT": tr("Invalid input. Check your prompt and the selected area."),
        "INVALID_INPUT": tr("Invalid input. Try a different image or selection."),
        "PAYLOAD_TOO_LARGE": tr("Image too large. Draw a smaller zone or pick a lower Quality."),
        "RESOLUTION_NOT_ALLOWED": tr(
            "This Quality is not in your plan. Upgrade to Pro to use it."
        ),
        "NOT_FOUND": tr("Resource not found."),
        "NOT_SEEDED": tr("Catalog not yet available, please retry shortly."),
        "DEMO_FETCH_FAILED": tr("Could not load the demo preview."),
        "UNKNOWN_TEMPLATE": tr("Unknown template."),
    }
    localized = mapping.get(code)
    if localized is not None:
        return localized




    if code.upper() in PROMPT_BLOCKED_CODES:
        return tr("This prompt is not allowed: its content goes against our rules.")
    return html.escape(error or "")















_USER_FIXABLE_CODES = frozenset({
    "AUTH_ERROR",
    "NO_KEY", "INVALID_KEY", "KEY_REVOKED", "AUTH_LOCKED",
    "INVALID_CRS", "ANTIMERIDIAN", "POLAR", "TOO_LARGE",
    "MAP_ROTATED", "ZONE_TOO_SMALL", "PAYLOAD_TOO_LARGE",
    "BAD_REQUEST", "BAD_INPUT", "INVALID_INPUT", "RESOLUTION_NOT_ALLOWED",
    "QUOTA_EXCEEDED", "LIMIT_REACHED", "USAGE_LIMIT_REACHED",
    "MONTHLY_LIMIT_REACHED", "TRIAL_EXHAUSTED",
    "SUBSCRIPTION_EXPIRED", "SUBSCRIPTION_INACTIVE", "FREE_TIER_EXPIRED",
    "DEVICE_LIMIT_EXCEEDED",
    "GENERATION_CANCELLED",
})








_CONNECTIVITY_CODES = NETWORK_ERROR_CODES - frozenset({"TIMEOUT"})

_TRANSIENT_CODES = _CONNECTIVITY_CODES | frozenset({
    "RATE_LIMITED", "NOT_READY", "NOT_AVAILABLE",
    "UPLOAD_TOKEN_INVALID", "UPLOAD_TOKEN_MISMATCH",
})







_SERVER_FAULT_CODES = frozenset({
    "TIMEOUT", "GENERATION_TIMED_OUT",
    "SERVER_ERROR", "RATE_LIMITER_DOWN", "STORAGE_UNAVAILABLE", "SIGN_FAILED",
    "UPSTREAM_UNAVAILABLE", "UPSTREAM_EMPTY", "PROVIDER_ERROR", "PROVIDER_BAD_RESPONSE",
})






_CREDIT_REASSURE_CODES = frozenset({
    "GENERATION_FAILED", "SERVER_ERROR", "PROVIDER_ERROR", "PROVIDER_BAD_RESPONSE",
    "UPSTREAM_UNAVAILABLE", "UPSTREAM_EMPTY", "EMPTY_RESPONSE",
    "IMAGE_FORMAT_UNSUPPORTED", "STORAGE_UNAVAILABLE", "RATE_LIMITED",
    "RATE_LIMITER_DOWN", "MISCONFIGURED", "DB_ERROR", "NOT_AVAILABLE",
    "NOT_READY",
})


def _is_prompt_blocked(normalized_code: str) -> bool:







    return normalized_code in PROMPT_BLOCKED_CODES


def _prompt_blocked_message(error: str, code: str) -> str:


    parts = [
        _localize_server_error(error, code),
        get_export_copy(
            "flows.errors.not_charged", tr("You have not been charged."), escape=True
        ),
    ]
    url = content_policy_url()
    if url:
        parts.append(f'<a href="{url}">{_served_cta(code) or tr("Read our content rules")}</a>')
    return " ".join(parts)


def _report_policy(normalized_code: str) -> str:















    if _is_prompt_blocked(normalized_code):
        return "none"
    if normalized_code in _USER_FIXABLE_CODES:
        return "none"
    if normalized_code in _TRANSIENT_CODES:
        return "link"
    if normalized_code in _SERVER_FAULT_CODES:
        return "dialog"
    if normalized_code in get_export_dial_list(
        "error_policy.user_fixable_extra", (), normalize=str.upper
    ):
        return "none"
    if normalized_code in get_export_dial_list(
        "error_policy.transient_extra", (), normalize=str.upper
    ):
        return "link"
    return "dialog"





_DASHBOARD_CTA_CODES = (
    "INVALID_KEY", "KEY_REVOKED",
    "SUBSCRIPTION_EXPIRED", "SUBSCRIPTION_INACTIVE", "FREE_TIER_EXPIRED",
)


def _served_cta(code: str) -> str:


    if not code:
        return ""
    served = get_export_text(
        "error_ctas", code, get_export_dial("flows.errors.max_cta_chars", _MAX_CTA_CHARS)
    )
    return html.escape(served, quote=False) if served is not None else ""


def _with_proxy_hint(message: str, code: str) -> str:

    try:
        from ...api.network_error_classifier import network_setup_hint

        hint = network_setup_hint(code)
    except Exception:  # nosec B110
        hint = ""
    if not hint:
        return message
    sep = " " if message.endswith((".", "!", "?")) else ". "
    return f"{message}{sep}{hint}"


def network_next_step(code: str) -> str:




    code = (code or "").strip().upper()
    if code == "PROXY_ERROR":
        return tr("Check QGIS proxy settings: Settings > Options > Network") + "."
    if code == "SSL_ERROR":
        return tr(
            "Ask your IT team to allow terra-lab.ai, or import your company root "
            "certificate in Settings > Options > Authentication"
        ) + "."
    if code == "NETWORK_BLOCKED":
        return tr("Ask your IT team to allow terra-lab.ai.")
    if code not in NETWORK_ERROR_CODES:
        return ""
    return _with_proxy_hint(tr("Check your internet connection") + ".", code)


def _enrich_error_message(error: str, code: str = "") -> str:






    localized = _localize_server_error(error, code)
    cta = _served_cta(code)


    lead = localized if localized.endswith((".", "!", "?")) else f"{localized}."
    if code in _DASHBOARD_CTA_CODES:
        return f'{lead} <a href="{dashboard_error_url()}">{cta or tr("Check your dashboard")}</a>'
    if code == "TRIAL_EXHAUSTED":



        upgrade = get_server_url("upgrade_url", get_dashboard_url())
        return f'{lead} <a href="{upgrade}">{cta or tr("Subscribe")}</a>'
    if code == "DEVICE_LIMIT_EXCEEDED":
        return f'{localized} <a href="{dashboard_error_url()}">{cta or tr("Manage your computers")}</a>'
    if code == "PROXY_ERROR":
        return f"{lead} {cta or tr('Check QGIS proxy settings: Settings > Options > Network')}"
    if code == "SSL_ERROR":
        ssl_hint = cta or tr(
            "Ask your IT team to allow terra-lab.ai, or import your company root "
            "certificate in Settings > Options > Authentication"
        )
        return f"{lead} {ssl_hint}"
    if code in ("DNS_ERROR", "NO_NETWORK"):
        return _with_proxy_hint(f"{lead} {cta or tr('Check your internet connection')}", code)
    if code == "TIMEOUT":
        message = f"{lead} {cta or tr('Try again, or check your internet speed')}"



        qgis_step = tr("Raise the timeout in Settings > Options > Network.")
        if qgis_step in (error or ""):
            sep = " " if message.endswith((".", "!", "?")) else ". "
            message = f"{message}{sep}{qgis_step}"
        return _with_proxy_hint(message, code)
    if code == "CONNECTION_REFUSED":
        return _with_proxy_hint(
            f"{lead} {cta or tr('The service may be temporarily unavailable')}", code
        )
    if code == "AUTH_ERROR":
        return f'{lead} <a href="{dashboard_error_url()}">{cta or tr("Check your dashboard")}</a>'


    if cta:
        return f"{lead} {cta}"
    return localized







_MODEL_FAILURE_HINTS = (
    "no image",
    "no_image",
    "finish_reason",
    "no candidates",
    "block",
    "safety",
    "couldn't complete",
    "could not complete",
    "rephrasing your prompt",
    "no credit was charged",




    "content_policy",
    "content policy",
    "content checker",
    "flagged",







    "no_media_generated",
    "did not generate",
    "invalid_request",
    "could not generate",
    "cannot be processed",
)

_NON_MODEL_FAILURE_CODES = NETWORK_ERROR_CODES | frozenset({
    "QUOTA_EXCEEDED",
    "LIMIT_REACHED",
    "USAGE_LIMIT_REACHED",
    "MONTHLY_LIMIT_REACHED",
    "TRIAL_EXHAUSTED",
    "NO_KEY",
    "INVALID_KEY",
    "KEY_REVOKED",
    "AUTH_LOCKED",
    "SUBSCRIPTION_EXPIRED",
    "SUBSCRIPTION_INACTIVE",
    "FREE_TIER_EXPIRED",
    "AUTH_ERROR",
    "GENERATION_CANCELLED",
})







_SAFETY_BLOCK_HINTS = (
    "block",
    "safety",
    "content_policy",
    "content policy",
    "content checker",
    "flagged",
)


def _is_safety_block(message: str) -> bool:


    text = (message or "").lower()
    hints = get_export_dial_list(
        "error_hints.safety_block", _SAFETY_BLOCK_HINTS, normalize=str.lower
    )
    return any(needle in text for needle in hints)


def _is_model_failure(message: str, normalized_code: str) -> bool:



    if normalized_code in _NON_MODEL_FAILURE_CODES:
        return False



    if _is_prompt_blocked(normalized_code):
        return False
    text = (message or "").lower()



    hints = get_export_dial_list(
        "error_hints.model_failure", _MODEL_FAILURE_HINTS, normalize=str.lower
    )
    return any(needle in text for needle in hints)





_BUSY_HINTS = (
    "resource exhausted",
    "resource_exhausted",
    "too many requests",
    "rate limit",
    "rate-limit",
    "service is busy",
    "busy right now",
    "try again in a moment",
)






_BUSY_CODES = ServerDialSet(
    "error_codes.busy_extra",
    {"RATE_LIMITED", "RESOURCE_EXHAUSTED", "PROVIDER_BUSY", "SERVICE_BUSY"},
    normalize=str.upper,
)


def _is_service_busy(message: str, normalized_code: str) -> bool:
    if normalized_code in _BUSY_CODES:
        return True
    text = (message or "").lower()

    hints = get_export_dial_list("error_hints.busy", _BUSY_HINTS, normalize=str.lower)
    return any(needle in text for needle in hints)


def _resolve_class_label(
    vector_color: str | None, vector_classes: list[dict] | None
) -> str:





    if not vector_color or not isinstance(vector_classes, list):
        return ""
    target = vector_color.upper().lstrip("#")
    for entry in vector_classes:
        if not isinstance(entry, dict):
            continue
        color = (entry.get("color") or "").upper().lstrip("#")
        if color and color == target:
            label = entry.get("label")
            if isinstance(label, str):
                return label.strip()
    return ""
