"""Detected-class list for the Vectorize panel.

One row per color found in the map: checkbox (trace it or not), color swatch
(click to adjust), editable name, and coverage. Unchecked rows still matter -
they absorb their own pixels during nearest-color assignment so a traced class
never bleeds into a neighbor.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.i18n import tr
from ...panel_helpers import apply_swatch_style

# Two detected colors closer than this (summed-channel) are the same class:
# used to dedupe eyedropper picks against existing rows.
_SAME_CLASS_L1 = 48

_NAME_QSS = (
    "QLineEdit { border: 1px solid rgba(128,128,128,0.3); border-radius: 4px;"
    " padding: 2px 6px; font-size: 11px; color: palette(text);"
    " background: palette(base); }"
)


class _ClassRow(QWidget):
    """One detected class: [x] [swatch] [name] [coverage%]."""

    toggled = pyqtSignal()
    color_changed = pyqtSignal()

    def __init__(self, rgb: tuple[int, int, int], fraction: float | None,
                 label: str, checked: bool, parent=None) -> None:
        super().__init__(parent)
        self.rgb = tuple(rgb)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.check = QCheckBox()
        self.check.setChecked(checked)
        self.check.setToolTip(
            tr("Trace this color as polygons. Unchecked colors are treated "
               "as background.")
        )
        self.check.toggled.connect(lambda _c: self.toggled.emit())
        row.addWidget(self.check)

        self.swatch = QPushButton()
        self.swatch.setFixedSize(22, 22)
        self.swatch.setCursor(QtC.PointingHandCursor)
        self.swatch.setToolTip(tr("Adjust this color."))
        apply_swatch_style(self.swatch, QColor(*self.rgb))
        self.swatch.clicked.connect(self._on_swatch_clicked)
        row.addWidget(self.swatch)

        self.name = QLineEdit(label)
        self.name.setPlaceholderText(tr("Class name"))
        self.name.setStyleSheet(_NAME_QSS)
        self.name.setToolTip(
            tr("Free-text label written to each polygon's class_name attribute.")
        )
        row.addWidget(self.name, 1)

        self.coverage = QLabel(
            f"{fraction * 100.0:.0f}%" if fraction is not None else ""
        )
        self.coverage.setToolTip(tr("Share of the map covered by this color."))
        self.coverage.setStyleSheet(
            "font-size: 10px; color: palette(text); background: transparent;"
            " border: none; min-width: 28px;"
        )
        row.addWidget(self.coverage)

    def _on_swatch_clicked(self) -> None:
        chosen = QColorDialog.getColor(QColor(*self.rgb), self, tr("Pick color"))
        if not chosen.isValid():
            return
        self.rgb = (chosen.red(), chosen.green(), chosen.blue())
        apply_swatch_style(self.swatch, QColor(*self.rgb))
        self.color_changed.emit()


class ClassListWidget(QWidget):
    """The list of detected classes plus helpers to query the selection.

    ``classes_changed`` fires on any change that alters WHAT the next run
    traces (checkbox, color, added row) - never on plain renames, which only
    matter when the run actually happens."""

    classes_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[_ClassRow] = []
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(4)

    # -- population ------------------------------------------------------

    def set_classes(self, entries: list[dict]) -> None:
        """Rebuild the rows from ``detect_classes()`` output. Background
        entries start unchecked; every real class starts checked."""
        self.clear()
        for i, entry in enumerate(entries):
            label = entry.get("label") or tr("Class {n}").format(n=i + 1)
            self._add_row(
                entry["rgb"],
                entry.get("fraction"),
                label,
                checked=not entry.get("is_background", False),
            )

    def clear(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []

    def add_class(self, rgb: tuple[int, int, int], label: str = "",
                  checked: bool = True) -> None:
        """Add a color (eyedropper pick). A near-duplicate of an existing row
        checks that row instead of stacking a twin."""
        existing = self._nearest_row(rgb, _SAME_CLASS_L1)
        if existing is not None:
            existing.check.setChecked(checked)
            if label:
                existing.name.setText(label)
            self.classes_changed.emit()
            return
        row = self._add_row(
            rgb, None, label or tr("Class {n}").format(n=len(self._rows) + 1),
            checked=checked,
        )
        row.setVisible(True)
        self.classes_changed.emit()

    def ensure_class(self, rgb: tuple[int, int, int], label: str = "") -> None:
        """Preconfigure path (template CTA): make sure ``rgb`` is present and
        checked, carrying the template's class label."""
        existing = self._nearest_row(rgb, _SAME_CLASS_L1 * 2)
        if existing is not None:
            existing.check.blockSignals(True)
            existing.check.setChecked(True)
            existing.check.blockSignals(False)
            if label:
                existing.name.setText(label)
            return
        self._add_row(rgb, None, label, checked=True)

    # -- queries ----------------------------------------------------------

    def selected_classes(self) -> list[dict]:
        """Checked rows, in display order: ``[{"rgb": .., "label": ..}, ...]``."""
        out = []
        for i, row in enumerate(self._rows):
            if not row.check.isChecked():
                continue
            label = row.name.text().strip() or tr("Class {n}").format(n=i + 1)
            out.append({"rgb": row.rgb, "label": label})
        return out

    def competitor_colors(self) -> list[tuple[int, int, int]]:
        """Unchecked rows: absorbed as background during assignment."""
        return [row.rgb for row in self._rows if not row.check.isChecked()]

    def count(self) -> int:
        return len(self._rows)

    def selection_signature(self) -> tuple:
        """Hashable snapshot of what would be traced (for restyle decisions)."""
        return tuple(
            (row.rgb, row.name.text().strip())
            for row in self._rows
            if row.check.isChecked()
        )

    # -- internals ---------------------------------------------------------

    def _add_row(self, rgb, fraction, label: str, checked: bool) -> _ClassRow:
        row = _ClassRow(rgb, fraction, label, checked, self)
        row.toggled.connect(self.classes_changed.emit)
        row.color_changed.connect(self.classes_changed.emit)
        self._box.addWidget(row)
        self._rows.append(row)
        return row

    def _nearest_row(self, rgb, max_l1: int) -> _ClassRow | None:
        best, best_d = None, max_l1 + 1
        for row in self._rows:
            d = sum(abs(a - b) for a, b in zip(row.rgb, rgb))
            if d < best_d:
                best, best_d = row, d
        return best
