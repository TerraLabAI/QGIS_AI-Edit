



























from __future__ import annotations

from qgis.core import QgsNetworkAccessManager, QgsNetworkReplyContent
from qgis.PyQt.QtCore import QByteArray, QEventLoop, QTimer
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..core import qt_compat as QtC

_POLL_MS = 20


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
        if content.error() == QtC.NetworkNoError:
            self._error = ""
            return QtC.BlockingNoError
        self._error = content.errorString() or ""
        return QtC.BlockingNetworkError

    @staticmethod
    def _put(request, body, feedback):




        reply = QgsNetworkAccessManager.instance().put(request, body)
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(_POLL_MS)
        state = {"reply": reply}

        def _tick():
            current = state["reply"]
            try:
                if current is None or current.isFinished():
                    loop.quit()
                elif feedback is not None and feedback.isCanceled():
                    current.abort()
            except RuntimeError:
                loop.quit()

        poll.timeout.connect(_tick)
        try:
            if not reply.isFinished():
                poll.start()
                loop.exec()
            content = QgsNetworkReplyContent(reply)
            content.setContent(reply.readAll())
            return content
        finally:
            poll.stop()
            state["reply"] = None
            reply.deleteLater()
