"""Tutorials, Keyboard shortcuts, More plugins and the Contact us sheet of the
settings dialog. Every method runs as an ``AccountSettingsDialog`` method.
"""
from __future__ import annotations

import sys

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ....core import telemetry
from ....core import telemetry_events as te
from ....core.auth.activation_manager import build_utm_url, get_contact_call_url, get_support_email, get_tutorial_url
from ....core.config_store import get_export_copy
from ....core.i18n import tr
from ....core.logger import log_warning
from ....core.qt_compat import event_pos
from ...dock.design_tokens import (
    ACCENT_BORDER,
    ACCENT_BORDER_SOFT,
    ACCENT_TINT,
    BTN_GHOST_QSS,
    BTN_PRIMARY_QSS,
    CHIP_PX,
    FIELD,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    INK,
    INK_2,
    INK_3,
    LINE,
    MONO_FAMILY,
    RADIUS_BOX,
    RADIUS_CARD,
    RADIUS_CHIP,
    SURFACE,
    category_ink,
    category_tint,
)
from ...external_url import open_external
from ...sibling_thumbnails import (
    GuideStill,
    ThumbnailLoader,
    cached_thumbnail,
    is_thumbnail_url_usable,
    sibling_logo_path,
)
from .widgets import Page, make_button

_TILE_QSS = (
    f"QFrame#learnTile {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#learnTile:hover {{ background: {ACCENT_TINT}; border-color: {ACCENT_BORDER_SOFT}; }}"
    f"QFrame#learnTile:focus {{ border: 1px solid {ACCENT_BORDER}; }}"
    f"QLabel#learnShot {{ background: {FIELD}; border: none; border-radius: {RADIUS_BOX}px; }}"
    f"QLabel#learnKind {{ font-size: {FONT_HINT}px; font-weight: 600; color: {INK_2}; background: transparent; }}"
    f"QLabel#learnTitle {{ font-size: {FONT_BASE}px; font-weight: 600; color: {INK}; background: transparent; }}"
    f"QLabel#learnNote {{ font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; }}"
)
_SHORTCUTS_QSS = (
    f"QFrame#shCard {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#shRule {{ background: {LINE}; border: none; min-width: 1px; max-width: 1px; }}"
    f"QLabel#shSection {{ font-size: {FONT_BODY}px; font-weight: 600;"
    f" color: {INK_2}; background: transparent; }}"
    f"QLabel#shAction {{ font-size: {FONT_BASE}px; color: {INK}; background: transparent; }}"
    f"QLabel#shNote {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; }}"
    f"QLabel#shKey {{ font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px; color: {INK_2};"
    f" background: {FIELD}; border: 1px solid {LINE}; border-radius: {RADIUS_CHIP}px; padding: 0 8px; }}"
)
_SHOT_PX = 118
_TILE_PAD = 10
_MARK_FILE = "mark@4x.png"
# The social pictures of the two pages the tiles open, 1200 by 630 like the
# guide stills on the More plugins cards, and cached in the same place.
_GUIDE_STILL_URL = "https://terra-lab.ai/blog/ai-edit-complete-guide/og.jpg"
_BLOG_STILL_URL = "https://terra-lab.ai/images/og/blog.jpg"
_CONTACT_TITLE_QSS = f"font-size: {FONT_BASE + 2}px; font-weight: 600; color: {INK}; background: transparent;"
_CONTACT_EMAIL_QSS = f"font-size: {FONT_BASE}px; font-weight: 600; color: {INK}; background: transparent;"
_CONTACT_BODY_QSS = f"font-size: {FONT_BODY + 1}px; color: {INK_2}; background: transparent;"
# The native title bar names the product, like the confirm windows.
PRODUCT_WINDOW_TITLE = "AI Edit"


