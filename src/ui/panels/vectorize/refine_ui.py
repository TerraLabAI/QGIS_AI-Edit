"""Refine group construction for the Vectorize panel."""
from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ....core.i18n import tr


class RefineUiMixin:
    """Builds the refine controls group and its spinbox/checkbox rows."""

    def _build_refine_group(self) -> QGroupBox:
        """Refine group - always fully expanded, no disclosure arrow.

        The whole group stays hidden until the first successful Vectorize
        click; once visible, every control is shown at once with no toggle.
        """
        group = QGroupBox(tr("Refine vectorization"))
        group.setCheckable(False)
        group.setStyleSheet(
            "QGroupBox { background-color: transparent; border: none;"
            " border-radius: 0px; margin: 0px; padding: 0px; padding-top: 20px; }"
            "QGroupBox::title { subcontrol-origin: padding;"
            " subcontrol-position: top left; padding: 2px 4px;"
            " background-color: transparent; border: none;"
            " font-weight: bold; }"
        )
        outer = QVBoxLayout(group)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        content.setObjectName("vectorizeRefineContent")
        content.setStyleSheet(
            "QWidget#vectorizeRefineContent {"
            " background-color: rgba(128, 128, 128, 0.08);"
            " border: 1px solid rgba(128, 128, 128, 0.2);"
            " border-radius: 4px; }"
            "QLabel { background: transparent; border: none; }"
        )
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(6)

        def _section(text: str) -> QLabel:
            lbl = QLabel(text.upper())
            lbl.setStyleSheet(
                "font-size: 10px; color: palette(text); font-weight: bold;"
                " background: transparent; border: none;"
                " border-bottom: 1px solid rgba(128, 128, 128, 0.35);"
                " padding: 4px 0px 4px 0px; margin-bottom: 4px;"
                " letter-spacing: 1px;"
            )
            return lbl

        def _spin_row(parent_layout, label_text: str, tip: str,
                      lo: int, hi: int, default: int) -> QSpinBox:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(label_text)
            lab.setStyleSheet("font-size: 11px; color: palette(text);")
            lab.setToolTip(tip)
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(default)
            spin.setMinimumWidth(55)
            spin.setMaximumWidth(70)
            spin.setToolTip(tip)
            row.addWidget(lab)
            row.addStretch()
            row.addWidget(spin)
            parent_layout.addLayout(row)
            return spin

        def _dspin_row(parent_layout, label_text: str, tip: str,
                       lo: float, hi: float, default: float, step: float) -> QDoubleSpinBox:
            """Float spinbox for sub-integer (finer) control."""
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(label_text)
            lab.setStyleSheet("font-size: 11px; color: palette(text);")
            lab.setToolTip(tip)
            spin = QDoubleSpinBox()
            spin.setDecimals(1)
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setValue(default)
            spin.setMinimumWidth(55)
            spin.setMaximumWidth(75)
            spin.setToolTip(tip)
            row.addWidget(lab)
            row.addStretch()
            row.addWidget(spin)
            parent_layout.addLayout(row)
            return spin

        def _check_row(parent_layout, label_text: str, tip: str,
                       default: bool) -> QCheckBox:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(label_text)
            lab.setStyleSheet("font-size: 11px; color: palette(text);")
            lab.setToolTip(tip)
            chk = QCheckBox()
            chk.setChecked(default)
            chk.setToolTip(tip)
            row.addWidget(lab)
            row.addStretch()
            row.addWidget(chk)
            parent_layout.addLayout(row)
            return chk

        refine_hint = QLabel(
            "ⓘ " + tr("Adjustments re-run instantly and update the same layer.")
        )
        refine_hint.setWordWrap(True)
        refine_hint.setStyleSheet(
            "font-size: 11px; color: rgba(128,128,128,0.95);"
            " background: transparent; border: none; margin-bottom: 2px;"
        )
        content_layout.addWidget(refine_hint)

        # Detection: how the color is matched. Tolerance is per-channel (0-255),
        # so the range is bounded and every step is meaningful. The color itself
        # is re-picked in the swatch above; changing it re-runs the same way.
        content_layout.addWidget(_section(tr("Detection")))
        self._tolerance_spin = _spin_row(
            content_layout,
            tr("Color tolerance:"),
            tr(
                "How loosely the picked color is matched against the white "
                "background. Each pixel goes to whichever is closer, so edges stay "
                "clean even when the model's color drifts. Higher catches more "
                "shades of the picked color; lower keeps only the purest ones."
            ),
            0, 255, 90,
        )
        self._sieve_spin = _spin_row(
            content_layout,
            tr("Remove speckle:"),
            tr("Drop connected blobs smaller than this many pixels before tracing."),
            0, 2000, 10,
        )

        content_layout.addWidget(_section(tr("Outline")))
        self._simplify_spin = _dspin_row(
            content_layout,
            tr("Simplify outline:"),
            tr("Reduce small variations in the outline (0 = no change)."),
            0.0, 50.0, 1.0, 0.5,
        )
        self._round_corners_check = _check_row(
            content_layout,
            tr("Round corners:"),
            tr(
                "Round corners for natural shapes like trees and bushes. "
                "Increase 'Simplify outline' for smoother results."
            ),
            default=False,
        )

        content_layout.addWidget(_section(tr("Selection")))
        self._expand_spin = _spin_row(
            content_layout,
            tr("Expand/Contract:"),
            tr("Positive = expand outward, Negative = shrink inward"),
            -1000, 1000, 0,
        )
        self._expand_spin.setSuffix(" px")
        self._fill_holes_check = _check_row(
            content_layout,
            tr("Fill holes:"),
            tr("Fill interior holes in the selection"),
            default=False,
        )
        self._min_pixels_spin = _spin_row(
            content_layout,
            tr("Min polygon size:"),
            tr(
                "Drop polygons smaller than this many pixels after tracing. "
                "Useful for cleaning up speckle that the sieve missed."
            ),
            0, 100000, 50,
        )
        self._min_pixels_spin.setSuffix(" px")

        outer.addWidget(content)

        # Every refine control re-runs the vectorize (debounced) on change.
        for spin in (
            self._tolerance_spin,
            self._sieve_spin,
            self._simplify_spin,
            self._expand_spin,
            self._min_pixels_spin,
        ):
            spin.valueChanged.connect(self._on_refine_changed)
        for chk in (self._round_corners_check, self._fill_holes_check):
            chk.stateChanged.connect(self._on_refine_changed)

        return group

    def _reset_refine_spinboxes(self) -> None:
        for spin, default in (
            (self._tolerance_spin, 90),
            (self._sieve_spin, 10),
            (self._simplify_spin, 1),
            (self._expand_spin, 0),
            (self._min_pixels_spin, 50),
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
