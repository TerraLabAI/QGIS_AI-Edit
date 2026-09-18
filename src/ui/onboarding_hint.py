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

import html
import os
import weakref

from qgis.PyQt.QtCore import QSettings, QSize, pyqtSignal
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
from ..core.config_store import get_export_copy, get_export_dial_list
from ..core.i18n import tr

# Computed here rather than imported from .dock.style: importing that module
# runs the dock package, whose widget reaches back into this one for BLUE_TINT
# before this file has defined it, and the whole cycle fails on unload.
ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "resources",
    "icons",
)

_SETTINGS_PREFIX = "AIEdit/hints/"

# Hint ids. Listed here so settings can reset them all at once.
HINT_LIBRARY_INTRO = "library_intro"
HINT_MARKUP = "flow_markup"
HINT_VECTORIZE = "flow_vectorize"
# Reference panel's one-line "what references are" note, same role as the
# Mark up panel's hint: concise, closable, restorable from Account Settings.
HINT_REFERENCE = "flow_reference"
# Post-sign-in first-steps banner pointing at the step-by-step guide.
HINT_FIRST_STEPS = "first_steps"
# Reference + Markup discoverability tip, shown under the prompt input once
# the first zone is drawn. Closing it, attaching a reference or touching
# markup retires it for the rest of the QGIS session only: it is back on the
# first zone of the next session (see SESSION_ONLY_HINTS).
HINT_GUIDE_AI = "guide_ai"
# One-line AI Segmentation cross-promo inside the Vectorize CTA card, shown
# on segmentation-style results (the moment outline quality is being judged).
HINT_SEG_CROSS = "seg_cross_promo"
# "Name your marks in the prompt" tip, shown under the prompt input while
# saved Mark up strokes exist for the current zone. First-time markers come
# back from Mark up with no idea the marks must be referenced in the prompt
# to matter, so this rides the same slot as HINT_GUIDE_AI and outranks it
# (markup is the more specific state). Session-only, and a Generate click
# with marks present retires it too (see DockPromptMixin).
HINT_MARKUP_PROMPT = "markup_prompt_tip"
ALL_HINTS = [
    HINT_LIBRARY_INTRO, HINT_MARKUP, HINT_VECTORIZE, HINT_FIRST_STEPS,
    HINT_GUIDE_AI, HINT_SEG_CROSS, HINT_MARKUP_PROMPT, HINT_REFERENCE,
]

# Hints whose dismissal lasts one QGIS session instead of for good. Reference
# and Mark up are the two features that decide whether a generation lands, and
# a user who saw the tip in March does not remember it in July, so these come
# back on the first zone of every session (Yvann 2026-07-31). Nothing is
# written to QSettings for these: the set below IS the memory, and it dies
# with the process.
# The two panel hints joined them once they started carrying the link to the
# guide (Yvann 2026-08-08): a hint closed for good takes the only in-context
# way to reach the tutorial down with it, and these are the two panels people
# open then leave without using (435 open Mark up, ~2% mark anything).
SESSION_ONLY_HINTS = frozenset({
    HINT_GUIDE_AI, HINT_MARKUP_PROMPT, HINT_MARKUP, HINT_REFERENCE,
})
_SESSION_DISMISSED: set[str] = set()


# Step-by-step written guide: base URL comes from activation_manager's
# get_guide_url() (server override, shipped constant fallback); every
# touchpoint derives its own variant via guide_url(<utm_content>).

# Card tints as RGB components: the TerraLab leaf green is the default (matches
# the rest of the AI Edit dock chrome); blue is available for automatic-style
# callouts to stay consistent with the sibling plugin.
GREEN_TINT = (139, 172, 39)
BLUE_TINT = (25, 118, 210)
# Discreet neutral grey for low-key guidance (the guide tip): a quiet card
# that does not shout for attention (Yvann 2026-07-08).
NEUTRAL_TINT = (128, 132, 138)

# Live hint widgets, so "Show guidance tips again" can re-show them without a
# dock rebuild. This includes compact, permanent guidance labels as well as
# DismissibleHint cards. Weak refs: closing/destroying a widget drops out on
# its own.
_LIVE_HINTS: list[weakref.ref[QWidget]] = []

