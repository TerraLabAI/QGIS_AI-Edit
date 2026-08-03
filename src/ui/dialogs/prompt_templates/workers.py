"""Background QThread workers for history fetches and favorite sync."""
from __future__ import annotations

import time

try:  # SIP ships with both PyQt5 and PyQt6; used to hand a C++ object to C++.
    from qgis.PyQt import sip as _sip
except ImportError:  # pragma: no cover - defensive only
    _sip = None

from qgis.PyQt.QtCore import QCoreApplication, QThread, pyqtSignal

from ....core.logger import log_warning

# ---------------------------------------------------------------------------
# Sync workers
# ---------------------------------------------------------------------------


class _LibrarySyncWorker(QThread):
    """Background fetch of the user's past generations for Recent + Favorites.

    Pulls the rich before/after history endpoint (prompt + signed input/output
    URLs + location) so Recent and Favorites render as generation cards the
    user can re-add to the map, reuse, or download. Emits one signal per
    section so the dialog refreshes progressively."""

    recent_jobs_fetched = pyqtSignal(list, bool)
    favorite_jobs_fetched = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, client, auth: dict, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth

    def run(self):
        # The two fetches below block on the API timeout and cannot be aborted
        # from outside, so these checks are the only points where a shutdown
        # can actually shorten this worker instead of waiting it out.
        if self.isInterruptionRequested():
            return
        try:
            hist = self._client.get_generation_history(self._auth, limit=50)
        except Exception as e:
            self.failed.emit(f"history: {e}")
            return
        if isinstance(hist, dict) and "error" not in hist:
            jobs = hist.get("jobs", []) or []
            # Older servers don't send has_more; a full page implies more.
            self.recent_jobs_fetched.emit(jobs, bool(hist.get("has_more", len(jobs) >= 50)))
        else:
            self.failed.emit(
                f"history: {hist.get('error', 'unknown') if isinstance(hist, dict) else 'parse_error'}"
            )

        if self.isInterruptionRequested():
            return
        try:
            favs = self._client.get_generation_history(
                self._auth, limit=50, favorites_only=True
            )
        except Exception as e:
            self.failed.emit(f"favorites: {e}")
            return
        if isinstance(favs, dict) and "error" not in favs:
            self.favorite_jobs_fetched.emit(favs.get("jobs", []) or [])
        else:
            self.failed.emit(
                f"favorites: {favs.get('error', 'unknown') if isinstance(favs, dict) else 'parse_error'}"
            )


