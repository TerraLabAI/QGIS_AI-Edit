





from __future__ import annotations

from dataclasses import dataclass

from ....core.config_store import get_export_copy
from ....core.i18n import tr


def find_product_subscription(data: dict, product_id: str) -> dict | None:


    for sub in (data or {}).get("subscriptions", []) or []:
        if isinstance(sub, dict) and sub.get("product_id") == product_id:
            return sub
    return None


@dataclass
class AccountPlan:
    plan: str = "free"
    status: str = "active"
    used: int | None = None
    limit: int | None = None
    period_end: str = ""
    has_subscription: bool = False

    @property
    def is_free(self) -> bool:
        return self.plan == "free" and self.status != "trialing"

    @property
    def left(self) -> int | None:
        if self.used is None or self.limit is None:
            return None
        return max(0, int(self.limit) - int(self.used))


def _as_int(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def resolve_account_plan(data: dict, product_id: str) -> AccountPlan:

    data = data if isinstance(data, dict) else {}
    sub = find_product_subscription(data, product_id) or {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    plan = AccountPlan(has_subscription=bool(sub))
    plan.plan = str(sub.get("plan") or ("free" if usage.get("is_free_tier", True) else "pro"))
    plan.status = str(sub.get("status") or "active")
    plan.used = _as_int(sub.get("usage_this_month"))
    plan.limit = _as_int(sub.get("quota_limit"))
    if plan.used is None:
        plan.used = _as_int(usage.get("images_used"))
    if plan.limit is None:
        plan.limit = _as_int(usage.get("images_limit"))
    plan.period_end = str(sub.get("current_period_end") or usage.get("reset_date")
                          or usage.get("period_end") or "")
    return plan


def plan_display_name(plan: AccountPlan) -> str:
    if plan.plan == "pro":
        return tr("Pro plan")
    if plan.status == "trialing":
        return get_export_copy("dialogs.account_settings_dialog.plan_free_trial", tr("Free trial"))
    return tr("Free plan")
