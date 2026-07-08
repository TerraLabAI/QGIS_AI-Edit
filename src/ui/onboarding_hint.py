"""Dismissible in-UI guidance hints.

A new GIS user who is not AI-savvy needs to be told, in plain words, what each
step of the flow does. These hints are non-blocking inline callouts: shown once,
closed for good with the x, and re-enabled from Account Settings ("Show guidance
tips again"). One hint per screen at most, so guidance never becomes clutter.

Pattern (how mature apps do onboarding without burdening the UI):
  - inline callout, never a modal that blocks work,
  - dismissible and remembered (QSettings), not nagging,
  - re-showable on demand from settings (live, if the dock is open),
  - short, action-oriented copy with an optional 1-2-3 step row.
"""

from __future__ import annotations

import os
import weakref

from qgis.PyQt.QtCore import QSettings, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import qt_compat as QtC
from ..core.i18n import tr

_SETTINGS_PREFIX = "AIEdit/hints/"

# Hint ids. Listed here so settings can reset them all at once.
HINT_LIBRARY_INTRO = "library_intro"
HINT_ZONE = "flow_zone"
HINT_PROMPT = "flow_prompt"
HINT_TOOLS = "flow_tools"
HINT_MARKUP = "flow_markup"
HINT_VECTORIZE = "flow_vectorize"
# Post-sign-in first-steps banner pointing at the step-by-step guide.
HINT_FIRST_STEPS = "first_steps"
ALL_HINTS = [
    HINT_LIBRARY_INTRO, HINT_ZONE, HINT_PROMPT, HINT_TOOLS,
    HINT_MARKUP, HINT_VECTORIZE, HINT_FIRST_STEPS,
]

_ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "resources", "icons"
)

# Step-by-step written guide. Defined once here; every touchpoint derives its
# own variant via guide_url(<utm_content>) so the base + UTM stem never gets
# copy-pasted.
GUIDE_URL_BASE = "https://terra-lab.ai/blog/ai-edit-complete-guide"

# Card tints as RGB components: the TerraLab leaf green is the default (matches
# the rest of the AI Edit dock chrome); blue is available for automatic-style
# callouts to stay consistent with the sibling plugin.
GREEN_TINT = (139, 172, 39)
BLUE_TINT = (25, 118, 210)

# Live hint widgets, so "Show guidance tips again" can re-show them without a
# dock rebuild. Weak refs: closing/destroying a hint drops out on its own.
_LIVE_HINTS: list[weakref.ref[DismissibleHint]] = []


def guide_url(content: str) -> str:
    """Written-guide URL with the shared UTM stem and a per-touchpoint content."""
    return (
        f"{GUIDE_URL_BASE}"
        "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit"
        f"&utm_content={content}"
    )


def open_guide(content: str) -> None:
    """Open the written guide in the system browser and record the open.

    ``content`` is the touchpoint id (also the utm_content and telemetry
    source): footer_tutorial, post_signin, ... The URL always opens, even if
    telemetry is disabled or unavailable.
    """
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(guide_url(content)))
    try:
        from ..core import telemetry
        from ..core import telemetry_events as te
        telemetry.track(te.TUTORIAL_OPENED, {"tutorial_source": content})
    except Exception:  # nosec B110  Telemetry is best-effort, never blocks the open.
        pass


def is_hint_dismissed(hint_id: str) -> bool:
    return bool(QSettings().value(_SETTINGS_PREFIX + hint_id, False, type=bool))


def dismiss_hint(hint_id: str) -> None:
    QSettings().setValue(_SETTINGS_PREFIX + hint_id, True)


def reset_hints() -> None:
    """Re-enable every hint so the user sees the guidance again.

    Also re-shows any hint widget currently alive (a dock open behind the
    account dialog), so the change is visible immediately, not only next open.
    """
    s = QSettings()
    for hint_id in ALL_HINTS:
        s.remove(_SETTINGS_PREFIX + hint_id)
    for ref in list(_LIVE_HINTS):
        widget = ref()
        if widget is None:
            _LIVE_HINTS.remove(ref)
            continue
        widget.reshow()


def _card_qss(tint: tuple[int, int, int]) -> str:
    r, g, b = tint
    return (
        f"QFrame#hintCard {{ background: rgba({r},{g},{b},0.12); "
        f"border: 1px solid rgba({r},{g},{b},0.38); border-radius: 11px; }}"
    )


_TITLE_STYLE = (
    "color: palette(text); font-size: 14px; font-weight: 800; "
    "background: transparent; border: none;"
)
_BODY_STYLE = (
    "color: palette(text); font-size: 12px; background: transparent; border: none;"
)
_CLOSE_STYLE = (
    "QToolButton { background: transparent; color: rgba(120,124,130,0.95); "
    "border: none; font-size: 16px; font-weight: 700; }"
    "QToolButton:hover { color: palette(text); }"
)
_STEP_STYLE = (
    "QFrame { background: palette(base); border: 1px solid rgba(128,128,128,0.20); "
    "border-radius: 9px; }"
)
_STEP_NUM_STYLE = (
    "color: #14210A; background: #8BAC27; border-radius: 11px; "
    "font-size: 12px; font-weight: 800;"
)
_STEP_TITLE_STYLE = (
    "color: palette(text); font-size: 12px; font-weight: 700; "
    "background: transparent; border: none;"
)
_STEP_SUB_STYLE = (
    "color: rgba(120,124,130,0.95); font-size: 11px; "
    "background: transparent; border: none;"
)


