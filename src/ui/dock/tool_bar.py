




















from __future__ import annotations

from typing import TYPE_CHECKING

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import QBoxLayout, QPushButton, QWidget

from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ..icons import icon_for
from .design_tokens import (
    BTN_QUIET_QSS,
    BTN_SMALL_PX,
    INK,
    INK_2,
    INK_3,
    SPACE_CARD,
    picked_qss,
    qcolor,
)
from .style import _tinted_svg_icon

if TYPE_CHECKING:
    from .widget import AIEditDockWidget


_GLYPH_PX = 14






RESULT_TOOL_QSS = BTN_QUIET_QSS + (
    f"QPushButton {{ min-height: {BTN_SMALL_PX - 8}px; padding: 2px 8px 2px 6px; }}"
    f'QPushButton[suggested="true"] {{ color: {INK}; font-weight: 600; }}'
    + picked_qss("QPushButton")
)


class _ToolToggle(QPushButton):




    def __init__(self, parent: QWidget, on_change):
        super().__init__(parent)
        self._on_change = on_change

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        super().setVisible(visible)
        self._on_change()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(enabled)
        self._on_change()

    def set_active(self, active: bool) -> None:
        self.setProperty("active", bool(active))


def tool_available(toggle) -> bool:


    try:
        return not toggle.isHidden()
    except RuntimeError:
        return False


def build_tool_toggles(
    dock: AIEditDockWidget, vectorize_seq: QKeySequence, swipe_seq: QKeySequence
) -> None:




    holder = QWidget(dock)
    holder.setObjectName("aiEditToolToggles")
    holder.setVisible(False)
    dock._tool_toggles_holder = holder

    def sync() -> None:
        refresh = getattr(dock, "_sync_result_tools_row", None)
        if refresh is not None and hasattr(dock, "_vectorize_btn") and hasattr(dock, "_swipe_btn"):
            refresh()

    dock._vectorize_btn = _ToolToggle(holder, sync)
    dock._vectorize_btn.clicked.connect(dock.vectorize_clicked.emit)
    dock._vectorize_btn.setVisible(False)


    dock._swipe_btn = _ToolToggle(holder, sync)
    dock._swipe_btn.setCheckable(True)
    dock._swipe_btn.setEnabled(False)
    dock._swipe_btn.toggled.connect(dock.swipe_toggled.emit)
    dock._swipe_btn.toggled.connect(lambda _checked: sync())
    dock._swipe_btn.setVisible(False)

    native = QKeySequence.SequenceFormat.NativeText
    for btn, seq in (
        (getattr(dock, "_result_compare_btn", None), swipe_seq),
        (getattr(dock, "_result_vectorize_btn", None), vectorize_seq),
    ):
        if btn is not None and not seq.isEmpty():
            btn.setToolTip(f"{btn.toolTip()} ({seq.toString(native)})")

    vectorize_btn = getattr(dock, "_result_vectorize_btn", None)
    if vectorize_btn is not None:
        dock._result_vectorize_tip = vectorize_btn.toolTip()
    sync()


class _ResultToolsRow(QWidget):



    def __init__(self):
        super().__init__()
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(SPACE_CARD)

    def add_button(self, btn: QPushButton) -> None:
        self._box.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)

    def finish(self) -> None:
        self._box.addStretch(1)

    def _shown_buttons(self) -> list:
        items = (self._box.itemAt(i) for i in range(self._box.count()))
        return [it.widget() for it in items if it.widget() is not None and not it.widget().isHidden()]

    def minimumSizeHint(self) -> QSize:  # noqa: N802

        base = super().minimumSizeHint()
        widest = max((b.sizeHint().width() for b in self._shown_buttons()), default=0)
        return QSize(min(base.width(), widest), base.height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit(event.size().width())

    def _fit(self, width: int) -> None:
        shown = self._shown_buttons()
        need = sum(b.sizeHint().width() for b in shown)
        need += self._box.spacing() * max(0, len(shown) - 1)
        direction = (
            QBoxLayout.Direction.TopToBottom if need > width else QBoxLayout.Direction.LeftToRight
        )
        if self._box.direction() != direction:
            self._box.setDirection(direction)


def set_compare_icon(btn: QPushButton) -> None:

    btn.setIcon(_swipe_icon(qcolor(INK if btn.isChecked() else INK_2)))


def _swipe_icon(ink):


    return _tinted_svg_icon("swipe.svg", ink)


def build_result_tools_row(dock: AIEditDockWidget) -> QWidget:


    row = _ResultToolsRow()
    dock._result_tools_row = row

    compare = QPushButton(get_export_copy("dock.build_result.compare_btn", tr("Compare")))
    compare_tip = get_export_copy(
        "dock.build_result.compare_btn_tooltip",
        tr("Swipe between the original map and this result"),
    )
    compare.setToolTip(compare_tip)
    compare.setAccessibleName(compare.text())
    compare.setIcon(_swipe_icon(qcolor(INK_2)))
    compare.setCheckable(True)
    compare.toggled.connect(dock._on_result_compare_toggled)
    dock._result_compare_btn = compare

    vectorize = QPushButton(get_export_copy("dock.build_result.vectorize", tr("Vectorize")))
    vectorize_tip = get_export_copy(
        "dock.build_result.vectorize_btn_tooltip", tr("Turn this result into polygons you can edit")
    )
    vectorize.setToolTip(vectorize_tip)
    vectorize.setAccessibleName(vectorize.text())
    vectorize.setIcon(
        icon_for(vectorize, "polygon", _GLYPH_PX, qcolor(INK_2), disabled_color=qcolor(INK_3))
    )

    vectorize.clicked.connect(dock._on_result_vectorize_clicked)
    vectorize.setProperty("suggested", False)
    dock._result_vectorize_btn = vectorize
    dock._result_vectorize_tip = vectorize_tip
    dock._vectorize_cta_pending = None

    for btn in (compare, vectorize):
        btn.setStyleSheet(RESULT_TOOL_QSS)
        btn.setIconSize(QSize(_GLYPH_PX, _GLYPH_PX))
        btn.setMinimumHeight(BTN_SMALL_PX)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        row.add_button(btn)
    row.finish()
    row.setVisible(False)
    return row
