"""The generation loader: AI Segmentation's run line on AI Edit's tokens.

One 20 px row above an 8 px bar (Yvann, 2026-09-18): the 3 by 3 grid of dots
AI Segmentation and AI Agent pulse while they work, one factual phase line
("Sending your image to the AI"), the time since Generate and the percent.
The bar's fill slides from the run's sky to the done green as it advances.

The percent and the colour follow the bar's own ``valueChanged``, so the
state machine keeps driving ``bar.setValue`` and never has to know about
either. Every timer stops while the loader is hidden, and a paintEvent here
never raises: an exception there takes QGIS down.
"""
from __future__ import annotations

import math
import time

from qgis.PyQt.QtCore import QRectF, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import design_tokens as tokens

LOADER_GRID_PX = 15
LOADER_ROW_PX = 20
_DOT_PX = 4.0
_DOT_GAP = 1.5
_DOT_RADIUS = 1.0
# One dot's pulse, and the stagger that makes the grid ripple from the centre
# column outwards (AI Agent's Loading State, Dots).
_DOT_CYCLE_S = 0.65
_DOT_REST = 0.15
_DOT_DELAYS = ((0.09, 0.18, 0.27), (0.0, 0.09, 0.18), (0.09, 0.18, 0.27))
_DOT_TICK_MS = 40
_CLOCK_TICK_MS = 100


def _ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def loader_dot_opacity(phase: float) -> float:
    """A dot's opacity at ``phase`` in [0, 1): rest, up, full, down, rest."""
    phase %= 1.0
    if phase < 0.18:
        return _DOT_REST + (1.0 - _DOT_REST) * _ease_in_out(phase / 0.18)
    if phase < 0.42:
        return 1.0
    if phase < 0.62:
        return 1.0 - (1.0 - _DOT_REST) * _ease_in_out((phase - 0.42) / 0.20)
    return _DOT_REST


def format_loader_elapsed(seconds) -> str:
    """``1.6s`` under a minute, ``1m 05s`` past it."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError, OverflowError):
        seconds = 0.0
    if math.isnan(seconds) or math.isinf(seconds):
        seconds = 0.0
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m {int(seconds - minutes * 60):02d}s"


class LoaderDotsGrid(QWidget):
    """The 3 by 3 grid of dots in the run's sky, pulsing one after the other."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._started = time.monotonic()
        self._ink = tokens.qcolor(tokens.category_ink(tokens.PROGRESS_HUE_START))
        self._timer = QTimer(self)
        self._timer.setInterval(_DOT_TICK_MS)
        self._timer.timeout.connect(self.update)
        self.setFixedSize(LOADER_GRID_PX, LOADER_GRID_PX)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def start(self) -> None:
        self._running = True
        self._started = time.monotonic()
        self._sync_timer()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self.update()

    def _sync_timer(self) -> None:
        if self._running and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event):  # noqa: N802 - Qt override
        self._timer.stop()
        super().hideEvent(event)

    def _dot_alpha(self, row: int, col: int) -> float:
        if not self._timer.isActive():
            return 1.0 if (row, col) == (1, 1) else _DOT_REST
        since = time.monotonic() - self._started
        return loader_dot_opacity((since - _DOT_DELAYS[row][col]) / _DOT_CYCLE_S)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            step = _DOT_PX + _DOT_GAP
            for row in range(3):
                for col in range(3):
                    colour = QColor(self._ink)
                    colour.setAlphaF(self._dot_alpha(row, col))
                    painter.setBrush(colour)
                    painter.drawRoundedRect(
                        QRectF(col * step, row * step, _DOT_PX, _DOT_PX),
                        _DOT_RADIUS, _DOT_RADIUS)
            painter.end()
        except Exception:  # noqa: BLE001 - paint must never raise
            return


