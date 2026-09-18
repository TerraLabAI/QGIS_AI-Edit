"""AI Edit's two map tools, Vectorize and Compare, in the dock.

They used to sit in a bar pinned under the whole dock, visible on every
screen, and people did not know what they were for or when to use them
(Yvann, 2026-09-17). They now appear only where they make sense: under a
generated result, as two quiet buttons sized to their words.

prompt, then ``[Generate] [New edit]``
version strip
``◧ Compare   ▱ Vectorize``

Two hidden toggles stay the source of truth (``dock._vectorize_btn`` and
``dock._swipe_btn``): the plugin, the account kill switches, Escape and the
MCP API drive them as before. Showing or hiding a toggle means "the tool is
available"; the visible result buttons mirror their availability, enabled
state and checked state (``DockToolsFooterMixin._sync_result_tools_row``).
The keyboard shortcuts click the toggles, so they work on every screen.

Keys are picked at build time so none collides with a QGIS menu mnemonic
(``pick_dock_shortcuts``); the result button tooltips name the key bound.
"""
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

# 14 px: the row is a compact line under the versions (Yvann, 2026-09-18).
_GLYPH_PX = 14

# The quiet button at the 28 px target, with the picked look while
# the comparison is on (the one visible sign, next to the canvas pill, that
# a click here ends it). A result that looks ready to vectorize (flat colour
# zones, a template with vector hints) gives Vectorize the strong ink and a
# heavier weight, no new hue (``suggested`` property, set_vectorize_suggestion).
RESULT_TOOL_QSS = BTN_QUIET_QSS + (
    f"QPushButton {{ min-height: {BTN_SMALL_PX - 8}px; padding: 2px 8px 2px 6px; }}"
    f'QPushButton[suggested="true"] {{ color: {INK}; font-weight: 600; }}'
    + picked_qss("QPushButton")
)


class _ToolToggle(QPushButton):
    """A never-drawn button holding one tool's state (available, enabled,
    checked). ``setVisible`` records availability and refreshes the result
    row; ``set_active`` is kept for the panel code that lights it."""

    def __init__(self, parent: QWidget, on_change):
        super().__init__(parent)
        self._on_change = on_change

    def setVisible(self, visible: bool) -> None:  # noqa: N802 - Qt override
        super().setVisible(visible)
        self._on_change()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt override
        super().setEnabled(enabled)
        self._on_change()

    def set_active(self, active: bool) -> None:
        self.setProperty("active", bool(active))


def tool_available(toggle) -> bool:
    """True while the tool's toggle is switched on (signed in, feature on).
    The toggles are never on screen, so ``isHidden`` is the flag."""
    try:
        return not toggle.isHidden()
    except RuntimeError:
        return False  # the dock is being torn down


def build_tool_toggles(
    dock: AIEditDockWidget, vectorize_seq: QKeySequence, swipe_seq: QKeySequence
) -> None:
    """Hang the hidden ``dock._vectorize_btn`` / ``dock._swipe_btn`` on a
    hidden holder, and name the bound keys in the result buttons' tooltips.
    Runs after ``build_result_tools_row``. The caller wires the shortcuts
    (``_make_dock_shortcut``)."""
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

    # A checkable toggle: on arms the swipe map tool, off (or Esc) disarms it.
    dock._swipe_btn = _ToolToggle(holder, sync)
    dock._swipe_btn.setCheckable(True)
    dock._swipe_btn.setEnabled(False)  # gated on the active layer
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
    # The plain Vectorize tooltip, key included, for the suggestion to restore.
    vectorize_btn = getattr(dock, "_result_vectorize_btn", None)
    if vectorize_btn is not None:
        dock._result_vectorize_tip = vectorize_btn.toolTip()
    sync()


class _ResultToolsRow(QWidget):
    """Two quiet buttons side by side, stacked when their words do not fit
    the dock's width (a long translation at the narrowest dock)."""

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

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        # Narrow enough to stack: the widest button, not the whole row.
        base = super().minimumSizeHint()
        widest = max((b.sizeHint().width() for b in self._shown_buttons()), default=0)
        return QSize(min(base.width(), widest), base.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit(event.size().width())

    def _fit(self, width: int) -> None:
        shown = self._shown_buttons()
        need = sum(b.sizeHint().width() for b in shown)  # win-ok: no equal stretch, sizeHint each
        need += self._box.spacing() * max(0, len(shown) - 1)
        direction = (
            QBoxLayout.Direction.TopToBottom if need > width else QBoxLayout.Direction.LeftToRight
        )
        if self._box.direction() != direction:
            self._box.setDirection(direction)


def set_compare_icon(btn: QPushButton) -> None:
    """The swipe glyph, strong ink while the comparison is on."""
    btn.setIcon(_swipe_icon(qcolor(INK if btn.isChecked() else INK_2)))


def _swipe_icon(ink):
    # swipe.svg ships with the plugin (the canvas pill uses it too); icons.py
    # has no before/after glyph.
    return _tinted_svg_icon("swipe.svg", ink)


def build_result_tools_row(dock: AIEditDockWidget) -> QWidget:
    """The result screen's Compare and Vectorize buttons, hung on
    ``dock._result_compare_btn`` / ``dock._result_vectorize_btn``."""
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
    # Opens the panel pre-filled when the result carries a suggestion.
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
    row.setVisible(False)  # shown once a tool is available (_sync_result_tools_row)
    return row
