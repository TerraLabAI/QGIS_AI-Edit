






from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QObject, Qt, QTimer
from qgis.PyQt.QtWidgets import QDockWidget, QLabel

from ...core import qt_compat as QtC
from ...core.i18n import tr
from ..dock import design_tokens as tokens



_TOP_GAP = 12

_FLASH_MS = 2200


def hint_for_vertex_count(count: int) -> str:
    if count <= 0:
        return tr("Click to add points, or drag a box")
    if count < 3:
        return tr("Keep clicking to add points")
    return tr("Double-click, right-click or press Enter to finish")


def too_few_points_text() -> str:
    return tr("Add at least 3 points")


class ZoneDrawHint(QObject):


    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._label: QLabel | None = None
        self._state_text = ""
        self._state_count = -1
        self._shown = False
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)
        canvas.installEventFilter(self)


        self._message_bar = self._find_message_bar()
        if self._message_bar is not None:
            self._message_bar.installEventFilter(self)



    def show_state(self, vertex_count: int) -> None:


        if vertex_count != self._state_count:
            self._flash_timer.stop()
        self._state_count = vertex_count
        self._state_text = hint_for_vertex_count(vertex_count)
        self._shown = True
        if not self._flash_timer.isActive():
            self._render(self._state_text)

    def flash_too_few(self) -> None:
        self._shown = True
        self._render(too_few_points_text())
        self._flash_timer.start(_FLASH_MS)

    def hide(self) -> None:
        self._shown = False
        self._state_count = -1
        self._flash_timer.stop()
        if self._label is not None:
            self._label.hide()
        self._set_dock_text(None)

    def dispose(self) -> None:
        self.hide()
        for watched in (self._canvas, self._message_bar):
            if watched is None:
                continue
            try:
                watched.removeEventFilter(self)
            except RuntimeError:
                pass
        self._message_bar = None
        if self._label is not None:
            try:
                self._label.deleteLater()
            except RuntimeError:
                pass
            self._label = None


        try:
            self.deleteLater()
        except RuntimeError:
            pass

    def text(self) -> str:

        if self._label is None or not self._label.isVisible():
            return ""
        return self._label.text()



    def _end_flash(self) -> None:
        if self._shown:
            self._render(self._state_text)

    def _render(self, text: str) -> None:
        label = self._ensure_label()
        label.setText(text)
        label.adjustSize()
        self._place()
        label.show()
        label.raise_()
        self._set_dock_text(text)

    def _ensure_label(self) -> QLabel:
        if self._label is None:
            label = QLabel(self._canvas)
            label.setAttribute(QtC.WA_TransparentForMouseEvents, True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)


            label.setStyleSheet(
                f"QLabel {{ background: {tokens.SURFACE}; color: {tokens.INK};"
                f" border: 1px solid {tokens.LINE_STRONG}; border-radius: 14px;"
                f" font-size: {tokens.FONT_BODY}px; padding: 5px 14px; }}"
            )
            self._label = label
        return self._label

    def _place(self) -> None:
        if self._label is None:
            return
        viewport = self._canvas.viewport()
        geo = viewport.geometry() if viewport is not None else self._canvas.rect()
        x = geo.x() + max(0, (geo.width() - self._label.width()) // 2)
        top = geo.y()
        bar = self._message_bar
        try:
            if bar is not None and bar.isVisible():
                bar_rect = bar.rect()
                bottom = bar.mapTo(self._canvas.window(), bar_rect.bottomLeft())
                bottom = self._canvas.mapFrom(self._canvas.window(), bottom)
                if 0 <= bottom.y() < geo.height() // 2:
                    top = max(top, bottom.y())
        except (RuntimeError, TypeError):
            pass
        self._label.move(x, top + _TOP_GAP)

    def _find_message_bar(self):
        iface = self._iface()
        try:
            return iface.messageBar() if iface is not None else None
        except (AttributeError, RuntimeError):
            return None

    def eventFilter(self, obj, event):  # noqa: N802
        kind = event.type()
        on_canvas = obj is self._canvas and kind == QEvent.Type.Resize
        on_bar = obj is self._message_bar and kind in (
            QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Resize
        )
        if on_canvas or on_bar:
            self._place()
        return False

    def _iface(self):
        try:
            from qgis.utils import iface
        except ImportError:
            return None
        return iface

    def _set_dock_text(self, text: str | None) -> None:


        iface = self._iface()
        if iface is None:
            return
        try:
            dock = iface.mainWindow().findChild(QDockWidget, "AIEditDockWidget")
        except (AttributeError, RuntimeError):
            return
        label = getattr(dock, "_select_zone_hint", None) if dock is not None else None
        if label is None:
            return
        if text is None:
            text = getattr(dock, "_select_zone_hint_default", None)
            if text is None:
                return
        try:
            label.setText(text)
        except RuntimeError:
            pass