# Sentinel href for a hint's inline guide link. Not a URL: the widget only
# needs to know that ITS link was clicked, and each panel maps that to its own
# touchpoint id.
GUIDE_LINK_HREF = "terralab:guide"


def build_guide_link_html(text: str) -> str:
    """The inline "learn more" anchor for a hint body.

    Brand blue, no underline: the footer-links pattern of the design system.
    The href is a sentinel, never a URL - the widget answers ``linkActivated``
    and the real address is resolved at click time by ``open_guide``, so the
    server can move the target without a plugin release. The arrow glyph is
    The arrow glyph is concatenated OUTSIDE ``tr()`` so translators only
    ever see words.

    ``LINK_INK`` is imported here, not at module scope: importing the dock
    package at import time closes the cycle described above ICONS_DIR.
    """
    from .dock.design_tokens import LINK_INK

    return (
        f'<a href="{GUIDE_LINK_HREF}" style="color: {LINK_INK};'
        f' text-decoration: none;">{html.escape(text)} ↗</a>'
    )


# Section anchors of the written guide, so a panel's link lands on its own
# chapter instead of the top of a long page. The value is the heading id
# rehype-slug writes for that chapter (github-slugger over the heading text),
# and it is the same in every locale because the feature names are not
# translated in the guide ("## Mark up" in all twelve files).
GUIDE_ANCHOR_MARKUP = "mark-up"
# "## Reference images and layers". This heading IS translated, so the id
# matches the English guide only; on a localised page the browser finds no
# such element and opens it at the top, which is exactly where the link used
# to land. Nothing breaks, the English reader gains the jump.
GUIDE_ANCHOR_REFERENCE = "reference-images-and-layers"


def guide_url(content: str, anchor: str = "") -> str:
    """Written-guide URL with the shared UTM stem and a per-touchpoint content.

    ``anchor`` is an optional section id (``GUIDE_ANCHOR_*``), appended last:
    a fragment has to follow the query string or the browser reads it as part
    of the last parameter.
    """
    from ..core.auth.activation_manager import get_guide_url

    base = get_guide_url()
    sep = "&" if "?" in base else "?"
    url = (
        f"{base}{sep}utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit"
        f"&utm_content={content}"
    )
    return f"{url}#{anchor}" if anchor else url


def open_guide(content: str, anchor: str = "") -> None:
    """Open the written guide in the system browser and record the open.

    ``content`` is the touchpoint id (also the utm_content and telemetry
    source): footer_tutorial, post_signin, ... ``anchor`` scrolls the page to
    one chapter. The URL always opens, even if telemetry is disabled or
    unavailable.
    """
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(guide_url(content, anchor)))
    try:
        from ..core import telemetry
        from ..core import telemetry_events as te
        telemetry.track(te.TUTORIAL_OPENED, {"tutorial_source": content})
    except Exception:  # nosec B110  Telemetry is best-effort, never blocks the open.
        pass


def is_hint_dismissed(hint_id: str) -> bool:
    """True when the user closed the hint, or when the server suppresses it.

    The served list (hints.suppressed) is an additive union, so it is only ever
    an extra reason to HIDE: one deploy retires a hint that nags or reads wrong
    on every install, whatever plugin version it runs, and a deploy can never
    make a hint the user dismissed come back.

    A session-only hint reads its dismissal from memory, never from QSettings,
    so a value an older version stored under the same key is ignored and the
    hint comes back."""
    if hint_id in SESSION_ONLY_HINTS:
        if hint_id in _SESSION_DISMISSED:
            return True
    elif bool(QSettings().value(_SETTINGS_PREFIX + hint_id, False, type=bool)):
        return True
    return hint_id in get_export_dial_list("hints.suppressed", ())


def dismiss_hint(hint_id: str) -> None:
    if hint_id in SESSION_ONLY_HINTS:
        _SESSION_DISMISSED.add(hint_id)
        return
    QSettings().setValue(_SETTINGS_PREFIX + hint_id, True)


