












from __future__ import annotations

import hmac
from urllib.parse import parse_qs, urlsplit

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtNetwork import QHostAddress, QTcpServer

from ..logger import log_debug, log_warning

SIGNED_IN_PATH = "/terralab/signed-in"
_MAX_REQUEST_BYTES = 8192
_HEADERS = ("Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n"
            "Connection: close\r\n")
_BACK_TO_QGIS_HTML = (
    '<!doctype html><html><head><meta charset="utf-8"><title>TerraLab</title></head>'
    '<body style="font-family:sans-serif;text-align:center;margin-top:15vh">'
    "<h1>Go back to QGIS</h1><p>You can close this tab.</p></body></html>"
)


class PairingListener(QObject):



    grant_received = pyqtSignal(int, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server: QTcpServer | None = None
        self._codes: list[str] = []
        self._waiting: dict[int, object] = {}
        self._next_id = 0
        self.port = 0

    def start(self) -> int:
        if self._server is not None:
            return self.port
        server = QTcpServer(self)
        if not server.listen(QHostAddress("127.0.0.1"), 0):
            log_warning("Sign-in listener could not start; the code input will be used")
            server.deleteLater()
            return 0
        port = int(server.serverPort())
        if not 1024 <= port <= 65535:
            server.close()
            server.deleteLater()
            return 0
        server.newConnection.connect(self._on_connection)
        self._server = server
        self.port = port
        log_debug("Sign-in listener started")
        return port

    def is_listening(self) -> bool:
        return self._server is not None

    def add_code(self, code: str) -> None:
        if code and code not in self._codes:
            self._codes.append(code)

    def stop(self) -> None:

        for request_id in list(self._waiting):
            self.answer(request_id, "")
        self._codes = []
        if self._server is not None:
            try:
                self._server.close()
                self._server.deleteLater()
            except RuntimeError:  # nosec B110
                pass
            self._server = None
        self.port = 0

    def _knows_code(self, given: str) -> bool:
        found = False
        for code in self._codes:

            if hmac.compare_digest(given.encode("utf-8"), code.encode("utf-8")):
                found = True
        return found

    def _on_connection(self) -> None:
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                break
            socket.readyRead.connect(lambda s=socket: self._on_ready(s))
            socket.disconnected.connect(socket.deleteLater)

    def _on_ready(self, socket) -> None:
        try:
            if socket.property("tl_seen"):
                socket.readAll()
                return
            data = bytes(socket.peek(_MAX_REQUEST_BYTES))
            if b"\n" not in data:
                if len(data) >= _MAX_REQUEST_BYTES:
                    self._send(socket, 404)
                return
            socket.setProperty("tl_seen", True)
            socket.readAll()
            parts = data.split(b"\n", 1)[0].strip().decode("latin-1", "replace").split(" ")
            if self._server is None or len(parts) < 2 or parts[0].upper() != "GET":
                self._send(socket, 404)
                return
            url = urlsplit(parts[1])
            if url.path != SIGNED_IN_PATH:
                self._send(socket, 404)
                return
            query = parse_qs(url.query, keep_blank_values=True)
            code = (query.get("code") or [""])[0]
            grant = (query.get("grant") or [""])[0]
            if not code or not grant or not self._knows_code(code):
                self._send(socket, 404)
                return
            self._next_id += 1
            request_id = self._next_id
            self._waiting[request_id] = socket
            self.grant_received.emit(request_id, code, grant)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Sign-in listener: request failed ({type(exc).__name__})")
            try:
                self._send(socket, 404)
            except Exception:  # nosec B110
                pass

    def answer(self, request_id: int, redirect_url: str) -> None:


        socket = self._waiting.pop(request_id, None)
        if socket is None:
            return
        try:
            if redirect_url:
                self._send(socket, 302, location=redirect_url)
            else:
                self._send(socket, 200, html=_BACK_TO_QGIS_HTML)
        except RuntimeError:  # nosec B110
            pass

    @staticmethod
    def _send(socket, status: int, location: str = "", html: str = "") -> None:
        reason = {200: "OK", 302: "Found", 404: "Not Found"}.get(status, "OK")
        body = (html or ("Not found" if status == 404 else "")).encode("utf-8")
        head = f"HTTP/1.1 {status} {reason}\r\n{_HEADERS}"
        if location:
            head += f"Location: {location}\r\n"
        head += ("Content-Type: text/html; charset=utf-8\r\n" if html else "Content-Type: text/plain\r\n")
        head += f"Content-Length: {len(body)}\r\n\r\n"
        socket.write(head.encode("ascii") + body)
        socket.flush()
        socket.disconnectFromHost()
