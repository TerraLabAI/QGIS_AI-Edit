











from __future__ import annotations

from typing import Callable

from ..core.i18n import get_locale


PRO_LOGIN_TARGET = "pricing"


_LOGIN_LINK_WAIT_MS = 6_000


_pending: set = set()


def _is_openable(url) -> bool:
    return isinstance(url, str) and url.startswith("https://") and " " not in url


def open_pro_page(
    cta_source: str,
    fallback_url: str,
    *,
    client=None,
    auth_manager=None,
    on_outcome: Callable[[str], None] | None = None,
) -> None:





    from .external_url import open_external

    state = {"done": False}

    def settle(url: str, checkout_link: str) -> None:
        if state["done"]:
            return
        state["done"] = True
        if on_outcome is not None:
            try:
                on_outcome(checkout_link)
            except Exception:  # nosec B110
                pass
        open_external(url)

    try:
        auth = auth_manager.get_auth_header() if auth_manager is not None else {}
    except Exception:  # nosec B110
        auth = {}
    if not auth or client is None:
        settle(fallback_url, "fallback")
        return

    try:
        from qgis.core import QgsApplication
        from qgis.PyQt.QtCore import QTimer

        from ..workers.generic_request_task import GenericRequestTask

        task = GenericRequestTask(
            "Opening the AI Edit plans",
            lambda: client.get_plugin_login_link(
                PRO_LOGIN_TARGET, cta_source, auth, locale=get_locale()),
            silent=True,
        )
    except Exception:  # nosec B110
        settle(fallback_url, "fallback")
        return

    def on_answer(answer) -> None:
        _pending.discard(task)
        url = answer.get("url") if isinstance(answer, dict) else None
        if _is_openable(url):
            settle(url, "direct")
        else:
            settle(fallback_url, "fallback")

    def on_failed(*_args) -> None:
        _pending.discard(task)
        settle(fallback_url, "fallback")

    task.succeeded.connect(on_answer)
    task.failed.connect(on_failed)
    _pending.add(task)
    QTimer.singleShot(_LOGIN_LINK_WAIT_MS, lambda: settle(fallback_url, "fallback"))
    try:
        QgsApplication.taskManager().addTask(task)
    except Exception:  # nosec B110
        _pending.discard(task)
        settle(fallback_url, "fallback")