def reset_hints() -> None:
    """Re-enable every hint so the user sees the guidance again.

    Also re-shows any hint widget currently alive (a dock open behind the
    account dialog), so the change is visible immediately, not only next open.
    """
    s = QSettings()
    for hint_id in ALL_HINTS:
        s.remove(_SETTINGS_PREFIX + hint_id)
    _SESSION_DISMISSED.clear()
    for ref in list(_LIVE_HINTS):
        widget = ref()
        if widget is None:
            _LIVE_HINTS.remove(ref)
            continue
        reshow = getattr(widget, "reshow", None)
        if callable(reshow):
            reshow()
            continue
        hint_id = getattr(widget, "_guidance_hint_id", "")
        widget.setVisible(bool(hint_id) and not is_hint_dismissed(hint_id))


def register_hint_widget(widget: QWidget, hint_id: str) -> None:
    """Apply a hint's visibility policy to a compact guidance widget.

    Some panels keep their one-line guidance directly under the title rather
    than rendering a closable card. Registering that label here gives it the
    same server suppression and live Settings reset behavior as
    :class:`DismissibleHint` without changing its visual treatment.
    """
    widget._guidance_hint_id = hint_id
    widget.setVisible(not is_hint_dismissed(hint_id))
    _LIVE_HINTS.append(weakref.ref(widget))


def _hint_styles() -> dict:
    """The hint card's sheets, AI Agent's card line: surface on the strong
    hairline, 12 px corners, the inks by role. Built on first use, because the
    tokens live in the dock package (see ICONS_DIR for the import cycle)."""
    from .dock import design_tokens as t

    return {
        "card": (
            f"QFrame#hintCard {{ background: {t.SURFACE}; border: 1px solid {t.LINE_STRONG};"
            f" border-radius: {t.RADIUS_BOX}px; }}"
        ),
        "title": (
            f"color: {t.INK}; font-size: {t.FONT_BASE}px; font-weight: 600;"
            " background: transparent; border: none;"
        ),
        "body": (
            f"color: {t.INK_2}; font-size: {t.FONT_BODY}px;"
            " background: transparent; border: none;"
        ),
        "close": (
            "QToolButton { background: transparent; border: 1px solid transparent; padding: 0;"
            f" border-radius: {t.RADIUS_CHIP}px; }}"
            f"QToolButton:hover {{ background: {t.HOVER}; }}"
            f"QToolButton:pressed {{ background: {t.HOVER_ON}; }}"
            f"QToolButton:focus {{ border-color: {t.ACCENT_BORDER}; }}"
        ),
        # The card's one action: a small ghost pill, never a second filled
        # button next to the screen's primary.
        "action": (
            f"QToolButton {{ background: {t.SURFACE}; color: {t.INK};"
            f" border: 1px solid {t.LINE_STRONG}; border-radius: {t.RADIUS_PILL_SMALL}px;"
            f" padding: 0 12px; min-height: {t.BTN_SMALL_PX - 2}px;"
            f" font-size: {t.FONT_BODY}px; font-weight: 500; }}"
            f"QToolButton:hover {{ background: {t.HOVER}; border-color: {t.INK_3}; }}"
            f"QToolButton:pressed {{ background: {t.HOVER_ON}; }}"
            f"QToolButton:focus {{ border-color: {t.ACCENT_BORDER}; }}"
        ),
        "step": (
            f"QFrame {{ background: {t.INSET}; border: 1px solid {t.LINE};"
            f" border-radius: {t.RADIUS_CARD}px; }}"
        ),
        "step_num": (
            f"color: {t.ON_ACCENT}; background: {t.ACCENT}; border-radius: 11px;"
            f" font-size: {t.FONT_BODY}px; font-weight: 700;"
        ),
        "step_title": (
            f"color: {t.INK}; font-size: {t.FONT_BODY}px; font-weight: 600;"
            " background: transparent; border: none;"
        ),
        "step_sub": (
            f"color: {t.INK_2}; font-size: {t.FONT_HINT}px;"
            " background: transparent; border: none;"
        ),
    }


# The card tint -> its category hue (``dock/design_tokens.category_*``):
# green tips are leaf, blue ones sky. The neutral tip keeps the plain surface
# and only its glyph tile takes the leaf hue.
_TINT_CATEGORIES = {GREEN_TINT: "leaf", BLUE_TINT: "sky"}
_HINT_TILE_PX = 22
_HINT_TILE_GLYPH_PX = 14


