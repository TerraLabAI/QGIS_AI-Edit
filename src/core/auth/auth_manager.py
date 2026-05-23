from __future__ import annotations

import threading
import time

from ..config_store import get_export_copy, get_export_dial
from ..errors import NETWORK_ERROR_CODES, ErrorCode
from ..i18n import tr






_PREFLIGHT_TIMEOUT_MS = 12_000



_CREDITS_TIMEOUT_MS = 8_000





_USAGE_CACHE_TTL_S = 60.0


class AuthManager:


    def __init__(self, client):
        self._client = client
        self._activation_key = ""
        self._usage_cache: dict | None = None
        self._usage_cache_monotonic = 0.0
        self._usage_lock = threading.RLock()
        self._key_revision = 0

    def set_activation_key(self, key: str):
        normalized = key.strip() if isinstance(key, str) else ""
        with self._usage_lock:
            self._activation_key = normalized
            self._key_revision += 1
            self._usage_cache = None
            self._usage_cache_monotonic = 0.0

    def _store_usage(self, usage, revision: int | None = None) -> None:
        if isinstance(usage, dict) and "error" not in usage:
            with self._usage_lock:
                if revision is not None and revision != self._key_revision:
                    return
                self._usage_cache = dict(usage)
                self._usage_cache_monotonic = time.monotonic()

    def seed_usage(self, usage: dict) -> None:


        self._store_usage(usage)

    def _fresh_cached_usage(self) -> dict | None:
        with self._usage_lock:
            ttl_s = get_export_dial("auth.usage_cache_ttl_s", _USAGE_CACHE_TTL_S)
            cache_fresh = all((
                self._usage_cache is not None,
                time.monotonic() - self._usage_cache_monotonic < ttl_s,
            ))
            if cache_fresh:
                return dict(self._usage_cache)
        return None

    def get_activation_key(self) -> str:
        return self._activation_key

    def has_activation_key(self) -> bool:
        return bool(self._activation_key)

    def get_auth_header(self) -> dict:

        with self._usage_lock:
            key = self._activation_key
        if not key:
            return {}
        headers = {
            "Authorization": f"Bearer {key}",
            "X-Product-ID": "ai-edit",
        }


        try:
            from ..device_id import get_device_hash, get_device_platform

            headers["X-Device-Hash"] = get_device_hash()
            platform = get_device_platform()
            if platform:
                headers["X-Device-Platform"] = platform
        except Exception:  # nosec B110
            pass
        return headers

    def check_can_generate(self) -> tuple[bool, str, str]:





        if not self._activation_key:
            return (
                False,
                get_export_copy(
                    "pipeline.auth_manager.no_key",
                    tr("No activation key. Enter your key to use AI Edit."),
                ),
                ErrorCode.NO_KEY.value,
            )

        with self._usage_lock:
            revision = self._key_revision
            auth = self.get_auth_header()
            usage = self._fresh_cached_usage()
        if usage is None:
            try:
                usage = self._client.get_usage(
                    auth=auth,
                    timeout_ms=get_export_dial(
                        "auth.preflight_timeout_ms", _PREFLIGHT_TIMEOUT_MS
                    ),
                )
            except Exception:
                return (
                    False,
                    get_export_copy(
                        "pipeline.auth_manager.no_network",
                        tr("No internet connection. Check your network and try again."),
                    ),
                    ErrorCode.NO_NETWORK.value,
                )
            self._store_usage(usage, revision)

        with self._usage_lock:
            if revision != self._key_revision:
                return False, tr("Your account changed. Please try again."), ErrorCode.NO_KEY.value
        if not isinstance(usage, dict):

            return True, "usage unavailable", ""
        if "error" in usage:
            code = str(usage.get("code") or "").strip().upper()
            if code in NETWORK_ERROR_CODES:


                return (
                    False,
                    get_export_copy(
                        "pipeline.auth_manager.no_network",
                        tr("No internet connection. Check your network and try again."),
                    ),
                    code,
                )
            if code == "INVALID_KEY":
                return (
                    False,
                    get_export_copy("pipeline.auth_manager.invalid_key", tr("Invalid activation key.")),
                    ErrorCode.INVALID_KEY.value,
                )
            if code == "SUBSCRIPTION_INACTIVE":
                return (
                    False,
                    get_export_copy("pipeline.auth_manager.subscription_expired", tr("Subscription expired.")),
                    ErrorCode.SUBSCRIPTION_EXPIRED.value,
                )
            if code == "NO_AUTH":
                return (
                    False,
                    get_export_copy(
                        "pipeline.auth_manager.no_key",
                        tr("No activation key. Enter your key to use AI Edit."),
                    ),
                    ErrorCode.NO_KEY.value,
                )
            unknown_error = get_export_copy("pipeline.auth_manager.unknown_error", tr("Unknown error"))
            return False, str(usage.get("error") or unknown_error), code

        used = usage.get("images_used")
        limit = usage.get("images_limit")




        if type(used) is not int or type(limit) is not int or used < 0 or limit < 0:
            return True, "usage unavailable", ""

        if used >= limit:
            is_free = usage.get("is_free_tier", False)
            if is_free:

                return (
                    False,
                    tr("You've used this month's {limit} free credits. They renew at your next monthly reset.").format(
                        limit=limit,
                    ),
                    ErrorCode.TRIAL_EXHAUSTED.value,
                )
            return (
                False,
                tr("Monthly limit reached ({used}/{limit}).").format(used=used, limit=limit),
                ErrorCode.QUOTA_EXCEEDED.value,
            )

        return True, f"{used}/{limit} images used", ""

    def get_usage_info(self) -> dict:



        if not self._activation_key:
            return {
                "error": get_export_copy("pipeline.auth_manager.no_key_short", tr("No activation key")),
                "code": ErrorCode.NO_KEY.value,
            }
        with self._usage_lock:
            revision = self._key_revision
            auth = self.get_auth_header()
        try:
            usage = self._client.get_usage(
                auth=auth,
                timeout_ms=get_export_dial("auth.credits_timeout_ms", _CREDITS_TIMEOUT_MS),
            )
        except Exception:
            return {
                "error": get_export_copy("pipeline.auth_manager.connection_error", tr("Connection error")),
                "code": ErrorCode.NO_NETWORK.value,
            }
        with self._usage_lock:
            if revision != self._key_revision:
                return {"error": tr("Your account changed. Please try again."), "code": ErrorCode.NO_KEY.value}
        if not isinstance(usage, dict):
            return {
                "error": tr("Unexpected response from the server. Please try again."),
                "code": ErrorCode.UNKNOWN.value,
            }
        self._store_usage(usage, revision)
        return usage
