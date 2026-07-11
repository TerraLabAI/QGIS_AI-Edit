






from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
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
from ....core.config_store import get_export_copy, get_export_dial
from ....core.i18n import tr
from ...dock import design_tokens as T
from ...dock.design_tokens import repolish_widget
from ...panel_helpers import apply_swatch_style, check_box_qss





_SAME_CLASS_L1 = 40






_SWATCH_PX = 22
_ROW_QSS = (
    "QWidget#classRow { background: transparent;"
    f" border-radius: {T.RADIUS_CONTROL}px; }}"
    f"QWidget#classRow:hover {{ background: {T.HOVER}; }}"
    "QWidget#classRow QLineEdit { background: transparent; border: 1px solid transparent;"
    f" border-radius: {T.RADIUS_CHIP}px; padding: 2px 6px; font-size: {T.FONT_BODY}px; color: {T.INK};"
    f" selection-background-color: {T.ACCENT_BORDER}; }}"
    f"QWidget#classRow QLineEdit:hover {{ border-color: {T.LINE_STRONG}; background: {T.SURFACE}; }}"
    f"QWidget#classRow QLineEdit:focus {{ border-color: {T.ACCENT_BORDER}; background: {T.SURFACE}; }}"
    f'QWidget#classRow[traced="false"] QLineEdit {{ color: {T.INK_2}; }}'
    "QWidget#classRow QLabel#classCoverage { background: transparent; border: none;"
    f" font-size: {T.FONT_HINT}px; color: {T.INK_2}; }}"
)


def _class_number_name(n: int) -> str:

    return tr("Class {n}").format(n=n)


def _capitalized(label: str) -> str:

    return label[:1].upper() + label[1:]


class _ClassRow(QWidget):


    toggled = pyqtSignal()
    color_changed = pyqtSignal()

    def __init__(self, rgb: tuple[int, int, int], fraction: float | None,
                 label: str, checked: bool, parent=None) -> None:
        super().__init__(parent)
        self.rgb = tuple(rgb)
        self.setObjectName("classRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(_ROW_QSS)
        self.setMinimumHeight(T.ROW_PX + 4)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 8, 2)
        row.setSpacing(T.SPACE_CARD)

        self.check = QCheckBox()
        self.check.setChecked(checked)
        self.check.setStyleSheet(check_box_qss())
        self.check.setToolTip(
            get_export_copy(
                "widgets.class_list.trace_checkbox_tip",
                tr("Trace this color as polygons. Unchecked colors are treated "
                   "as background."),
            )
        )
        self.check.toggled.connect(self._on_check_toggled)
        row.addWidget(self.check)

        self.swatch = QPushButton()
        self.swatch.setFixedSize(_SWATCH_PX + 2, _SWATCH_PX + 2)
        self.swatch.setCursor(QtC.PointingHandCursor)
        self.swatch.setToolTip(
            get_export_copy("widgets.class_list.swatch_tip", tr("Adjust this color."))
        )
        self.swatch.setAccessibleName(self.swatch.toolTip())
        apply_swatch_style(self.swatch, QColor(*self.rgb))
        self.swatch.clicked.connect(self._on_swatch_clicked)
        row.addWidget(self.swatch)

        self.name = QLineEdit(label)
        self.name.setPlaceholderText(
            get_export_copy("widgets.class_list.name_placeholder", tr("Class name"))
        )
        self.name.setToolTip(
            get_export_copy(
                "widgets.class_list.name_field_tip",
                tr("Free-text label written to each polygon's class_name attribute."),
            )
        )



        self.name.setCursorPosition(0)
        self.name.editingFinished.connect(lambda: self.name.setCursorPosition(0))
        row.addWidget(self.name, 1)

        self.coverage = QLabel(
            f"{fraction * 100.0:.0f}%" if fraction is not None else ""
        )
        self.coverage.setToolTip(
            get_export_copy(
                "widgets.class_list.coverage_tip",
                tr("Share of the map covered by this color."),
            )
        )
        self.coverage.setObjectName("classCoverage")
        self.coverage.setMinimumWidth(30)
        self.coverage.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.coverage)
        self._sync_traced()

    def _on_check_toggled(self, _checked: bool) -> None:
        self._sync_traced()
        self.toggled.emit()

    def _sync_traced(self) -> None:

        self.setProperty("traced", "true" if self.check.isChecked() else "false")
        repolish_widget(self)
        repolish_widget(self.name)

    def _on_swatch_clicked(self) -> None:
        chosen = QColorDialog.getColor(
            QColor(*self.rgb),
            self,
            get_export_copy("widgets.class_list.color_dialog_title", tr("Pick color")),
        )
        if not chosen.isValid():
            return
        self.rgb = (chosen.red(), chosen.green(), chosen.blue())
        apply_swatch_style(self.swatch, QColor(*self.rgb))
        self.color_changed.emit()


