





























from __future__ import annotations

import time
import uuid

from qgis.core import QgsNetworkAccessManager, QgsNetworkReplyContent
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QByteArray, QEventLoop, QTimer
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..core import qt_compat as QtC

_POLL_MS = 20
_Attribute = getattr(QNetworkRequest, "Attribute", QNetworkRequest)
_User = _Attribute.User


_TOKEN_ATTR = _Attribute(int(getattr(_User, "value", _User)) + 71)

_AUTO_DELETE_ATTR = getattr(_Attribute, "AutoDeleteReplyOnFinishAttribute", None)


_DEADLINE_MARGIN_S = 30.0
_DEFAULT_DEADLINE_S = 600.0


class BlockingRequest:


    def __init__(self):
        self._reply = None
        self._error = ""

    def get(self, request, forceRefresh: bool = False, feedback=None):  # noqa: N803
        return self._run("GET", request, None, forceRefresh, feedback)

    def post(self, request, data, forceRefresh: bool = False, feedback=None):  # noqa: N803
        return self._run("POST", request, data, forceRefresh, feedback)

    def put(self, request, data, feedback=None):
        return self._run("PUT", request, data, True, feedback)

    def reply(self):
        return self._reply

    def errorMessage(self) -> str:  # noqa: N802
        return self._error

    def _run(self, method: str, request, data, force_refresh: bool, feedback):
        request = QNetworkRequest(request)
        if request.attribute(QtC.RedirectPolicyAttribute) is None:
            request.setAttribute(QtC.RedirectPolicyAttribute, QtC.NoLessSafeRedirectPolicy)
        body = data if isinstance(data, QByteArray) else QByteArray(bytes(data or b""))
        if method == "PUT":
            content = self._put(request, body, feedback)
        elif method == "POST":
            content = QgsNetworkAccessManager.blockingPost(request, body, "", force_refresh, None)
        else:
            content = QgsNetworkAccessManager.blockingGet(request, "", force_refresh, None)
        self._reply = content
        if content is None:
            self._error = "No reply from the network stack"
            return QtC.BlockingNetworkError
        if content.error() == QtC.NetworkNoError:
            self._error = ""
            return QtC.BlockingNoError
        self._error = content.errorString() or ""
        return QtC.BlockingNetworkError

    @staticmethod
    def _put(request, body, feedback):













        nam = QgsNetworkAccessManager.instance()
        token = uuid.uuid4().hex
        request.setAttribute(_TOKEN_ATTR, token)
        if _AUTO_DELETE_ATTR is not None:
            request.setAttribute(_AUTO_DELETE_ATTR, True)
        try:
            timeout_s = request.transferTimeout() / 1000.0
        except AttributeError:
            timeout_s = 0
        deadline = time.monotonic() + (timeout_s if timeout_s > 0 else _DEFAULT_DEADLINE_S) + _DEADLINE_MARGIN_S
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(_POLL_MS)
        state = {"content": None, "reply": None, "aborted": False}

        def _abort():
            state["aborted"] = True
            reply = state["reply"]
            try:
                if reply is not None and not sip.isdeleted(reply):
                    reply.abort()
            except RuntimeError:
                pass  # nosec B110

        def _finished(content):
            try:
                if state["content"] is None and content.request().attribute(_TOKEN_ATTR) == token:
                    state["content"] = QgsNetworkReplyContent(content)
                    loop.quit()
            except Exception:  # noqa: BLE001
                loop.quit()

        def _tick():
            try:
                if state["content"] is not None:
                    loop.quit()
                elif time.monotonic() > deadline:
                    _abort()
                    loop.quit()
                elif feedback is not None and feedback.isCanceled():



                    _abort()
                    loop.quit()
            except Exception:  # noqa: BLE001
                loop.quit()

        signal = nam.finished[QgsNetworkReplyContent]
        signal.connect(_finished)
        poll.timeout.connect(_tick)
        try:
            state["reply"] = nam.put(request, body)
            if state["content"] is None:
                poll.start()
                loop.exec()
        finally:
            poll.stop()
            signal.disconnect(_finished)
            reply, state["reply"] = state["reply"], None
            if _AUTO_DELETE_ATTR is None and reply is not None:
                try:
                    if not sip.isdeleted(reply):
                        reply.deleteLater()
                except RuntimeError:
                    pass  # nosec B110
        return state["content"]