class _HistoryPageWorker(QThread):
    """Background fetch of one OLDER page of the generation history - the
    Recent tab's server-side Load more, used once the locally held jobs are
    all visible but the server reported has_more."""

    page_fetched = pyqtSignal(list, bool)
    failed = pyqtSignal(str)

    def __init__(self, client, auth: dict, before: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._before = before

    def run(self):
        if self.isInterruptionRequested():
            return
        try:
            resp = self._client.get_generation_history(
                self._auth, limit=50, before=self._before
            )
        except Exception as e:
            self.failed.emit(f"history page: {e}")
            return
        if isinstance(resp, dict) and "error" not in resp:
            jobs = resp.get("jobs", []) or []
            self.page_fetched.emit(jobs, bool(resp.get("has_more", False)))
        else:
            self.failed.emit("history page: server error")


class _FavoriteSyncWorker(QThread):
    """Fire-and-forget POST/DELETE for a single prompt favorite toggle (the ★
    on a curated template, distinct from a generation favorite)."""

    def __init__(
        self,
        client,
        auth: dict,
        prompt: str,
        label: str,
        source_category: str,
        now_favorited: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._prompt = prompt
        self._label = label or None
        self._source_category = source_category or None
        self._now_favorited = now_favorited

    def run(self):
        if self.isInterruptionRequested():
            return
        try:
            if self._now_favorited:
                self._client.add_favorite(
                    self._auth, self._prompt, self._label, self._source_category
                )
            else:
                self._client.remove_favorite(self._auth, self._prompt)
        except Exception as e:
            log_warning(f"Favorite sync failed (silent): {e}")


class _GenerationFavoriteWorker(QThread):
    """Fire-and-forget star/unstar of a single past generation."""

    def __init__(self, client, auth: dict, request_id: str, now_favorited: bool, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._request_id = request_id
        self._now_favorited = now_favorited

    def run(self):
        if self.isInterruptionRequested():
            return
        try:
            self._client.set_generation_favorite(
                self._auth, self._request_id, self._now_favorited
            )
        except Exception as e:
            log_warning(f"Generation favorite sync failed (silent): {e}")


# In-flight background workers, held independently of any dialog. A running
# QThread that loses its last Python reference can be garbage-collected and
# destroyed mid-run, which aborts the QGIS process. Keeping the worker here
# until it emits finished lets the dialog be closed or deleted at any time
# while the blocking fetch is still going, without crashing.
_INFLIGHT_WORKERS: set = set()


def _detach_worker(worker: QThread) -> None:
    _INFLIGHT_WORKERS.add(worker)
    worker.finished.connect(lambda: _INFLIGHT_WORKERS.discard(worker))
    worker.finished.connect(worker.deleteLater)


# TOTAL time a shutdown spends waiting on the detached workers, in ms. Not per
# worker: one star click starts one fire-and-forget worker, so five stalled
# toggles on a bad network used to freeze QGIS for five times this budget, on
# the main thread, with no UI to say why.
DRAIN_WAIT_MS = 4000

# Signals the Prompt Library dialogs connect to. The drain cuts these by name
# rather than blockSignals(True), which would also block `finished` - the
# signal carrying each worker's own deleteLater, so a worker completing during
# the drain leaked its C++ QThread (harmless at exit, real under Plugin
# Reloader). Not every worker class declares every name; getattr skips the rest.
_DIALOG_SIGNALS = (
    "recent_jobs_fetched",
    "favorite_jobs_fetched",
    "page_fetched",
    "failed",
)

# Threads that outlived the drain budget. Held so nothing here drops the last
# reference; the real protection is the ownership move in _strand_worker.
_STRANDED_WORKERS: list = []


def _mute_worker(worker: QThread) -> None:
    """Cut the worker's dialog-facing signals, leaving `finished` connected."""
    for name in _DIALOG_SIGNALS:
        try:
            sig = getattr(worker, name, None)
            if sig is None:
                continue
            sig.disconnect()
        except (RuntimeError, TypeError):  # nosec B110 - nothing was connected
            pass


def _strand_worker(worker: QThread) -> None:
    """Keep a thread we could not join alive until the process ends.

    ~QThread calls qFatal("QThread: Destroyed while thread is still running"),
    which on Windows is the crash dialog plus a crash-recovery prompt on the
    next launch. Every owner eventually runs that destructor: the Python
    wrapper when it is collected, and a QObject parent when the parent goes -
    so parenting the survivor to the application only moved the same abort to
    application teardown. Hand the C++ half to C++ with no owner instead:
    nothing destroys it, the OS reclaims the thread when the process exits, and
    the abort never happens. The cost is one leaked QThread per stalled
    request, and `finished` stays connected so a thread that does come back
    still deletes itself.

    Without sip we fall back to the application parent, which defers the abort
    rather than preventing it. There is no third option from here: the blocking
    request lives inside the API client, out of this module's reach.
    """
    _STRANDED_WORKERS.append(worker)
    if _sip is not None:
        try:
            worker.setParent(None)
            _sip.transferto(worker, None)
            return
        except (RuntimeError, TypeError, ValueError):  # nosec B110
            pass
    try:
        worker.setParent(QCoreApplication.instance())
    except (RuntimeError, TypeError):  # nosec B110
        pass
    log_warning("Prompt Library worker outlived the shutdown drain and could not be detached")


def drain_prompt_library_workers(wait_ms: int = DRAIN_WAIT_MS) -> None:
    """Join the detached Prompt Library threads before Qt destroys them.

    Qt calls qFatal("QThread: Destroyed while thread is still running") and
    aborts the process when a QThread is deleted from inside run(), which is
    what quitting QGIS during a library sync used to do. Call from unload()
    and from aboutToQuit.

    ``wait_ms`` is the budget for the WHOLE drain, not for each worker.

    A request already in flight cannot be aborted from here: the blocking
    request object is created and owned inside the API client, so the only
    cooperative exits are the isInterruptionRequested() checks at the top of
    each run() and between _LibrarySyncWorker's two fetches. Anything still
    inside a request when the budget runs out is STRANDED, not stopped (see
    _strand_worker): the process keeps a live thread until it exits.
    """
    deadline = time.monotonic() + max(0, wait_ms) / 1000.0
    for worker in list(_INFLIGHT_WORKERS):
        _mute_worker(worker)
        try:
            worker.requestInterruption()
        except (RuntimeError, AttributeError):  # nosec B110 - C++ half gone
            pass
        try:
            if worker.isRunning():
                remaining_ms = int(max(0.0, deadline - time.monotonic()) * 1000)
                worker.wait(remaining_ms)
            still_running = worker.isRunning()
        except RuntimeError:
            still_running = False
        if not still_running:
            _INFLIGHT_WORKERS.discard(worker)
            continue
        _strand_worker(worker)