class LearnTile(QFrame):
    """A tutorial as a tile: a tinted glyph panel, the kind, a title, a line."""

    clicked = pyqtSignal()

    def __init__(self, glyph: str, category: str, kind: str, title: str, note: str, parent=None,
                 image_url: str = ""):
        from ...dock.design_tokens import qcolor
        from ...icons import pixmap_for

        super().__init__(parent)
        self.setObjectName("learnTile")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_TILE_QSS)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title)
        # Reachable from the keyboard like a button: Tab to it, Space or
        # Enter opens it. A mouse-only tile left keyboard users no way in.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        col = QVBoxLayout(self)
        col.setContentsMargins(_TILE_PAD, _TILE_PAD, _TILE_PAD, 14)
        col.setSpacing(6)
        self._still = None
        self._loader = None
        if image_url:
            # The page's own picture, as on the More plugins cards; the banana
            # on a tinted panel stands in until it loads, or if it never does.
            self._still = GuideStill(sibling_logo_path(_MARK_FILE), self)
            col.addWidget(self._still)
            self._load_still(image_url)
        else:
            shot = QLabel(self)
            shot.setObjectName("learnShot")
            shot.setFixedHeight(_SHOT_PX)
            shot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # The topic's hue: the glyph in its ink on its tint.
            shot.setStyleSheet(
                f"QLabel#learnShot {{ background: {category_tint(category)}; border: none;"
                f" border-radius: {RADIUS_BOX}px; }}")
            shot.setPixmap(pixmap_for(shot, glyph, 36, qcolor(category_ink(category))))
            col.addWidget(shot)
        col.addSpacing(4)
        for name, text in (("learnKind", kind), ("learnTitle", title), ("learnNote", note)):
            label = QLabel(text, self)
            label.setObjectName(name)
            label.setWordWrap(True)
            label.setContentsMargins(4, 0, 4, 0)
            col.addWidget(label)
        col.addStretch(1)

    def _load_still(self, url: str) -> None:
        """From the shared cache when it is there, the network once when not."""
        if not is_thumbnail_url_usable(url):
            return
        cached = cached_thumbnail(url)
        if cached is not None:
            self._still.set_image(cached)
            return
        self._loader = ThumbnailLoader(self)
        self._loader.loaded.connect(self._still.set_image)
        self._loader.fetch(url)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self._still is not None:
            self._still.set_width(max(1, event.size().width() - 2 * _TILE_PAD))

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


def _native_key(seq) -> str:
    return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)


def live_tool_keys(window=None) -> dict[str, str]:
    """The keys the dock really bound for Draw, Vectorize and Compare.

    dock/build_result.py picks the first free key of each candidate list
    (input_decisions.SHORTCUT_CANDIDATES), so on a QGIS where Alt+M is taken
    Draw sits on Alt+K. The page printed Alt+M, Alt+V and Alt+B whatever the
    dock had picked. Falls back to the first candidates without a dock.
    """
    from ...input_decisions import pick_dock_shortcuts

    keys = pick_dock_shortcuts(set())
    try:
        from qgis.PyQt.QtWidgets import QDockWidget

        dock = window.findChild(QDockWidget, "AIEditDockWidget") if window is not None else None
    except (RuntimeError, AttributeError, TypeError):
        dock = None
    for action, attr in (("markup", "_markup_shortcut"), ("vectorize", "_vectorize_shortcut"),
                         ("swipe", "_swipe_shortcut")):
        try:
            seq = getattr(dock, attr).key().toString(QKeySequence.SequenceFormat.PortableText)
        except (AttributeError, RuntimeError):
            continue
        if seq:
            keys[action] = seq
    return keys


def shortcut_sections(window=None) -> tuple:
    """AI Edit's whole keyboard map, as two columns of (heading, rows).

    Each row is (action, note, key). The keys are the ones the code binds:
    the launch QShortcut in plugin_parts/lifecycle.py, the dock's Return,
    Enter and Escape shortcuts, the tool keys the dock picked
    (``live_tool_keys``), and the drawing keys of the zone tool.
    """
    from ...plugin_parts.lifecycle import LAUNCH_SHORTCUT

    tool_keys = live_tool_keys(window)

    undo_key = QKeySequence(QKeySequence.StandardKey.Undo).toString(
        QKeySequence.SequenceFormat.NativeText)
    # The key is labelled Enter on Windows and Linux keyboards; only a Mac
    # names it Return. Qt spells and translates either name itself.
    enter_key = _native_key("Return" if sys.platform == "darwin" else "Enter")
    esc_key = "Esc"
    editing = (
        (tr("Launch AI Edit"), "", _native_key(LAUNCH_SHORTCUT)),
        (tr("Generate"), tr("Outside the prompt box"), enter_key),
        (tr("Go back one step"), "", esc_key),
        (tr("Undo"), get_export_copy(
            "dialogs.settings.shortcuts.undo_while_drawing", tr("While drawing")), undo_key),
    )
    tools = (
        (get_export_copy("dialogs.settings.shortcuts.draw", tr("Draw")), "",
         _native_key(tool_keys["markup"])),
        (tr("Vectorize"), "", _native_key(tool_keys["vectorize"])),
        (get_export_copy("dialogs.settings.shortcuts.compare", tr("Compare")), "",
         _native_key(tool_keys["swipe"])),
    )
    drawing = (
        (tr("Close the zone"), "", enter_key),
        (tr("Remove the last point"), "", _native_key("Backspace")),
        (tr("Cancel the zone"), "", esc_key),
    )
    return (
        ((tr("Editing"), editing),),
        ((tr("Tools"), tools), (tr("Drawing a zone"), drawing)),
    )