class ClassListWidget(QWidget):






    classes_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[_ClassRow] = []
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(2)



    def set_classes(self, entries: list[dict]) -> None:


        self.clear()
        labels = self._unique_labels(entries)
        for i, entry in enumerate(entries):
            label = labels[i]
            self._add_row(
                entry["rgb"],
                entry.get("fraction"),
                label,
                checked=not entry.get("is_background", False),
            )

    @staticmethod
    def _unique_labels(entries: list[dict]) -> list[str]:







        guesses = [_capitalized(str(entry.get("label") or "").strip()) for entry in entries]
        totals: dict[str, int] = {}
        for guess in guesses:
            if guess:
                totals[guess] = totals.get(guess, 0) + 1
        seen: dict[str, int] = {}
        unnamed = 0
        out: list[str] = []
        for guess in guesses:
            if not guess:
                unnamed += 1
                out.append(_class_number_name(unnamed))
                continue
            if totals[guess] == 1:
                out.append(guess)
                continue
            seen[guess] = seen.get(guess, 0) + 1
            out.append(tr("{label} {n}").format(label=guess, n=seen[guess]))
        return out

    def _next_class_name(self) -> str:


        taken = {row.name.text().strip() for row in self._rows}
        n = 1
        while _class_number_name(n) in taken:
            n += 1
        return _class_number_name(n)

    def clear(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []

    def add_class(self, rgb: tuple[int, int, int], label: str = "",
                  checked: bool = True) -> str:



        existing = self._nearest_row(rgb, get_export_dial("vectorize.same_class_l1", _SAME_CLASS_L1))
        if existing is not None:
            existing.check.setChecked(checked)
            if label:
                existing.name.setText(label)
                existing.name.setCursorPosition(0)
            self.classes_changed.emit()
            return existing.name.text().strip() or _class_number_name(
                self._rows.index(existing) + 1
            )
        row = self._add_row(
            rgb, None, label or self._next_class_name(),
            checked=checked,
        )
        row.setVisible(True)
        self.classes_changed.emit()
        return ""

    def ensure_class(self, rgb: tuple[int, int, int], label: str = "") -> None:


        existing = self._nearest_row(rgb, get_export_dial("vectorize.same_class_l1", _SAME_CLASS_L1) * 2)
        if existing is not None:
            existing.check.blockSignals(True)
            existing.check.setChecked(True)
            existing.check.blockSignals(False)
            existing._sync_traced()
            if label:
                existing.name.setText(label)
                existing.name.setCursorPosition(0)
            self.classes_changed.emit()
            return
        self._add_row(
            rgb, None, label or self._next_class_name(),
            checked=True,
        )
        self.classes_changed.emit()



    def selected_classes(self) -> list[dict]:

        out = []
        for i, row in enumerate(self._rows):
            if not row.check.isChecked():
                continue
            label = row.name.text().strip() or _class_number_name(i + 1)
            out.append({"rgb": row.rgb, "label": label})
        return out

    def competitor_colors(self) -> list[tuple[int, int, int]]:

        return [row.rgb for row in self._rows if not row.check.isChecked()]

    def count(self) -> int:
        return len(self._rows)

    def has_checked_class(self) -> bool:

        return any(row.check.isChecked() for row in self._rows)

    def selection_signature(self) -> tuple:

        return tuple(
            (row.rgb, row.name.text().strip())
            for row in self._rows
            if row.check.isChecked()
        )



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
