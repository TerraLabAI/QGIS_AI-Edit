
from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ....core import qt_compat as QtC
from ....core.config_store import get_export_copy
from ....core.i18n import tr
from ...dock import design_tokens as T
from ...panel_helpers import (
    FIELD_LABEL_QSS,
    PanelSection,
    check_box_qss,
    spin_box_qss,
)





_REFINE_TOLERANCE_DEFAULT = 90
_REFINE_SIMPLIFY_DEFAULT = 1.0
_REFINE_SIEVE_DEFAULT = 10
_REFINE_MIN_PIXELS_DEFAULT = 50




_SPIN_MIN_PX = 112





_STEP_LABEL_QSS = (
    f"font-size: {T.FONT_HINT}px; font-weight: 600; color: {T.INK_2};"
    " background: transparent; border: none; padding: 6px 0px 0px 0px;"
)


class _ClickableFieldLabel(QLabel):


    def __init__(self, text: str, check: QCheckBox) -> None:
        super().__init__(text)
        self._check = check
        self.setCursor(QtC.PointingHandCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._check.isEnabled():
            self._check.toggle()
        super().mouseReleaseEvent(event)


class RefineUiMixin:


    def _build_refine_group(self) -> PanelSection:






        group = PanelSection(get_export_copy("widgets.refine_ui.refine_group_title", tr("Refine")))
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(T.SPACE_CARD)
        group.body.addLayout(content_layout)
        spin_qss = spin_box_qss()

        def _section(text: str) -> QLabel:

            lbl = QLabel(text)
            lbl.setIndent(0)
            lbl.setStyleSheet(_STEP_LABEL_QSS)
            return lbl

        def _field_label(text: str, tip: str) -> QLabel:


            lab = QLabel(text.rstrip(" :"))
            lab.setStyleSheet(FIELD_LABEL_QSS)
            lab.setToolTip(tip)
            return lab

        def _spin_row(parent_layout, label_text: str, tip: str,
                      lo: int, hi: int, default: int) -> QSpinBox:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(T.SPACE_OUTER)
            lab = _field_label(label_text, tip)
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(default)


            spin.setMinimumWidth(_SPIN_MIN_PX)
            spin.setToolTip(tip)
            spin.setStyleSheet(spin_qss)
            row.addWidget(lab)
            row.addStretch()
            row.addWidget(spin)
            parent_layout.addLayout(row)
            spin.row_label = lab
            return spin

        def _dspin_row(parent_layout, label_text: str, tip: str,
                       lo: float, hi: float, default: float, step: float) -> QDoubleSpinBox:

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(T.SPACE_OUTER)
            lab = _field_label(label_text, tip)
            spin = QDoubleSpinBox()
            spin.setDecimals(1)
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setValue(default)
            spin.setMinimumWidth(_SPIN_MIN_PX)
            spin.setToolTip(tip)
            spin.setStyleSheet(spin_qss)
            row.addWidget(lab)
            row.addStretch()
            row.addWidget(spin)
            parent_layout.addLayout(row)
            return spin

        def _check_row(parent_layout, label_text: str, tip: str,
                       default: bool) -> QCheckBox:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(T.SPACE_OUTER)
            chk = QCheckBox()


            lab = _ClickableFieldLabel(label_text.rstrip(" :"), chk)
            lab.setStyleSheet(FIELD_LABEL_QSS)
            lab.setToolTip(tip)
            chk.setChecked(default)
            chk.setToolTip(tip)
            chk.setAccessibleName(lab.text())


            chk.setStyleSheet(check_box_qss() + "QCheckBox { spacing: 0px; }")
            row.addWidget(lab)
            row.addStretch()
            row.addWidget(chk)
            parent_layout.addLayout(row)
            return chk



        content_layout.addWidget(
            _section(get_export_copy("widgets.refine_ui.detection_section", tr("Detection")))
        )
        self._tolerance_spin = _spin_row(
            content_layout,
            get_export_copy("widgets.refine_ui.color_tolerance_label", tr("Color tolerance:")),
            get_export_copy(
                "widgets.refine_ui.color_tolerance_tip_short",
                tr("How far a pixel's color may drift from its class. Higher takes in noisy shades."),
            ),
            0, 255, _REFINE_TOLERANCE_DEFAULT,
        )

        content_layout.addWidget(
            _section(get_export_copy("widgets.refine_ui.outline_section", tr("Outline")))
        )
        self._simplify_spin = _dspin_row(
            content_layout,
            get_export_copy("widgets.refine_ui.simplify_outline_label", tr("Simplify outline:")),
            get_export_copy(
                "widgets.refine_ui.simplify_outline_tip",
                tr("Reduce small variations in the outline (0 = no change)."),
            ),
            0.0, 50.0, _REFINE_SIMPLIFY_DEFAULT, 0.5,
        )
        self._round_corners_check = _check_row(
            content_layout,
            get_export_copy("widgets.refine_ui.round_corners_label", tr("Round corners:")),
            get_export_copy(
                "widgets.refine_ui.round_corners_tip_short",
                tr("Softer outlines for natural shapes like trees."),
            ),
            default=False,
        )
        self._expand_spin = _spin_row(
            content_layout,
            get_export_copy("widgets.refine_ui.expand_contract_label", tr("Expand/Contract:")),
            get_export_copy(
                "widgets.refine_ui.expand_contract_tip_signed",
                tr("Above 0 grows every shape outward, below 0 shrinks it inward."),
            ),
            -1000, 1000, 0,
        )
        self._expand_spin.setSuffix(" px")

        content_layout.addWidget(
            _section(get_export_copy("widgets.refine_ui.cleanup_section", tr("Cleanup")))
        )
        self._sieve_spin = _spin_row(
            content_layout,
            get_export_copy("widgets.refine_ui.remove_speckle_label", tr("Remove speckle:")),
            get_export_copy(
                "widgets.refine_ui.remove_speckle_tip",
                tr("Drop connected blobs smaller than this many pixels before tracing."),
            ),
            0, 2000, _REFINE_SIEVE_DEFAULT,
        )

        self._sieve_spin.setSuffix(" px")



        self._sieve_spin.setVisible(False)
        self._sieve_spin.row_label.setVisible(False)
        self._fill_holes_check = _check_row(
            content_layout,
            get_export_copy("widgets.refine_ui.fill_holes_label", tr("Fill holes:")),
            get_export_copy(
                "widgets.refine_ui.fill_holes_tip_covered",
                tr("Fill the holes inside each shape, except where another checked class sits."),
            ),
            default=False,
        )
        self._min_pixels_spin = _spin_row(
            content_layout,
            get_export_copy("widgets.refine_ui.min_polygon_size_label", tr("Min polygon size:")),
            get_export_copy(
                "widgets.refine_ui.min_polygon_size_tip_merge",
                tr("Shapes smaller than this join the class around them, so no hole is left."),
            ),
            0, 100000, _REFINE_MIN_PIXELS_DEFAULT,
        )
        self._min_pixels_spin.setSuffix(" px")



        spins = (
            self._tolerance_spin,
            self._sieve_spin,
            self._simplify_spin,
            self._expand_spin,
            self._min_pixels_spin,
        )
        column_px = max([_SPIN_MIN_PX] + [spin.sizeHint().width() for spin in spins])
        for spin in spins:
            spin.setMinimumWidth(column_px)


        for spin in spins:
            spin.valueChanged.connect(self._on_refine_changed)
        for chk in (self._round_corners_check, self._fill_holes_check):
            chk.stateChanged.connect(self._on_refine_changed)



        reset_row = QHBoxLayout()
        reset_row.setContentsMargins(0, 0, 0, 0)
        reset_row.addStretch(1)
        self._refine_reset_btn = QPushButton(
            get_export_copy("widgets.refine_ui.reset_button", tr("Reset settings"))
        )


        self._refine_reset_btn.setStyleSheet(
            T.BTN_LINK_QSS + f"QPushButton:disabled {{ color: {T.INK_3}; }}"
        )
        self._refine_reset_btn.setCursor(QtC.PointingHandCursor)
        self._refine_reset_btn.setAutoDefault(False)
        self._refine_reset_btn.setEnabled(False)
        self._refine_reset_btn.clicked.connect(self._on_refine_reset_clicked)
        reset_row.addWidget(self._refine_reset_btn)
        content_layout.addLayout(reset_row)
        for spin in spins:
            spin.valueChanged.connect(self._sync_refine_reset)
        for chk in (self._round_corners_check, self._fill_holes_check):
            chk.stateChanged.connect(self._sync_refine_reset)

        return group

    def _refine_at_defaults(self) -> bool:
        return (
            self._tolerance_spin.value() == _REFINE_TOLERANCE_DEFAULT
            and self._sieve_spin.value() == _REFINE_SIEVE_DEFAULT
            and abs(self._simplify_spin.value() - _REFINE_SIMPLIFY_DEFAULT) < 1e-9
            and self._expand_spin.value() == 0
            and self._min_pixels_spin.value() == _REFINE_MIN_PIXELS_DEFAULT
            and not self._round_corners_check.isChecked()
            and not self._fill_holes_check.isChecked()
        )

    def _sync_refine_reset(self, *_args) -> None:
        self._refine_reset_btn.setEnabled(not self._refine_at_defaults())

    def _on_refine_reset_clicked(self) -> None:

        if self._refine_at_defaults():
            return
        self._reset_refine_spinboxes()
        self._on_refine_changed()

    def _reset_refine_spinboxes(self) -> None:
        tolerance_default = _REFINE_TOLERANCE_DEFAULT
        sieve_default = _REFINE_SIEVE_DEFAULT
        simplify_default = _REFINE_SIMPLIFY_DEFAULT
        min_pixels_default = _REFINE_MIN_PIXELS_DEFAULT
        for spin, default in (
            (self._tolerance_spin, tolerance_default),
            (self._sieve_spin, sieve_default),
            (self._simplify_spin, simplify_default),
            (self._expand_spin, 0),
            (self._min_pixels_spin, min_pixels_default),
        ):
            spin.blockSignals(True)
            spin.setValue(default)
            spin.blockSignals(False)
        for chk, default in (
            (self._round_corners_check, False),
            (self._fill_holes_check, False),
        ):
            chk.blockSignals(True)
            chk.setChecked(default)
            chk.blockSignals(False)
        self._sync_refine_reset()
