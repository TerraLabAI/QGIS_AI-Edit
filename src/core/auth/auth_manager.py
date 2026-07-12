from __future__ import annotations

import threading
import time

from ..errors import NETWORK_ERROR_CODES, ErrorCode
from ..i18n import tr

# Pre-generation usage check budget. A working link answers in well under a
# second, so a short ceiling lets an offline/stalled connection surface the
# network error fast instead of making the user wait the full API timeout.
_PREFLIGHT_TIMEOUT_MS = 12_000

# Credits refresh runs as a hidden background task; keep its ceiling short so an
# unstable connection does not leave it hanging for the full 30s API timeout.
_CREDITS_TIMEOUT_MS = 8_000

# Credits are refetched on activation and after every generation, so a recent
# snapshot is almost always available when the user clicks Generate. Reusing it
# skips one network round-trip per generation; the server re-checks quota on
# submit anyway, so a stale "allowed" can never produce a free image.
_USAGE_CACHE_TTL_S = 60.0


class AuthManager:
    """Manages authentication state for paid AI Edit plugin."""

    def __init__(self, client):
        self._client = client
        self._activation_key = ""
        self._usage_cache: dict | None = None
        self._usage_cache_monotonic = 0.0
        self._usage_lock = threading.Lock()

    def set_activation_key(self, key: str):
        self._activation_key = key.strip() if key else ""
        with self._usage_lock:
            self._usage_cache = None

    def _store_usage(self, usage) -> None:
        if isinstance(usage, dict) and "error" not in usage:
            with self._usage_lock:
                self._usage_cache = dict(usage)
                self._usage_cache_monotonic = time.monotonic()

    def seed_usage(self, usage: dict) -> None:
        """Feed an externally fetched /usage payload into the snapshot the
        pre-generation check reuses (e.g. the key-validation response)."""
        self._store_usage(usage)

    def _fresh_cached_usage(self) -> dict | None:
        with self._usage_lock:
            cache_fresh = all((
                self._usage_cache is not None,
                time.monotonic() - self._usage_cache_monotonic < _USAGE_CACHE_TTL_S,
            ))
            if cache_fresh:
                return dict(self._usage_cache)
        return None

    def get_activation_key(self) -> str:
        return self._activation_key

    def has_activation_key(self) -> bool:
        return bool(self._activation_key)

    def get_auth_header(self) -> dict:
        """Build auth headers. Requires activation key."""
        if not self._activation_key:
            return {}
        headers = {
            "Authorization": f"Bearer {self._activation_key}",
            "X-Product-ID": "ai-edit",
        }
        # Anonymous per-machine hash so the server can apply the device limit.
        # Best-effort: a hash failure must never strip auth.
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
        """Check if user can generate.

        Returns:
            (allowed: bool, reason: str, error_code: str)
        """
        if not self._activation_key:
            return (
                False,
                tr("No activation key. Enter your key to use AI Edit."),
                ErrorCode.NO_KEY.value,
            )

        usage = self._fresh_cached_usage()
        if usage is None:
            auth = self.get_auth_header()
            try:
                usage = self._client.get_usage(auth=auth, timeout_ms=_PREFLIGHT_TIMEOUT_MS)
            except Exception:
                return (
                    False,
                    tr("No internet connection. Check your network and try again."),
                    ErrorCode.NO_NETWORK.value,
                )
            self._store_usage(usage)

        if "error" in usage:
            code = usage.get("code", "")
            if code in NETWORK_ERROR_CODES:
                # Keep the specific network code so the UI shows the matching hint
                # and stays inline (no bug-report dialog for a connectivity blip).
                return (
                    False,
                    tr("No internet connection. Check your network and try again."),
                    code,
                )
            if code == "INVALID_KEY":
                return False, tr("Invalid activation key."), ErrorCode.INVALID_KEY.value
            if code == "SUBSCRIPTION_INACTIVE":
                return False, tr("Subscription expired."), ErrorCode.SUBSCRIPTION_EXPIRED.value
            if code == "NO_AUTH":
                return (
                    False,
                    tr("No activation key. Enter your key to use AI Edit."),
                    ErrorCode.NO_KEY.value,
                )
            return False, usage.get("error", tr("Unknown error")), code

        used = usage.get("images_used")
        limit = usage.get("images_limit")

        # A payload missing either field must never block: defaulting a missing
        # limit to 0 read as "quota exhausted" for a valid paid user. The server
        # re-checks quota on submit anyway, so allow and let it arbitrate.
        if not isinstance(used, int) or not isinstance(limit, int):
            return True, "usage unavailable", ""

        if used >= limit:
            is_free = usage.get("is_free_tier", False)
            if is_free:
                # Free credits renew monthly on the 1st (UTC, server-side).
                return (
                    False,
                    tr("You've used this month's {limit} free credits. They renew on the 1st.").format(limit=limit),
                    ErrorCode.TRIAL_EXHAUSTED.value,
                )
            return (
                False,
                tr("Monthly limit reached ({used}/{limit}).").format(used=used, limit=limit),
                ErrorCode.QUOTA_EXCEEDED.value,
            )

        return True, f"{used}/{limit} images used", ""

    def get_usage_info(self) -> dict:
        """Fetch current usage info from backend. Always hits the network (the
        credit display must be authoritative) and refreshes the snapshot the
        pre-generation check reuses."""
        if not self._activation_key:
            return {"error": tr("No activation key"), "code": ErrorCode.NO_KEY.value}
        try:
            usage = self._client.get_usage(
                auth=self.get_auth_header(), timeout_ms=_CREDITS_TIMEOUT_MS
            )
        except Exception:
            return {"error": tr("Connection error"), "code": ErrorCode.NO_NETWORK.value}
        self._store_usage(usage)
        return usage