class DismissibleHint(QWidget):
    """A tinted inline guidance callout with an optional step row and link.

    ``steps`` is a list of ``(glyph, title, subtitle)`` tuples rendered as a
    1-2-3 row. ``action_text`` (optional) renders a small link-style button
    whose click emits ``action``. ``visibility_gate`` (optional callable ->
    bool) constrains ``reshow()`` so a guidance reset never flashes a pinned
    banner into a state where it does not belong. Closing the card stores the
    dismissal so it stays hidden until the user resets guidance from settings.
    """

    dismissed = pyqtSignal()
    action = pyqtSignal()

    def __init__(
        self,
        hint_id: str,
        title: str,
        body: str,
        steps: list[tuple[str, str, str]] | None = None,
        action_text: str | None = None,
        visibility_gate=None,
        tint: tuple[int, int, int] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._hint_id = hint_id
        # Optional callable -> bool. When set, a guidance reset only re-shows the
        # hint if the gate allows it. Banners pinned to the dock (always mounted,
        # no hidden parent) use this so "Show guidance again" can never reveal
        # them in the wrong state.
        self._visibility_gate = visibility_gate
        tint = tint or GREEN_TINT

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setObjectName("hintCard")
        card.setStyleSheet(_card_qss(tint))
        outer.addWidget(card)

        col = QVBoxLayout(card)
        col.setContentsMargins(16, 13, 12, 14)
        col.setSpacing(7)

        close_btn = QToolButton(card)
        close_btn.setText("✕")  # x glyph
        close_btn.setToolTip(tr("Got it - hide this tip"))
        close_btn.setCursor(QtC.PointingHandCursor)
        close_btn.setStyleSheet(_CLOSE_STYLE)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self._on_close)

        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet(_BODY_STYLE)

        act_btn = None
        if action_text:
            r, g, b = tint
            act_btn = QToolButton(card)
            act_btn.setText(action_text)
            act_btn.setCursor(QtC.PointingHandCursor)
            act_btn.setStyleSheet(
                f"QToolButton {{ background: transparent; border: none;"
                f" font-size: 12px; font-weight: 700; padding: 0px;"
                f" color: rgb({r},{g},{b}); }}"
            )
            act_btn.clicked.connect(self.action.emit)

        if title:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(_TITLE_STYLE)
            title_lbl.setWordWrap(True)
            head.addWidget(title_lbl, 1)
            head.addWidget(close_btn, 0, QtC.AlignTop)
            col.addLayout(head)
            col.addWidget(body_lbl)
            if act_btn is not None:
                col.addWidget(act_btn, 0, QtC.AlignLeft)
        else:
            # No title: one compact row - body text, then (optional) action
            # link, then the tiny x. Top-aligned to match the pre-existing
            # no-title hints exactly (markup / vectorize panels).
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            head.addWidget(body_lbl, 1, QtC.AlignTop)
            if act_btn is not None:
                head.addWidget(act_btn, 0, QtC.AlignTop)
            head.addWidget(close_btn, 0, QtC.AlignTop)
            col.addLayout(head)

        if steps:
            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 0)
            row.setSpacing(8)
            for i, (glyph, step_title, step_sub) in enumerate(steps, start=1):
                row.addWidget(self._step(i, glyph, step_title, step_sub), 1)
            col.addLayout(row)

        self.setVisible(not is_hint_dismissed(hint_id))
        _LIVE_HINTS.append(weakref.ref(self))

    def _step(self, n: int, glyph: str, title: str, sub: str) -> QFrame:
        box = QFrame(self)
        box.setStyleSheet(_STEP_STYLE)
        h = QHBoxLayout(box)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(9)
        num = QLabel(str(n))
        num.setFixedSize(22, 22)
        num.setAlignment(QtC.AlignCenter)
        num.setStyleSheet(_STEP_NUM_STYLE)
        h.addWidget(num, 0)
        txt = QVBoxLayout()
        txt.setContentsMargins(0, 0, 0, 0)
        txt.setSpacing(1)
        t = QLabel(f"{glyph}  {title}" if glyph else title)
        t.setStyleSheet(_STEP_TITLE_STYLE)
        txt.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setWordWrap(True)
            s.setStyleSheet(_STEP_SUB_STYLE)
            txt.addWidget(s)
        h.addLayout(txt, 1)
        return box

    def reshow(self) -> None:
        """Re-show after a guidance reset, honoring the optional visibility gate.

        A gate that returns False keeps the hint hidden (its owner shows it when
        the relevant screen is next active), so a reset never flashes a pinned
        banner into a state where it does not belong.
        """
        gate = self._visibility_gate
        if gate is not None:
            try:
                if not gate():
                    return
            except Exception:  # nosec B110 -- a broken gate must not block the reset
                pass
        self.show()

    def _on_close(self) -> None:
        dismiss_hint(self._hint_id)
        self.hide()
        self.dismissed.emit()


def search_icon() -> QIcon:
    """Magnifier icon for the prompt-library search field."""
    return QIcon(os.path.join(_ICONS_DIR, "search.svg"))
