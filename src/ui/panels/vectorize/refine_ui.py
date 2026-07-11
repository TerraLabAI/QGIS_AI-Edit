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
)

from ....core.i18n import tr
from ...panel_helpers import GROUP_BOX_QSS


class RefineUiMixin:
    """Builds the refine controls group and its spinbox/checkbox rows."""

    def _build_refine_group(self) -> QGroupBox:
        """Refine group, shown alone once a vectorization succeeds.

        Same bordered group box as Layer / Classes so the two panel states
        read as one surface. Sections follow the pipeline: how pixels match
        (Detection), how edges look (Outline), what gets dropped (Cleanup).
        """
        group = QGroupBox(tr("Refine"))
        group.setStyleSheet(GROUP_BOX_QSS)
        content_layout = QVBoxLayout(group)
        content_layout.setContentsMargins(8, 6, 8, 8)
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

        # Detection: how pixels are matched to classes. Tolerance is
        # per-channel (0-255), so the range is bounded and every step counts.
        content_layout.addWidget(_section(tr("Detection")))
        self._tolerance_spin = _spin_row(
            content_layout,
            tr("Color tolerance:"),
            tr(
                "Each pixel goes to the closest class color, so edges stay "
                "clean even when the model's colors drift. This caps how far a "
                "pixel may sit from its class: higher sweeps in noisy shades, "
                "lower leaves them out of every class."
            ),
            0, 255, 90,
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
        self._expand_spin = _spin_row(
            content_layout,
            tr("Expand/Contract:"),
            tr("Positive = expand outward, Negative = shrink inward"),
            -1000, 1000, 0,
        )
        self._expand_spin.setSuffix(" px")

        content_layout.addWidget(_section(tr("Cleanup")))
        self._sieve_spin = _spin_row(
            content_layout,
            tr("Remove speckle:"),
            tr("Drop connected blobs smaller than this many pixels before tracing."),
            0, 2000, 10,
        )
        self._fill_holes_check = _check_row(
            content_layout,
            tr("Fill holes:"),
            tr("Fill interior holes in each shape"),
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
