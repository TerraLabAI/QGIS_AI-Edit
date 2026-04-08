from typing import Tuple


class AuthManager:
    """Manages authentication state for paid AI Edit plugin."""

    def __init__(self, client):
        self._client = client
        self._activation_key = ""

    def set_activation_key(self, key: str):
        self._activation_key = key.strip() if key else ""

    def get_activation_key(self) -> str:
        return self._activation_key

    def has_activation_key(self) -> bool:
        return bool(self._activation_key)

    def get_auth_header(self) -> dict:
        """Build auth headers. Requires activation key."""
        if not self._activation_key:
            return {}
        return {
            "Authorization": f"Bearer {self._activation_key}",
            "X-Product-ID": "ai-edit",
        }

    def check_can_generate(self) -> Tuple[bool, str, str]:
        """Check if user can generate.

        Returns:
            (allowed: bool, reason: str, error_code: str)
        """
        if not self._activation_key:
            return False, "No activation key. Enter your key to use AI Edit.", "NO_KEY"

        auth = self.get_auth_header()
        try:
            usage = self._client.get_usage(auth=auth)
        except Exception:
            return False, "Connection error. Check your internet connection.", "CONNECTION_ERROR"

        if "error" in usage:
            code = usage.get("code", "")
            if code == "INVALID_KEY":
                return False, "Invalid activation key.", "INVALID_KEY"
            if code == "SUBSCRIPTION_INACTIVE":
                return False, "Subscription expired.", "SUBSCRIPTION_INACTIVE"
            if code == "NO_AUTH":
                return (
                    False,
                    "No activation key. Enter your key to use AI Edit.",
                    "NO_KEY",
                )
            return False, usage.get("error", "Unknown error"), code

        used = usage.get("images_used", 0)
        limit = usage.get("images_limit", 0)

        if used >= limit:
            return False, f"Monthly limit reached ({used}/{limit}).", "QUOTA_EXCEEDED"

        return True, f"{used}/{limit} images used", ""

    def get_usage_info(self) -> dict:
        """Fetch current usage info from backend."""
        if not self._activation_key:
            return {"error": "No activation key", "code": "NO_KEY"}
        try:
            return self._client.get_usage(auth=self.get_auth_header())
        except Exception:
            return {"error": "Connection error", "code": "CONNECTION_ERROR"}