def build_shortcuts_card(parent, window=None) -> QFrame:
    card = QFrame(parent)
    card.setObjectName("shCard")
    card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    card.setStyleSheet(_SHORTCUTS_QSS)
    columns = QHBoxLayout(card)
    columns.setContentsMargins(16, 14, 16, 14)
    columns.setSpacing(28)
    left, right = shortcut_sections(window)
    columns.addWidget(_shortcut_column(card, left), 1)
    rule = QFrame(card)
    rule.setObjectName("shRule")
    columns.addWidget(rule)
    columns.addWidget(_shortcut_column(card, right), 1)
    return card


def _shortcut_column(parent, sections: tuple) -> QWidget:
    column = QWidget(parent)
    col = QVBoxLayout(column)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(8)
    for index, (heading, rows) in enumerate(sections):
        label = QLabel(heading, column)
        label.setObjectName("shSection")
        label.setContentsMargins(0, 14 if index else 0, 0, 4)
        col.addWidget(label)
        for action, note, key in rows:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            words = QVBoxLayout()
            words.setSpacing(1)
            title = QLabel(action, column)
            title.setObjectName("shAction")
            title.setWordWrap(True)
            words.addWidget(title)
            if note:
                hint = QLabel(note, column)
                hint.setObjectName("shNote")
                hint.setWordWrap(True)
                words.addWidget(hint)
            row.addLayout(words, 1)
            cap = QLabel(key, column)
            cap.setObjectName("shKey")
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setFixedHeight(CHIP_PX)
            # One-glyph keys (Return, Backspace) keep the width of the others.
            cap.setMinimumWidth(CHIP_PX + 12)
            row.addWidget(cap, 0, Qt.AlignmentFlag.AlignTop)
            col.addLayout(row)
    col.addStretch(1)
    return column