class LoaderElapsedClock(QLabel):
    """The time since Generate, in the second ink, right aligned so the
    tenths ticking never shift the row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet(
            f"font-size: {tokens.FONT_HINT}px; color: {tokens.INK_2};"
            " background: transparent; border: none;"
        )
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance("59.9s") + 2)
        self._started = time.monotonic()
        self._frozen: float | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(_CLOCK_TICK_MS)
        self._timer.timeout.connect(self._tick_clock)
        self._tick_clock()

    def restart_clock(self) -> None:
        self._started = time.monotonic()
        self._frozen = None
        self._tick_clock()
        if self.isVisible():
            self._timer.start()

    def freeze_clock(self) -> None:
        self._timer.stop()
        if self._frozen is None:
            self._frozen = max(0.0, time.monotonic() - self._started)
        self.setText(format_loader_elapsed(self._frozen))

    def _tick_clock(self) -> None:
        if self._frozen is None:
            self.setText(format_loader_elapsed(time.monotonic() - self._started))

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._frozen is None:
            self._tick_clock()
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802 - Qt override
        self._timer.stop()
        super().hideEvent(event)


class GenerationProgressLoader(QWidget):
    """Dots, phase, clock and percent on one row, the coloured bar under it.

    ``bar`` is the QProgressBar the state machine drives (0 to 100);
    ``label`` is the phase line, written through ``set_phase_text``.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.dots = LoaderDotsGrid(self)
        row.addWidget(self.dots, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = QLabel("", self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        # The words give way, never the clock or the percent: elided to the
        # room left, the full line in the tooltip.
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.label.setStyleSheet(
            f"font-size: {tokens.FONT_BODY}px; font-weight: 500; color: {tokens.INK};"
            " background: transparent; border: none;"
        )
        row.addWidget(self.label, 1)
        self.clock = LoaderElapsedClock(self)
        row.addWidget(self.clock, 0, Qt.AlignmentFlag.AlignVCenter)
        self.percent = QLabel("", self)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.percent.setStyleSheet(
            f"font-size: {tokens.FONT_BODY}px; font-weight: 600; color: {tokens.INK};"
            " background: transparent; border: none;"
        )
        self.percent.setMinimumWidth(self.percent.fontMetrics().horizontalAdvance("100%") + 2)
        row.addWidget(self.percent, 0, Qt.AlignmentFlag.AlignVCenter)
        row_host = QWidget(self)
        row_host.setLayout(row)
        row_host.setFixedHeight(LOADER_ROW_PX)
        column.addWidget(row_host)

        self.bar = QProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self._bar_fill = ""
        self.bar.valueChanged.connect(self._on_bar_value)
        column.addWidget(self.bar)

        self._full_text = ""
        self.set_phase_text(text)
        self._on_bar_value(self.bar.value())

    def set_phase_text(self, text: str) -> None:
        # No trailing ellipsis: the pulsing dots and the clock say it runs.
        self._full_text = (text or "").rstrip(" .…")
        self._elide_label()

    def phase_text(self) -> str:
        return self._full_text

    def start_run(self) -> None:
        """A new generation: clock from zero, dots pulsing."""
        self.clock.restart_clock()
        self.dots.start()

    def stop_run(self) -> None:
        self.dots.stop()
        self.clock.freeze_clock()

    def _elide_label(self) -> None:
        width = max(0, self.label.width())
        metrics = self.label.fontMetrics()
        shown = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width) if width else self._full_text
        self.label.setText(shown)
        self.label.setToolTip(self._full_text if shown != self._full_text else "")

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide_label()

    def _on_bar_value(self, value: int) -> None:
        span = max(1, self.bar.maximum() - self.bar.minimum())
        ratio = max(0.0, min(1.0, (value - self.bar.minimum()) / span))
        self.percent.setText(f"{int(round(ratio * 100))}%")
        fill = tokens.progress_fill_color(ratio)
        # Restyle only when the colour itself moves.
        if fill != self._bar_fill:
            self._bar_fill = fill
            self.bar.setStyleSheet(tokens.progress_bar_qss(ratio))


__all__ = [
    "GenerationProgressLoader",
    "LoaderDotsGrid",
    "LoaderElapsedClock",
    "format_loader_elapsed",
    "loader_dot_opacity",
]
