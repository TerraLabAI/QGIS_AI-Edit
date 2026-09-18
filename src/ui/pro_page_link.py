"""One door from every Subscribe button to the AI Edit plans, signed in.

Same flow as AI Segmentation's ``pro_page_link``: the plugin holds an
activation key, so it asks ``/api/plugin/login-link`` for a one-time link to
the plans (the server resolves ``pricing`` to the AI Edit plans block) and
opens it. The user lands signed in, one click from Stripe Checkout, with no
magic link to read outside QGIS.

On no key, a refusal, an error or a timeout the plain served URL opens, which
the site reroutes to the same plans block signed out. The request runs on a
QgsTask, so the GUI thread never waits.
"""
from __future__ import annotations

from typing import Callable

from ..core.i18n import get_locale

# The server resolves this to the plans of the key's own product.
PRO_LOGIN_TARGET = "pricing"

# Backstop for a task that never reports; the socket has its own 5 s timeout.
_LOGIN_LINK_WAIT_MS = 6_000

# Tasks alive until they answer, so Python does not collect them mid-flight.
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
    """Open the plans signed in, or ``fallback_url``.

    ``on_outcome`` is called once with ``"direct"`` or ``"fallback"`` just
    before the browser opens, so the click event can say which door it took.
    """
    from .external_url import open_external

    state = {"done": False}

    def settle(url: str, checkout_link: str) -> None:
        if state["done"]:
            return
        state["done"] = True
        if on_outcome is not None:
            try:
                on_outcome(checkout_link)
            except Exception:  # nosec B110 -- telemetry never blocks a click
                pass
        open_external(url)

    try:
        auth = auth_manager.get_auth_header() if auth_manager is not None else {}
    except Exception:  # nosec B110 -- no key readable: the plain page
        auth = {}
    if not auth or client is None:
        settle(fallback_url, "fallback")
        return

    try:
        from qgis.core import QgsApplication
        from qgis.PyQt.QtCore import QTimer

        from ..workers.generic_request_task import GenericRequestTask

        task = GenericRequestTask(
            "Opening the AI Edit plans",  # silent task, never shown
            lambda: client.get_plugin_login_link(
                PRO_LOGIN_TARGET, cta_source, auth, locale=get_locale()),
            silent=True,
        )
    except Exception:  # nosec B110 -- no task manager: same destination as always
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