class HelpPagesMixin:
    """Tutorials, Keyboard shortcuts, More plugins, Contact us, Report a problem."""

    def _build_tutorials_page(self) -> Page:
        """The tutorial and the written guide, as tiles that open in the browser.

        The dialog stays open: a person who came here to learn comes back to
        the settings they were in the middle of.
        """
        page = Page(tr("Tutorials"), tr("Opens in your browser"), self,
                    glyph="play", category="coral")
        from ...onboarding_hint import guide_url

        tiles = [
            ("play", "coral", tr("Tutorial"), tr("See it in action"),
             tr("From zone to finished edit"), "settings_tutorial", ""),
            ("book", "sky", tr("Guide"), tr("Complete guide"),
             get_export_copy("dialogs.settings.tutorials.guide_note",
                             tr("Prompts, References, Draw, Vectorize")), "settings_guide",
             _GUIDE_STILL_URL),
            ("book", "sky", tr("Blog"), tr("TerraLab blog"),
             tr("Ideas and workflows"), "settings_blog", _BLOG_STILL_URL),
        ]
        urls = {
            "settings_tutorial": get_tutorial_url(),
            "settings_guide": guide_url("settings_guide"),
            "settings_blog": build_utm_url("/blog", "settings_blog"),
        }
        # No served video: the tutorial link is the guide itself, so one tile.
        if urls["settings_tutorial"].split("?")[0] == urls["settings_guide"].split("?")[0]:
            tiles.pop(0)
        holder = QWidget(page.body_widget)
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        for index, (glyph, category, kind, title, note, source, still) in enumerate(tiles):
            tile = LearnTile(glyph, category, kind, title, note, holder, image_url=still)
            tile.clicked.connect(lambda s=source: self._open_learn(s, urls[s]))
            grid.addWidget(tile, 0, index)
            grid.setColumnStretch(index, 1)
        page.add(holder)
        return page

    @staticmethod
    def _open_learn(source: str, url: str) -> None:
        open_external(url)
        try:
            telemetry.track(te.TUTORIAL_OPENED, {"tutorial_source": source})
        except Exception:  # nosec B110 - telemetry never blocks the open
            pass

    def _build_shortcuts_page(self) -> Page:
        page = Page(tr("Keyboard shortcuts"), "", self, glyph="terminal", category="leaf")
        try:
            page.add(build_shortcuts_card(page.body_widget, self.parentWidget()))
        except Exception as exc:  # noqa: BLE001 - a broken card must not break the window
            log_warning(f"Keyboard shortcuts not shown: {exc}")
        return page

    def _build_siblings_page(self) -> Page:
        """AI Agent and AI Segmentation, installable from here."""
        page = Page(tr("More plugins"), get_export_copy(
            "widgets.siblings_dialog.intro", tr("Other TerraLab plugins for QGIS")),
            self, glyph="puzzle", category="violet")
        self._siblings_widget = None
        try:
            from ...siblings_dialog import build_siblings_page

            self._siblings_widget = build_siblings_page(page.body_widget)
        except ImportError:
            self._siblings_widget = self._fallback_siblings(page.body_widget)
        except Exception as exc:  # noqa: BLE001 - a broken card must not break the window
            log_warning(f"More plugins not shown: {exc}")
        if self._siblings_widget is not None:
            page.add(self._siblings_widget)
        return page

    @staticmethod
    def _fallback_siblings(parent) -> QWidget:
        """The sibling cards of the TerraLab menu dialog, side by side."""
        from ...cross_plugin_discovery import open_sibling, open_sibling_tutorial
        from ...siblings_dialog import SiblingCard, _card_copy

        holder = QWidget(parent)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        cards = []
        for product_id, note in _card_copy():
            card = SiblingCard(product_id, note, holder)
            card.install_requested.connect(open_sibling)
            card.tutorial_requested.connect(open_sibling_tutorial)
            row.addWidget(card, 1, Qt.AlignmentFlag.AlignTop)
            cards.append(card)
        row.addStretch(0)
        holder.refresh = lambda: [card.refresh() for card in cards]
        return holder

    def _refresh_siblings(self) -> None:
        widget = getattr(self, "_siblings_widget", None)
        if widget is not None and hasattr(widget, "refresh"):
            try:
                widget.refresh()
            except RuntimeError:
                pass

    def _open_contact(self) -> None:
        """Bug, question, feature request: the address, and a call.

        The confirm window's frame (surface ground, a bold title, one muted
        line), so it reads as part of the same product. Buttons take focus
        from Tab only: the filled Copy email no longer opened with a focus
        ring, and its "Copied" goes back to "Copy email" after two seconds.
        """
        from ...dock.pro_ceiling import copy_email_to_clipboard
        from ...keyboard_focus import settle_dialog_default_button
        from ..error_report_dialog import SUPPORT_EMAIL

        support_email = get_support_email(SUPPORT_EMAIL)
        call_url = get_contact_call_url()
        title = get_export_copy("dock.tools_footer.contact_us_title", tr("Contact us"))
        dlg = QDialog(self)
        dlg.setObjectName("contactSheet")
        dlg.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        dlg.setStyleSheet(f"QDialog#contactSheet {{ background: {SURFACE}; }}")
        dlg.setWindowTitle(PRODUCT_WINDOW_TITLE)
        dlg.setMinimumWidth(380)
        dlg.setMaximumWidth(480)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(8)
        heading = QLabel(title, dlg)
        heading.setStyleSheet(_CONTACT_TITLE_QSS)
        lay.addWidget(heading)
        msg = QLabel(get_export_copy(
            "dock.tools_footer.contact_us_body",
            tr("Bug, question or idea? Write to us."),
        ), dlg)
        msg.setWordWrap(True)
        msg.setStyleSheet(_CONTACT_BODY_QSS)
        lay.addWidget(msg)
        email_label = QLabel(support_email, dlg)
        email_label.setTextFormat(Qt.TextFormat.PlainText)
        email_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        email_label.setStyleSheet(_CONTACT_EMAIL_QSS)
        lay.addWidget(email_label)
        lay.addSpacing(10)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        copy_label = get_export_copy("dock.tools_footer.copy_email_btn", tr("Copy email"))
        if call_url:
            call_btn = make_button(
                get_export_copy("dock.tools_footer.book_call_btn", tr("Book a call")),
                BTN_GHOST_QSS, dlg)
            call_btn.clicked.connect(lambda: open_external(call_url))
            buttons.addWidget(call_btn)
        # The filled one last, at the right edge, as in every confirm window.
        copy_btn = make_button(copy_label, BTN_PRIMARY_QSS, dlg)
        copy_btn.clicked.connect(
            lambda: copy_email_to_clipboard(copy_btn, support_email, idle_text=copy_label))
        buttons.addWidget(copy_btn)
        lay.addLayout(buttons)
        settle_dialog_default_button(dlg)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()

    def _open_report(self) -> None:
        from ..error_report_dialog import show_error_report

        show_error_report(self)
