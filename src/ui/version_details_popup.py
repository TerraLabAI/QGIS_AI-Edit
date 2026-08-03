"""Version details popup, opened from a version-strip tile's ⓘ.

Split out of ``version_strip.py`` when that file passed the size ceiling.
The seam is the concern: the strip is
a film-strip widget, this is the modal that reads ONE version out loud (its
prompt, its resolution, what it was built from). No image: the canvas already
shows the version full-size.

Mirrors the generation detail dialog so the two read as one design language.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..core import qt_compat as QtC
from ..core.i18n import tr
from .dock.style import BRAND_BLUE, COPY_BTN_QSS, COPY_SVG, FOCUS_RING

# Small uppercase section label, a boxed prompt, and a flat one-click copy.
_STRIP_SECTION_STYLE = (
    "color: rgba(128,128,128,0.95); font-size: 10px; font-weight: 700;"
    " background: transparent; border: none;"
)
_META_STYLE = (
    "color: palette(text); font-size: 13px; font-weight: 700;"
    " background: transparent; border: none;"
)
_PROMPT_BOX_STYLE = (
    "QLabel { color: palette(text); font-size: 12px;"
    " background: rgba(128,128,128,0.05);"
    " border: 1px solid rgba(128,128,128,0.15);"
    " border-radius: 6px; padding: 10px 12px; }"
)
_NOTE_STYLE = (
    "color: rgba(128,128,128,0.95); font-size: 12px;"
    " background: transparent; border: none;"
)
# COPY_BTN_QSS is shared with the generation detail dialog and carries no focus
# state; the ring is appended here so the popup's only action shows where the
# keyboard is.
_COPY_FOCUS_QSS = f"QPushButton:focus {{ border: 1px solid {FOCUS_RING}; }}"


class VersionDetailsPopup(QDialog):
    """Light popup opened from a tile's ⓘ: the prompt + basic info (resolution,
    lineage). No image: the canvas already shows the version full-size."""

    def __init__(
        self,
        label: str,
        definition: str | None,
        base_label: str | None,
        prompt: str,
        is_original: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("Version details"))
        self.setModal(True)
        self._prompt = prompt or ""
        self._copy_btn = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        # Meta line: "V1 · 1K · from Original". Version label bold, lineage in
        # brand blue so the eye reads "what this is" before the prompt.
        parts = [label]
        if not is_original and definition:
            parts.append(definition)
        meta_html = " · ".join(parts)
        if not is_original and base_label:
            from_label = tr("from {base}").format(base=base_label)
            meta_html += f" · <span style='color:{BRAND_BLUE}; font-weight:600;'>{from_label}</span>"
        if is_original:
            meta_html += " · " + tr("clean source")
        meta = QLabel(meta_html, self)
        meta.setTextFormat(Qt.TextFormat.RichText)
        meta.setStyleSheet(_META_STYLE)
        layout.addWidget(meta)

        if self._prompt:
            # Header row: "Prompt" + a tiny one-click Copy prompt button.
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(6)
            section = QLabel(tr("Prompt"), self)
            section.setStyleSheet(_STRIP_SECTION_STYLE)
            head.addWidget(section)
            head.addStretch(1)
            self._copy_btn = QPushButton(tr("Copy prompt"), self)
            self._copy_btn.setIcon(QIcon(COPY_SVG))
            self._copy_btn.setIconSize(QSize(13, 13))
            self._copy_btn.setStyleSheet(COPY_BTN_QSS + _COPY_FOCUS_QSS)
            self._copy_btn.setCursor(QtC.PointingHandCursor)
            self._copy_btn.setFlat(True)
            # The dialog's only action: it has to take focus, or a keyboard user
            # opens this popup and can do nothing in it.
            self._copy_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._copy_btn.clicked.connect(self._copy_prompt)
            head.addWidget(self._copy_btn)
            layout.addLayout(head)

            body = QLabel(self._prompt, self)
            body.setWordWrap(True)
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setStyleSheet(_PROMPT_BOX_STYLE)
            body.setMaximumWidth(360)
            layout.addWidget(body)
        else:
            # Original (or prompt-less) version: a quiet one-line note, no box.
            note = QLabel(
                tr("The original zone, before any AI edit.") if is_original
                else tr("(no prompt)"),
                self,
            )
            note.setWordWrap(True)
            note.setTextFormat(Qt.TextFormat.PlainText)
            note.setStyleSheet(_NOTE_STYLE)
            note.setMaximumWidth(360)
            layout.addWidget(note)

    def _copy_prompt(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._prompt)
        if self._copy_btn is not None:
            self._copy_btn.setText(tr("Copied"))
            QtC.safe_single_shot(1400, self._copy_btn, self._reset_copy_btn)

    def _reset_copy_btn(self) -> None:
        if self._copy_btn is not None:
            self._copy_btn.setText(tr("Copy prompt"))