def _hint_category(tint: tuple[int, int, int]) -> tuple[str, bool]:
    """(hue, whether the card itself is tinted) for a card tint."""
    category = _TINT_CATEGORIES.get(tuple(tint))
    return (category, True) if category else ("leaf", False)


def _hint_card_qss(category: str, tinted: bool) -> str:
    from .dock import design_tokens as t

    base = _hint_styles()["card"]
    if not tinted:
        return base
    return base + (
        f"QFrame#hintCard {{ background: {t.category_tint(category)};"
        f" border-color: {t.category_line(category)}; }}"
    )


def _hint_glyph_tile(parent: QWidget, category: str) -> QLabel:
    """The tip's sparkle in its hue's ink on a small round tile of its tint."""
    from .dock import design_tokens as t
    from .icons import pixmap_for

    tile = QLabel(parent)
    tile.setObjectName("hintTile")
    tile.setFixedSize(_HINT_TILE_PX, _HINT_TILE_PX)
    tile.setAlignment(QtC.AlignCenter)
    tile.setStyleSheet(
        f"QLabel#hintTile {{ background: {t.category_tint(category, strong=True)};"
        f" border: none; border-radius: {_HINT_TILE_PX // 2}px; }}"
    )
    tile.setPixmap(pixmap_for(tile, "sparkles", _HINT_TILE_GLYPH_PX, t.qcolor(t.category_ink(category))))
    return tile


