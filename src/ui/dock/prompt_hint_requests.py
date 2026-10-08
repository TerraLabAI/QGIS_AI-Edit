







from __future__ import annotations

import time
from typing import Callable

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QObject, QTimer

from ...core.logger import log_debug


PROMPT_HINT_KINDS = frozenset({"vector_file", "measure", "land_cover", "objects", "qa"})


_PROMPT_HINT_DEBOUNCE_MS = 400

_PROMPT_HINT_CACHE_MAX = 64

_PROMPT_HINT_RATE_LIMIT_PAUSE_S = 60.0
_PROMPT_HINT_RATE_LIMIT_MAX_S = 600.0


class PromptHintRequester(QObject):







    def __init__(
        self,
        deliver: Callable[[str, str | None], None],
        client_and_auth: Callable[[], tuple],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._deliver = deliver
        self._client_and_auth = client_and_auth
        self._timers: dict[str, QTimer] = {}
        self._wanted: dict[str, tuple[str, bool]] = {}
        self._tasks: dict[str, object] = {}
        self._cache: dict[tuple[str, bool], str | None] = {}


        self._refused_auth: dict | None = None
        self._paused_until = 0.0
        self._closed = False

    def request(self, box: str, text: str, has_template: bool) -> None:


        if self._closed:
            return
        key = (text or "", bool(has_template))
        self._wanted[box] = key
        if not key[0]:
            self._stop_timer(box)
            self._cancel_task(box)
            self._deliver(box, None)
            return
        if key in self._cache:
            self._stop_timer(box)
            self._cancel_task(box)

            _client, auth = self._client_and_auth()
            self._deliver(box, self._cache[key] if auth else None)
            return
        timer = self._timers.get(box)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(_PROMPT_HINT_DEBOUNCE_MS)
            timer.timeout.connect(lambda b=box: self._ask(b))
            self._timers[box] = timer
        timer.start()

    def shutdown(self) -> None:

        self._closed = True
        for timer in self._timers.values():
            try:
                timer.stop()
            except RuntimeError:  # nosec B110
                pass
        for box in list(self._tasks):
            self._cancel_task(box)



    def _stop_timer(self, box: str) -> None:
        timer = self._timers.get(box)
        if timer is not None:
            timer.stop()

    def _cancel_task(self, box: str) -> None:


        task = self._tasks.pop(box, None)
        if task is None:
            return
        for name in ("succeeded", "failed"):
            try:
                getattr(task, name).disconnect()
            except (TypeError, RuntimeError):  # nosec B110
                pass
        try:
            task.cancel()
        except RuntimeError:  # nosec B110
            pass

    def _ask(self, box: str) -> None:
        if self._closed:
            return
        key = self._wanted.get(box)
        if key is None or not key[0]:
            return
        self._cancel_task(box)
        client, auth = self._client_and_auth()
        if client is None or not auth or auth == self._refused_auth or time.monotonic() < self._paused_until:
            self._deliver(box, None)
            return
        from ...workers.generic_request_task import GenericRequestTask

        text, has_template = key


        task = GenericRequestTask(
            "AI Edit prompt hint",
            lambda c=client, a=auth, t=text, h=has_template: {"answer": c.get_prompt_hints(a, t, h)},
            silent=True,
        )
        task.succeeded.connect(lambda result, b=box, k=key, a=auth: self._on_answer(b, k, a, result))
        task.failed.connect(lambda _msg, _code, b=box, k=key: self._on_failed(b, k))
        self._tasks[box] = task
        QgsApplication.taskManager().addTask(task)

    def _on_answer(self, box: str, key: tuple[str, bool], auth: dict, result) -> None:
        if self._closed:
            return
        self._tasks.pop(box, None)
        answer = result.get("answer") if isinstance(result, dict) else None
        if not isinstance(answer, dict):
            self._on_failed(box, key)
            return
        if "error" in answer:
            self._note_refusal(answer, auth)
            self._on_failed(box, key)
            return
        kind = answer.get("kind")
        if kind not in PROMPT_HINT_KINDS:
            kind = None
        self._remember(key, kind)
        if self._wanted.get(box) == key:
            self._deliver(box, kind)

    def _on_failed(self, box: str, key: tuple[str, bool]) -> None:

        if self._closed:
            return
        self._tasks.pop(box, None)
        if self._wanted.get(box) == key:
            self._deliver(box, None)

    def _note_refusal(self, answer: dict, auth: dict) -> None:
        status = answer.get("http_status")
        code = str(answer.get("code") or "").strip().upper()
        if status in (401, 403):
            self._refused_auth = dict(auth)
            log_debug(f"Prompt hints refused ({code or status}); off until the sign-in changes")
        elif status == 429 or code == "RATE_LIMITED":
            wait = answer.get("retry_after")
            if not isinstance(wait, (int, float)) or isinstance(wait, bool) or wait <= 0:
                wait = _PROMPT_HINT_RATE_LIMIT_PAUSE_S
            self._paused_until = time.monotonic() + min(float(wait), _PROMPT_HINT_RATE_LIMIT_MAX_S)

    def _remember(self, key: tuple[str, bool], kind: str | None) -> None:
        self._cache.pop(key, None)
        if len(self._cache) >= _PROMPT_HINT_CACHE_MAX:
            self._cache.pop(next(iter(self._cache)), None)
        self._cache[key] = kind