class DismissibleHint(QWidget):
    """A tinted inline guidance callout with an optional step row and link.

    ``steps`` is a list of ``(glyph, title, subtitle)`` tuples rendered as a
    1-2-3 row. ``action_text`` (optional) renders a small link-style button
    whose click emits ``action``. ``link_text`` (optional) appends a quiet
    anchor on its own line under the body, emitting ``link_activated`` - the
    low-key sibling of ``action_text`` for "read more about this feature".
    ``rich_body`` renders the body as HTML,
    for a sentence that shows a UI icon inline. ``visibility_gate`` (optional callable ->
    bool) constrains ``reshow()`` so a guidance reset never flashes a pinned
    banner into a state where it does not belong. Closing the card stores the
    dismissal so it stays hidden until the user resets guidance from settings.
    """

    dismissed = pyqtSignal()
    action = pyqtSignal()
    link_activated = pyqtSignal()

    def __init__(
        self,
        hint_id: str,
        title: str,
        body: str,
        steps: list[tuple[str, str, str]] | None = None,
        action_text: str | None = None,
        rich_body: bool = False,
        visibility_gate=None,
        tint: tuple[int, int, int] | None = None,
        action_color: tuple[int, int, int] | None = None,
        link_text: str | None = None,
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
        category, tinted = _hint_category(tint)
        styles = _hint_styles()
        from .dock.design_tokens import BTN_SMALL_PX, INK_2, qcolor
        from .icons import icon_for

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setObjectName("hintCard")
        card.setAttribute(QtC.WA_StyledBackground, True)
        card.setStyleSheet(_hint_card_qss(category, tinted))
        outer.addWidget(card)

        col = QVBoxLayout(card)
        col.setContentsMargins(12, 10, 8, 12)
        col.setSpacing(4)

        close_btn = QToolButton(card)
        close_btn.setIcon(icon_for(close_btn, "close", 14, qcolor(INK_2)))
        close_btn.setIconSize(QSize(14, 14))
        close_btn.setAccessibleName(
            get_export_copy("widgets.onboarding_hint.hide_tip_tooltip", tr("Hide this tip")))
        close_btn.setToolTip(
            get_export_copy("widgets.onboarding_hint.hide_tip_tooltip", tr("Hide this tip")))
        close_btn.setCursor(QtC.PointingHandCursor)
        close_btn.setStyleSheet(styles["close"])
        close_btn.setFixedSize(BTN_SMALL_PX, BTN_SMALL_PX)
        close_btn.clicked.connect(self._on_close)

        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet(styles["body"])
        # Explicit, never auto-detected: a body that carries an <img> pointing
        # at a UI button must render, and a plain body must keep showing any
        # bracket or ampersand a translation puts in it.
        as_html = rich_body or bool(link_text)
        body_lbl.setTextFormat(QtC.RichText if as_html else QtC.PlainText)
        if link_text:
            # The body turns into HTML here, so a plain translation has to be
            # escaped first or a stray & or < would eat part of the sentence.
            head_html = body if rich_body else html.escape(body)
            body_lbl.setText(f"{head_html}<br>{build_guide_link_html(link_text)}")
            # The plugin resolves and opens the address itself (telemetry rides
            # along), so Qt must not follow the sentinel href on its own.
            body_lbl.setOpenExternalLinks(False)
            body_lbl.setTextInteractionFlags(QtC.LinksAccessibleByMouse)
            body_lbl.linkActivated.connect(self._on_link_activated)

        act_btn = None
        if action_text:
            act_btn = QToolButton(card)
            act_btn.setText(action_text)
            act_btn.setCursor(QtC.PointingHandCursor)
            # A pill, unmistakably a button and not a hanging bit of text
            # (Yvann 2026-07-08), but a ghost one: the screen keeps a single
            # filled primary. ``action_color`` is accepted and no longer read.
            act_btn.setStyleSheet(styles["action"])
            act_btn.clicked.connect(self.action.emit)

        if title:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            head.addWidget(_hint_glyph_tile(card, category), 0, QtC.AlignVCenter)
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(styles["title"])
            title_lbl.setWordWrap(True)
            head.addWidget(title_lbl, 1, QtC.AlignVCenter)
            head.addWidget(close_btn, 0, QtC.AlignTop)
            col.addLayout(head)
            body_lbl.setContentsMargins(_HINT_TILE_PX + 8, 0, 16, 0)
            col.addWidget(body_lbl)
            if act_btn is not None:
                col.addSpacing(4)
                act_row = QHBoxLayout()
                act_row.setContentsMargins(_HINT_TILE_PX + 8, 0, 0, 0)
                act_row.addWidget(act_btn, 0, QtC.AlignLeft)
                act_row.addStretch(1)
                col.addLayout(act_row)
        else:
            # No title: one compact row - body text, then (optional) action
            # link, then the tiny x. Top-aligned to match the pre-existing
            # no-title hints exactly (markup / vectorize panels).
            # The glyph carries the tint's hue, and the row centres on the
            # close button so a one-line tip has even padding.
            col.setContentsMargins(12, 6, 6, 6)
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            head.addWidget(_hint_glyph_tile(card, category), 0, QtC.AlignVCenter)
            head.addWidget(body_lbl, 1, QtC.AlignVCenter)
            if act_btn is not None:
                head.addWidget(act_btn, 0, QtC.AlignVCenter)
            head.addWidget(close_btn, 0, QtC.AlignVCenter)
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
        styles = _hint_styles()
        box.setStyleSheet(styles["step"])
        h = QHBoxLayout(box)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(9)
        num = QLabel(str(n))
        num.setFixedSize(22, 22)
        num.setAlignment(QtC.AlignCenter)
        num.setStyleSheet(styles["step_num"])
        h.addWidget(num, 0)
        txt = QVBoxLayout()
        txt.setContentsMargins(0, 0, 0, 0)
        txt.setSpacing(1)
        t = QLabel(f"{glyph}  {title}" if glyph else title)
        t.setStyleSheet(styles["step_title"])
        txt.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setWordWrap(True)
            s.setStyleSheet(styles["step_sub"])
            txt.addWidget(s)
        h.addLayout(txt, 1)
        return box

    def reshow(self) -> None:
        """Re-show after a guidance reset, honoring the optional visibility gate.

        A gate that returns False keeps the hint hidden (its owner shows it when
        the relevant screen is next active), so a reset never flashes a pinned
        banner into a state where it does not belong.

        The reset clears the local dismissal before calling this, so the check
        below is the served suppression alone: a hint the server retired stays
        hidden through a reset.
        """
        if is_hint_dismissed(self._hint_id):
            return
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

    def _on_link_activated(self, href: str) -> None:
        """Relay a click on the body's guide anchor, ignoring anything else.

        The href is checked so a translation that smuggles in its own link
        cannot make the card open an address the plugin never chose.
        """
        if href == GUIDE_LINK_HREF:
            self.link_activated.emit()


def search_icon() -> QIcon:
    """Magnifier icon for the prompt-library search field."""
    return QIcon(os.path.join(ICONS_DIR, "search.svg"))
