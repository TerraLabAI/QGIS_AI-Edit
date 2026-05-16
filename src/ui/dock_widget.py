from __future__ import annotations

import html
import os
import re
import tempfile

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QPoint, QSize, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QImage,
    QKeySequence,
)
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QShortcut,
    QStyle,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ..core import qt_compat as QtC
from ..core.activation_manager import (
    get_subscribe_url,
    get_tutorial_url,
    has_consent,
)
from ..core.i18n import tr
from ..core.reference_image_store import ReferenceImageStore
from .credit_ring import CreditRing
from .reference_images_widget import ReferenceImagesWidget

# ---------------------------------------------------------------------------
# Brand colors (Material Design 2 — shared with AI Segmentation)
# ---------------------------------------------------------------------------
BRAND_GREEN = "#2e7d32"
BRAND_GREEN_HOVER = "#1b5e20"
BRAND_GREEN_DISABLED = "#c8e6c9"
BRAND_BLUE = "#1976d2"
BRAND_BLUE_HOVER = "#1565c0"
BRAND_RED = "#d32f2f"
BRAND_RED_HOVER = "#b71c1c"
BRAND_GRAY = "#757575"
BRAND_GRAY_HOVER = "#616161"
BRAND_DISABLED = "#b0bec5"
DISABLED_TEXT = "#666666"
ERROR_TEXT = "#ef5350"
SUCCESS_TEXT = "#66bb6a"

MAX_PROMPT_CHARS = 2000


def _format_template_prompt(prompt: str) -> str:
    """One sentence per paragraph + colon-before-list also splits."""
    if not prompt:
        return prompt
    with_colon = re.sub(r":\s+(?=[a-zA-Z][^.:\n]*,)", ":\n\n", prompt, count=1)
    return re.sub(r"\.\s+([A-Z])", r".\n\n\1", with_colon)


TERRALAB_URL = (
    "https://terra-lab.ai/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dock_branding"
)
SUPPORT_EMAIL = "yvann.barbot@terra-lab.ai"

# ---------------------------------------------------------------------------
# Reusable QSS style constants (design system)
# ---------------------------------------------------------------------------
_BTN_GREEN = (
    f"QPushButton {{ background-color: {BRAND_GREEN}; color: #000000;"
    f" padding: 8px 16px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GREEN_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_GREEN_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_GREEN_AUTH = (
    f"QPushButton {{ background-color: {BRAND_GREEN}; color: #000000; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GREEN_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; }}"
)

_BTN_BLUE = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" padding: 6px 12px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE_AUTH = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; }}"
)

_BTN_GRAY = (
    f"QPushButton {{ background-color: {BRAND_GRAY}; color: #000000;"
    f" padding: 4px 8px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GRAY_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT}; }}"
)

_BTN_DISABLED = (
    f"QPushButton {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT};"
    f" padding: 8px 16px; }}"
)

_BTN_GHOST = (
    "QPushButton { background-color: transparent; color: palette(text);"
    " padding: 8px 16px; border-radius: 4px;"
    " border: 1px solid rgba(128, 128, 128, 0.35); }"
    "QPushButton:hover { background-color: rgba(128, 128, 128, 0.15);"
    " border: 1px solid rgba(128, 128, 128, 0.5); }"
    f"QPushButton:disabled {{ background-color: rgba(128, 128, 128, 0.08);"
    f" border: 1px solid rgba(128, 128, 128, 0.15); color: {DISABLED_TEXT}; }}"
)

# Footer icon buttons (gear / question mark) — slim toolbuttons.
# Hover/pressed states are gated by the dynamic ``hover`` property
# rather than Qt's :hover pseudo-state. With InstantPopup menus, Qt
# fails to clear :hover once the menu closes (the synthetic Leave
# event is eaten by the popup), so the button stays tinted. The
# property-driven approach lets us force-reset the visual via
# ``_FooterIconButton.set_hovered(False)``.
_FOOTER_ICON_BTN_STYLE = (
    "QToolButton { background: transparent; border: none; padding: 6px 10px;"
    " font-size: 22px; font-weight: 600;"
    " color: palette(text); border-radius: 4px; }"
    'QToolButton[hover="true"] { background: rgba(128,128,128,0.15); }'
    "QToolButton::menu-indicator { image: none; width: 0; }"
)

_FOOTER_MENU_STYLE = (
    "QMenu { background: palette(base); border: 1px solid rgba(128,128,128,0.35);"
    " border-radius: 6px; padding: 4px; }"
    "QMenu::item { background: transparent; padding: 6px 14px; border-radius: 4px;"
    " color: palette(text); }"
    "QMenu::item:selected { background: rgba(128,128,128,0.18); }"
)

_INSTRUCTION_BOX = (
    "QLabel {"
    "  background-color: rgba(128, 128, 128, 0.12);"
    "  border: 1px solid rgba(128, 128, 128, 0.25);"
    "  border-radius: 4px;"
    "  padding: 8px;"
    "  font-size: 10px;"
    "  color: palette(text);"
    "}"
)

_SECTION_HEADER = (
    "font-weight: bold; font-size: 12px; color: palette(text);"
    " margin: 0px; padding: 0px 0px 2px 0px;"
)

_SECTION_HEADER_EXTRA_TOP = (
    "font-weight: bold; font-size: 12px; color: palette(text); padding-top: 6px;"
)


def _picture_icon() -> QIcon:
    """Return the 'attach reference image' glyph as a vector icon.

    Loads the bundled SVG (resources/icons/image.svg) so QIcon's built-in
    SVG renderer re-rasterises it crisply at whatever size the QToolButton
    requests — matching the visual weight of the ⚙ / ? footer glyphs.
    """
    plugin_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return QIcon(os.path.join(plugin_root, "resources", "icons", "image.svg"))


def _make_section_header(text: str, extra_top: bool = False) -> QLabel:
    """Create a section header label."""
    label = QLabel(text)
    label.setStyleSheet(_SECTION_HEADER_EXTRA_TOP if extra_top else _SECTION_HEADER)
    label.setContentsMargins(0, 0, 0, 0)
    return label


_IMAGE_DROP_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def _image_paths_from_mime(mime) -> list[str]:
    if not mime.hasUrls():
        return []
    out: list[str] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = url.toLocalFile()
        ext = os.path.splitext(path)[1].lower()
        if ext in _IMAGE_DROP_EXTS:
            out.append(path)
    return out


class _FooterIconButton(QToolButton):
    """QToolButton whose hover tint is driven by an explicit ``hover``
    dynamic property rather than Qt's :hover pseudo-state.

    With InstantPopup menus, Qt fails to fire the synthetic Leave event
    after the menu closes, so the button stays visually pressed/hovered
    until the next real mouse move. Tracking hover ourselves lets us
    force-reset it on ``menu.aboutToHide``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("hover", False)

    def set_hovered(self, hovered: bool) -> None:
        if bool(self.property("hover")) == hovered:
            return
        self.setProperty("hover", hovered)
        # Re-polish so the [hover="true"] selector takes effect.
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def enterEvent(self, event):  # noqa: N802
        self.set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.set_hovered(False)
        super().leaveEvent(event)


class _SubmitTextEdit(QTextEdit):
    """Borderless QTextEdit used inside _PromptContainer.

    - Enter submits, Shift+Enter inserts newline.
    - Image file paths in the clipboard or raw image data (e.g. a screenshot
      copied from Preview) are routed to the references store via
      ``images_pasted`` instead of being inserted as an emoji-doc icon.
    - Drag-drop is delegated to the parent container by disabling
      ``acceptDrops``, so the whole bordered area lights up rather than just
      the text area.
    - No QSS is applied here on purpose: a stylesheet on QTextEdit forces Qt
      into its own renderer, which loses the native (e.g. macOS accent-blue)
      caret. The frame and transparent background are set programmatically.
    """

    submitted = pyqtSignal()
    images_pasted = pyqtSignal(list)

    _INNER_STYLE = (
        "QTextEdit { background: transparent; padding: 4px; }"
        # Some Qt styles still reserve a thin strip for the horizontal
        # scrollbar even with ScrollBarAlwaysOff; force its height to 0.
        "QTextEdit QScrollBar:horizontal { height: 0px; margin: 0px; }"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(False)
        # Remove the native frame — the surrounding _PromptContainer paints it.
        self.setFrameShape(QtC.FrameNoFrame)
        # Transparent background only; keep palette untouched so the QTextEdit
        # keeps its native caret (the accent-blue insertion bar on macOS).
        # Setting QPalette.Base ourselves silently disables the caret on some
        # Qt builds, hence the QSS-only path here.
        self.setStyleSheet(self._INNER_STYLE)
        # Wrap long tokens (URLs, glued words) mid-character so the line never
        # exceeds the box width, and kill the horizontal scrollbar — Qt
        # otherwise reserves space for it (the "invisible bar" at the bottom).
        self.setLineWrapMode(QtC.LineWrapWidgetWidth)
        self.setWordWrapMode(QtC.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        # Force plain text on paste: rich-text from a browser or markdown
        # source can carry <pre> / white-space:nowrap that defeats wrapping,
        # leaving the line wider than the viewport even with wrap modes set.
        self.setAcceptRichText(False)

    def keyPressEvent(self, event):  # noqa: N802
        if (
            event.key() in (QtC.Key_Return, QtC.Key_Enter)
            and not event.modifiers() & QtC.ShiftModifier  # noqa: W503
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source):  # noqa: N802
        if source.hasImage() or _image_paths_from_mime(source):
            return False
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):  # noqa: N802
        paths = _image_paths_from_mime(source)
        if paths:
            self.images_pasted.emit(paths)
            return
        if source.hasImage():
            self._emit_clipboard_image(source)
            return
        super().insertFromMimeData(source)

    def _emit_clipboard_image(self, source) -> None:
        """Save raw clipboard image data to a temp PNG and forward as a path."""
        image = source.imageData()
        if image is None:
            return
        if not isinstance(image, QImage):
            image = QImage(image)
        if image.isNull():
            return
        fd, tmp_path = tempfile.mkstemp(prefix="ai-edit-paste-", suffix=".png")
        os.close(fd)
        saved = False
        try:
            saved = bool(image.save(tmp_path, "PNG"))
            if saved:
                self.images_pasted.emit([tmp_path])
        finally:
            # Listener processed the path synchronously (direct connection);
            # the refs store has its own compressed copy by now.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class _ResolutionMenuItem(QWidget):
    """Custom widget for one resolution row inside the resolution QMenu.

    Layout:   [ ✓ ]  Label                          N credits

    The leading checkmark column is fixed-width so all three rows align.
    Locked rows (free-tier 2K / 4K) render in a muted color but stay
    clickable — the click still fires so the dock widget can show the
    "Subscribe for higher resolution" banner.

    QMenu does not paint its selection highlight under QWidgetAction items,
    so the hover background is drawn by the widget itself via a :hover
    stylesheet. Child labels are marked transparent-for-mouse so the
    parent receives all hover/click events even when the cursor is over
    a QLabel.
    """

    clicked = pyqtSignal()

    _ITEM_STYLE = (
        "QWidget#resolutionMenuItem { background: transparent; border-radius: 4px; }"
        "QWidget#resolutionMenuItem:hover { background: rgba(128,128,128,0.20); }"
    )

    def __init__(
        self,
        label: str,
        credits: int,
        selected: bool,
        locked: bool,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("resolutionMenuItem")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setStyleSheet(self._ITEM_STYLE)
        self.setCursor(QtC.PointingHandCursor)
        self.setMinimumHeight(26)
        if locked:
            self.setToolTip(tr("Subscribe for higher resolution"))

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 16, 4)
        row.setSpacing(8)

        check = QLabel("✓" if selected else "", self)
        check.setFixedWidth(12)
        check.setStyleSheet(
            "font-size: 12px; color: palette(text); background: transparent;"
        )
        check.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        row.addWidget(check)

        muted = f"color: {DISABLED_TEXT};" if locked else "color: palette(text);"
        name = QLabel(label, self)
        name.setStyleSheet(f"font-size: 12px; background: transparent; {muted}")
        name.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        row.addWidget(name)

        row.addStretch()

        cost_color = (
            f"color: {DISABLED_TEXT};"
            if locked
            else "color: rgba(128,128,128,0.85);"
        )
        cost = QLabel(tr("{n} credits").format(n=credits), self)
        cost.setStyleSheet(f"font-size: 11px; background: transparent; {cost_color}")
        cost.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        row.addWidget(cost)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == QtC.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _PromptContainer(QFrame):
    """Bordered frame wrapping prompt textbox + refs strip + footer row.

    Footer layout (bottom row):

        [Templates]                              [ 2K  ⌄ ]   [ + ]

    The whole frame is the drop target so dragging anywhere over it lights up
    a single coherent area, matching the ChatGPT-style attachment flow.
    """

    files_dropped = pyqtSignal(list)
    attach_clicked = pyqtSignal()
    templates_clicked = pyqtSignal()
    resolution_changed = pyqtSignal(str)

    _NORMAL_STYLE = (
        "QFrame#promptContainer { border: 1px solid rgba(128,128,128,0.3);"
        " border-radius: 4px; background-color: rgba(128,128,128,0.06); }"
    )
    _READONLY_STYLE = (
        "QFrame#promptContainer { border: 1px solid rgba(128,128,128,0.3);"
        " border-radius: 4px; background-color: rgba(128,128,128,0.10); }"
    )
    _ATTACH_BTN_STYLE = (
        "QToolButton { background: transparent; border: none; padding: 1px 8px;"
        " font-size: 14px; font-weight: normal;"
        " color: rgba(128,128,128,0.75); }"
        "QToolButton:hover { color: palette(text);"
        " background: rgba(128,128,128,0.15); border-radius: 4px; }"
        "QToolButton:disabled { color: rgba(128,128,128,0.35); background: transparent; }"
    )
    _FOOTER_TEXT_BTN_STYLE = (
        "QToolButton { background: transparent; border: none; padding: 2px 6px;"
        " font-size: 11px; color: palette(text); }"
        "QToolButton:hover { color: palette(text);"
        " background: rgba(128,128,128,0.15); border-radius: 4px; }"
        "QToolButton:disabled { color: rgba(128,128,128,0.45); background: transparent; }"
        "QToolButton::menu-indicator { image: none; width: 0; }"
    )
    # Property-driven hover variant for buttons that pop a QMenu — see
    # _FooterIconButton for the rationale (Qt eats the synthetic Leave
    # event when a popup closes, leaving Qt's :hover stuck on).
    _FOOTER_TEXT_BTN_HOVERPROP_STYLE = (
        "QToolButton { background: transparent; border: none; padding: 2px 6px;"
        " font-size: 11px; color: palette(text); border-radius: 4px; }"
        'QToolButton[hover="true"] { background: rgba(128,128,128,0.15); }'
        "QToolButton:disabled { color: rgba(128,128,128,0.45); background: transparent; }"
        "QToolButton::menu-indicator { image: none; width: 0; }"
    )
    _MENU_STYLE = (
        "QMenu { background: palette(base); border: 1px solid rgba(128,128,128,0.35);"
        " border-radius: 6px; padding: 4px; }"
        "QMenu::item { background: transparent; padding: 0; }"
        "QMenu::item:selected { background: rgba(128,128,128,0.18); border-radius: 4px; }"
    )

    def __init__(self, text_edit: _SubmitTextEdit, parent=None):
        super().__init__(parent)
        self.setObjectName("promptContainer")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setAcceptDrops(True)

        self._text_edit = text_edit
        self._base_style = self._NORMAL_STYLE
        self._readonly = False

        # Resolution state mirrored from the dock widget so the popup can be
        # rebuilt locally without re-reaching into the parent.
        self._selected_resolution = "2K"
        self._resolution_costs: dict[str, int] = {"1K": 20, "2K": 30, "4K": 40}
        self._free_tier = False

        # No graphics effect attached at init: applying QGraphicsDropShadowEffect
        # to a parent of a QTextEdit silently breaks the text-insertion caret
        # (the effect pipeline intercepts the QTextEdit's blink timer paint).
        # The drag-over glow is attached on dragEnterEvent and detached on
        # dragLeave / drop — see _set_glow.

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)

        # Slot index 0 is reserved for the refs strip when injected.
        layout.addWidget(text_edit)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(0)

        self._templates_btn = QToolButton(self)
        self._templates_btn.setText(tr("Templates"))
        self._templates_btn.setToolTip(tr("Browse prompt templates"))
        self._templates_btn.setCursor(QtC.PointingHandCursor)
        self._templates_btn.setStyleSheet(self._FOOTER_TEXT_BTN_STYLE)
        self._templates_btn.clicked.connect(self.templates_clicked.emit)
        footer_row.addWidget(self._templates_btn)

        footer_row.addStretch()

        self._resolution_menu = QMenu(self)
        self._resolution_menu.setStyleSheet(self._MENU_STYLE)
        # Allow per-action tooltips (Qt swallows them by default in QMenu).
        self._resolution_menu.setToolTipsVisible(True)
        self._resolution_btn = _FooterIconButton(self)
        self._resolution_btn.setToolTip(tr("Output resolution"))
        self._resolution_btn.setCursor(QtC.PointingHandCursor)
        self._resolution_btn.setStyleSheet(self._FOOTER_TEXT_BTN_HOVERPROP_STYLE)
        self._resolution_btn.clicked.connect(self._show_resolution_menu)
        # Force the hover tint off when the popup closes — Qt does not
        # synthesise a Leave event in this case (same fix as the help menu).
        self._resolution_menu.aboutToHide.connect(
            lambda btn=self._resolution_btn: (btn.setDown(False), btn.set_hovered(False))
        )
        footer_row.addWidget(self._resolution_btn)
        self._rebuild_resolution_menu()
        self._update_resolution_label()

        self._attach_btn = QToolButton(self)
        self._attach_btn.setIcon(_picture_icon())
        self._attach_btn.setIconSize(QSize(20, 20))
        self._attach_btn.setToolTip(tr("Add reference image"))
        self._attach_btn.setCursor(QtC.PointingHandCursor)
        self._attach_btn.setStyleSheet(self._ATTACH_BTN_STYLE)
        self._attach_btn.clicked.connect(self.attach_clicked.emit)
        footer_row.addWidget(self._attach_btn)

        layout.addLayout(footer_row)
        self.setStyleSheet(self._base_style)

    # -- public API --------------------------------------------------------

    def insert_refs_widget(self, widget: QWidget) -> None:
        """Move a shared refs widget into this container at the top slot."""
        old_parent = widget.parentWidget()
        if old_parent is not None and old_parent is not self:
            old_layout = old_parent.layout()
            if old_layout is not None:
                old_layout.removeWidget(widget)
        widget.setParent(self)
        self.layout().insertWidget(0, widget)

    def set_readonly(self, readonly: bool) -> None:
        self._readonly = readonly
        self._base_style = self._READONLY_STYLE if readonly else self._NORMAL_STYLE
        self.setStyleSheet(self._base_style)
        self._text_edit.setReadOnly(readonly)
        # Visible-but-disabled while a generation runs — matches the Claude
        # chat input pattern of leaving the footer chrome in place.
        self._templates_btn.setEnabled(not readonly)
        self._resolution_btn.setEnabled(not readonly)
        self._attach_btn.setEnabled(not readonly)

    def set_attach_enabled(self, enabled: bool) -> None:
        """Hide the + button when the refs store is at capacity.

        Readonly state is handled by set_readonly, which keeps the button
        visible-but-disabled — do not gate visibility on readonly here.
        """
        self._attach_btn.setVisible(enabled)

    def set_resolution_state(
        self,
        selected: str,
        costs: dict[str, int] | None,
        free_tier: bool,
    ) -> None:
        """Refresh the trigger label, the menu items and their lock state."""
        self._selected_resolution = selected
        if costs:
            self._resolution_costs = costs
        self._free_tier = free_tier
        self._rebuild_resolution_menu()
        self._update_resolution_label()

    # -- resolution menu internals ----------------------------------------

    def _update_resolution_label(self) -> None:
        # ▾ (U+25BE) sits on the text baseline; ⌄ (U+2304) renders too low
        # in most system fonts and breaks the visual alignment.
        self._resolution_btn.setText(f"{self._selected_resolution}  ▾")

    def _rebuild_resolution_menu(self) -> None:
        self._resolution_menu.clear()
        for res in ("1K", "2K", "4K"):
            locked = self._free_tier and res != "1K"
            selected = res == self._selected_resolution
            credits = self._resolution_costs.get(res, 0)
            widget = _ResolutionMenuItem(
                res, credits, selected, locked, self._resolution_menu
            )
            widget.clicked.connect(lambda r=res: self._on_menu_item_clicked(r))
            action = QWidgetAction(self._resolution_menu)
            action.setDefaultWidget(widget)
            if locked:
                action.setToolTip(tr("Subscribe for higher resolution"))
            self._resolution_menu.addAction(action)

    def _on_menu_item_clicked(self, label: str) -> None:
        self._resolution_menu.close()
        self.resolution_changed.emit(label)

    def _show_resolution_menu(self) -> None:
        if not self._resolution_btn.isEnabled():
            return
        anchor = self._resolution_btn.mapToGlobal(QPoint(0, 0))
        menu_height = self._resolution_menu.sizeHint().height()
        anchor.setY(anchor.y() - menu_height)
        self._resolution_menu.popup(anchor)

    # -- drag and drop -----------------------------------------------------

    def _set_glow(self, active: bool) -> None:
        if active:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(14)
            effect.setOffset(0, 0)
            effect.setColor(QColor(25, 118, 210, 200))
            self.setGraphicsEffect(effect)
        else:
            # Detaching the effect restores native QTextEdit rendering and the
            # accent-blue caret blink.
            self.setGraphicsEffect(None)

    def dragEnterEvent(self, event):  # noqa: N802
        if self._readonly:
            return
        if _image_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            self._set_glow(True)

    def dragMoveEvent(self, event):  # noqa: N802
        if self._readonly:
            return
        if _image_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):  # noqa: N802
        self._set_glow(False)
        event.accept()

    def dropEvent(self, event):  # noqa: N802
        paths = _image_paths_from_mime(event.mimeData())
        self._set_glow(False)
        if paths and not self._readonly:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()


class AIEditDockWidget(QDockWidget):
    """Dock widget with prompt-first flow.

    The prompt view is always visible after activation. The selection tool
    stays active so the user can draw a zone at any time. The Generate
    button is disabled (shows "Select your zone") until a zone is drawn.
    """

    stop_clicked = pyqtSignal()
    generate_clicked = pyqtSignal(str)
    retry_clicked = pyqtSignal(str)       # retry on same zone with (possibly edited) prompt
    activation_attempted = pyqtSignal(str)
    change_key_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    launch_clicked = pyqtSignal()          # user clicked "Launch AI Edit" on entry screen
    exit_clicked = pyqtSignal()            # user clicked the always-visible Exit button
    # (template_id, template_name) for analytics — id is stable, name is human-readable.
    template_selected = pyqtSignal(str, str)

    def __init__(self, parent=None, reference_store: ReferenceImageStore | None = None):
        super().__init__(tr("AI Edit by TerraLab"), parent)
        self.setAllowedAreas(QtC.LeftDockWidgetArea | QtC.RightDockWidgetArea)
        self.setMinimumWidth(260)
        self._reference_store = reference_store

        # Global Escape: exit the flow no matter where focus is (canvas while
        # drawing a zone, prompt textarea, progress bar, etc.). WindowShortcut
        # context lets the shortcut fire on the parent main window's key events
        # via ShortcutOverride, which beats the map tool's local Escape handler.
        self._escape_shortcut = QShortcut(QKeySequence(QtC.Key_Escape), self)
        self._escape_shortcut.setContext(QtC.WindowShortcut)
        self._escape_shortcut.activated.connect(self._on_escape_pressed)

        self._setup_title_bar()

        # Main content
        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # --- Activation section ---
        self._activation_widget = self._build_activation_section()
        layout.addWidget(self._activation_widget)

        # --- Main content section ---
        self._main_widget = QWidget()
        main_layout = QVBoxLayout(self._main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        # Warning widget (no visible layer) — above prompt
        self._warning_widget = self._build_warning_widget()
        self._warning_widget.setVisible(False)
        main_layout.addWidget(self._warning_widget)

        # --- Launch section (entry screen, matches AI Segmentation pattern) ---
        self._launch_section = QWidget()
        launch_layout = QVBoxLayout(self._launch_section)
        launch_layout.setContentsMargins(0, 0, 0, 0)
        launch_layout.setSpacing(8)

        self._launch_btn = QPushButton(tr("Launch AI Edit"))
        self._launch_btn.setToolTip(tr("Start a new AI edit session"))
        self._launch_btn.setCursor(QtC.PointingHandCursor)
        self._launch_btn.setMinimumHeight(40)
        self._launch_btn.setStyleSheet(_BTN_GREEN_AUTH)
        self._launch_btn.clicked.connect(self.launch_clicked.emit)
        launch_layout.addWidget(self._launch_btn)

        self._launch_section.setVisible(False)
        main_layout.addWidget(self._launch_section)

        # --- Select-zone section (in-between state: invites user to draw on canvas) ---
        self._select_zone_section = QWidget()
        sz_layout = QVBoxLayout(self._select_zone_section)
        sz_layout.setContentsMargins(0, 0, 0, 0)
        sz_layout.setSpacing(6)

        self._select_zone_header = _make_section_header(tr("Select your zone"))
        sz_layout.addWidget(self._select_zone_header)

        self._select_zone_hint = QLabel(
            tr("Hold Shift and drag on the map to draw a rectangular zone. Plain drag pans the map.")
        )
        self._select_zone_hint.setWordWrap(True)
        self._select_zone_hint.setStyleSheet(_INSTRUCTION_BOX)
        sz_layout.addWidget(self._select_zone_hint)

        self._select_zone_section.setVisible(False)
        main_layout.addWidget(self._select_zone_section)

        # --- Prompt section (shown after zone selected) ---
        self._prompt_section = QWidget()
        self._prompt_section.setContentsMargins(0, 0, 0, 0)
        self._prompt_layout = QVBoxLayout(self._prompt_section)
        self._prompt_layout.setContentsMargins(0, 0, 0, 0)
        self._prompt_layout.setSpacing(6)

        self._prompt_header = _make_section_header(tr("What should AI change?"))
        self._prompt_header.setVisible(True)
        self._prompt_layout.addWidget(self._prompt_header)

        self._prompt_input = _SubmitTextEdit()
        self._prompt_input.setPlaceholderText(
            tr("type your prompt or use a template...")
        )
        self._prompt_input.document().setDocumentMargin(0)
        self._prompt_input.setMinimumHeight(60)
        self._prompt_input.setMaximumHeight(60)
        self._prompt_input.textChanged.connect(self._on_prompt_changed)
        self._prompt_input.submitted.connect(self._on_generate_clicked)
        self._prompt_input.document().documentLayout().documentSizeChanged.connect(
            self._adjust_prompt_height
        )
        self._prompt_container = _PromptContainer(self._prompt_input, self._prompt_section)
        self._prompt_container.templates_clicked.connect(self._on_browse_templates_clicked)
        self._prompt_container.resolution_changed.connect(self._on_resolution_selected)
        self._prompt_layout.addWidget(self._prompt_container)

        # Hidden by default: revealed by set_zone_selected() once the user
        # draws a rectangle. Initial dock state only shows the "Select your
        # zone" button.
        self._prompt_section.setVisible(False)
        main_layout.addWidget(self._prompt_section)

        # Reference images widget — created once, moved between the prompt
        # container and the result container as state changes.
        if self._reference_store is not None:
            self._reference_widget = ReferenceImagesWidget(
                self._reference_store, self
            )
            self._reference_widget.error_occurred.connect(
                lambda msg: self._show_status_box(msg, "error")
            )
            self._reference_widget.error_cleared.connect(self._hide_status_box)
            self._reference_widget.images_changed.connect(self._sync_attach_buttons)
            # Forward container actions: drop on container + paste in textbox +
            # paperclip click all funnel into the reference widget.
            self._prompt_container.files_dropped.connect(self._reference_widget.add_paths)
            self._prompt_container.attach_clicked.connect(
                self._reference_widget.open_file_picker
            )
            self._prompt_input.images_pasted.connect(self._reference_widget.add_paths)
            # Idle: keep the widget out of the title bar by hiding it until placed.
            self._reference_widget.setVisible(False)
        else:
            self._reference_widget = None

        # Consent checkbox (shown only until first generation)
        self._consent_check = QCheckBox()
        self._consent_check.setStyleSheet(
            "QCheckBox::indicator {"
            "  width: 16px; height: 16px;"
            "  border: 1px solid palette(text);"
            "  border-radius: 3px;"
            "  background-color: palette(base);"
            "}"
            f"QCheckBox::indicator:checked {{"
            f"  background-color: {BRAND_BLUE};"
            f"  border-color: {BRAND_BLUE};"
            f"}}"
        )
        self._consent_check.setText("")  # text set via label below
        consent_layout = QHBoxLayout()
        consent_layout.setContentsMargins(0, 0, 0, 0)
        consent_layout.setSpacing(4)
        consent_layout.addWidget(self._consent_check, 0)
        _terms_url = (
            "https://terra-lab.ai/terms-of-sale"
            "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=consent_terms"
        )
        _privacy_url = (
            "https://terra-lab.ai/privacy-policy"
            "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=consent_privacy"
        )
        # Plain-language disclosure of what the plugin actually does with the
        # image, so installing it on the QGIS marketplace doesn't surprise the
        # user. Mentions both upload and retention since neither is obvious from
        # the UI alone. {terms} and {privacy} are placeholders so the linked
        # words can be reordered in translations.
        _consent_template = tr(
            "By generating, you upload your selection to TerraLab for AI processing "
            "and EU storage. {terms} · {privacy}"
        )
        _terms_link = (
            f'<a href="{_terms_url}" style="color: {BRAND_BLUE};">{tr("Terms")}</a>'
        )
        _privacy_link = (
            f'<a href="{_privacy_url}" style="color: {BRAND_BLUE};">{tr("Privacy")}</a>'
        )
        consent_text = QLabel(
            _consent_template.format(terms=_terms_link, privacy=_privacy_link)
        )
        consent_text.setOpenExternalLinks(True)
        consent_text.setWordWrap(True)
        consent_text.setStyleSheet("font-size: 11px; color: palette(text);")
        consent_layout.addWidget(consent_text, 1)
        self._consent_widget = QWidget()
        self._consent_widget.setLayout(consent_layout)
        self._consent_widget.setVisible(False)
        self._consent_check.stateChanged.connect(self._on_consent_changed)
        main_layout.addWidget(self._consent_widget)

        # Generate + Exit row. Exit is shown in the PROMPT state (zone
        # selected) so the user always has a one-click way back to LAUNCH,
        # but is hidden while generation is in flight to avoid a "cancel mid-
        # run" footgun.
        generate_row = QHBoxLayout()
        generate_row.setContentsMargins(0, 0, 0, 0)
        generate_row.setSpacing(6)

        self._generate_btn = QPushButton(tr("Generate"))
        self._generate_btn.setToolTip(tr("Run the AI edit on your selected zone"))
        self._generate_btn.setCursor(QtC.PointingHandCursor)
        self._generate_btn.setEnabled(False)
        self._update_generate_style()
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        self._generate_btn.setVisible(False)
        generate_row.addWidget(self._generate_btn, 1)

        self._exit_btn = QPushButton(tr("Exit"))
        self._exit_btn.setToolTip(tr("Exit and return to the start"))
        self._exit_btn.setCursor(QtC.PointingHandCursor)
        self._exit_btn.setFixedWidth(72)
        self._exit_btn.setMinimumHeight(34)
        self._exit_btn.setStyleSheet(_BTN_GHOST)
        self._exit_btn.clicked.connect(self._on_exit_clicked)
        self._exit_btn.setVisible(False)
        generate_row.addWidget(self._exit_btn, 0)

        main_layout.addLayout(generate_row)

        # Progress section
        self._progress_widget = QWidget()
        progress_layout = QVBoxLayout(self._progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(4)
        self._progress_label = QLabel(tr("Preparing..."))
        self._progress_label.setStyleSheet("font-size: 11px; color: palette(text);")
        progress_layout.addWidget(self._progress_label)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        progress_layout.addWidget(self._progress_bar)

        self._progress_widget.setVisible(False)
        main_layout.addWidget(self._progress_widget)

        # Status message box (same pattern as AI Segmentation info boxes)
        self._status_widget = QWidget()
        self._status_widget.setVisible(False)
        status_box_layout = QHBoxLayout(self._status_widget)
        status_box_layout.setContentsMargins(8, 6, 8, 6)
        status_box_layout.setSpacing(8)
        self._status_icon = QLabel()
        _ico = self._status_widget.style().pixelMetric(
            QStyle.PixelMetric.PM_SmallIconSize
        )
        self._status_icon.setFixedSize(_ico, _ico)
        self._status_icon_size = _ico
        status_box_layout.addWidget(
            self._status_icon, 0, QtC.AlignTop
        )
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setOpenExternalLinks(True)
        self._status_label.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        status_box_layout.addWidget(self._status_label, 1)

        # CTA button displayed for quota exhaustion
        self._limit_cta_btn = QPushButton(tr("Subscribe"))
        self._limit_cta_btn.setToolTip(tr("Open the subscription page in your browser"))
        self._limit_cta_btn.setCursor(QtC.PointingHandCursor)
        self._limit_cta_btn.setStyleSheet(_BTN_BLUE)
        self._limit_cta_btn.clicked.connect(self._on_limit_cta_clicked)
        self._limit_cta_btn.setVisible(False)
        self._limit_cta_url = ""

        # --- Result section (shown after generation complete, iteration flow) ---
        self._result_section = QWidget()
        self._result_layout = QVBoxLayout(self._result_section)
        self._result_layout.setContentsMargins(0, 0, 0, 0)
        self._result_layout.setSpacing(6)

        # "What's next?" header
        result_header = _make_section_header(tr("What's next?"))
        self._result_layout.addWidget(result_header)

        # Editable prompt (edit and retry)
        self._result_prompt_input = _SubmitTextEdit()
        self._result_prompt_input.setPlaceholderText(
            tr("Edit the prompt above and retry, or pick a new action below")
        )
        self._result_prompt_input.document().setDocumentMargin(0)
        self._result_prompt_input.setMinimumHeight(50)
        self._result_prompt_input.setMaximumHeight(50)
        self._result_prompt_input.submitted.connect(self._on_retry_clicked)
        self._result_prompt_input.textChanged.connect(self._on_result_prompt_changed)
        self._result_prompt_input.document().documentLayout().documentSizeChanged.connect(
            self._adjust_result_prompt_height
        )
        self._result_prompt_container = _PromptContainer(
            self._result_prompt_input, self._result_section
        )
        self._result_prompt_container.templates_clicked.connect(
            self._on_browse_templates_clicked
        )
        self._result_prompt_container.resolution_changed.connect(
            self._on_resolution_selected
        )
        if self._reference_widget is not None:
            self._result_prompt_container.files_dropped.connect(
                self._reference_widget.add_paths
            )
            self._result_prompt_container.attach_clicked.connect(
                self._reference_widget.open_file_picker
            )
            self._result_prompt_input.images_pasted.connect(
                self._reference_widget.add_paths
            )
        self._result_layout.addWidget(self._result_prompt_container)

        # Action row: Regenerate (primary, flex) + Exit (ghost, fixed).
        result_actions_row = QHBoxLayout()
        result_actions_row.setContentsMargins(0, 4, 0, 0)
        result_actions_row.setSpacing(6)

        self._result_regenerate_btn = QPushButton(tr("Regenerate"))
        self._result_regenerate_btn.setToolTip(
            tr("Regenerate on the same zone using the current map view")
        )
        self._result_regenerate_btn.setCursor(QtC.PointingHandCursor)
        self._result_regenerate_btn.setStyleSheet(_BTN_GREEN)
        self._result_regenerate_btn.clicked.connect(self._on_retry_clicked)
        result_actions_row.addWidget(self._result_regenerate_btn, 1)

        self._result_exit_btn = QPushButton(tr("Exit"))
        self._result_exit_btn.setToolTip(tr("Exit and return to the start"))
        self._result_exit_btn.setCursor(QtC.PointingHandCursor)
        self._result_exit_btn.setFixedWidth(72)
        self._result_exit_btn.setMinimumHeight(34)
        self._result_exit_btn.setStyleSheet(_BTN_GHOST)
        self._result_exit_btn.clicked.connect(self._on_exit_clicked)
        result_actions_row.addWidget(self._result_exit_btn, 0)

        self._result_layout.addLayout(result_actions_row)

        # Minimal status line — shown under the action row after generation.
        # Submitting the prompt (Enter key) and the Regenerate button both
        # trigger a regen on the same zone.
        self._layer_saved_label = QLabel()
        self._layer_saved_label.setWordWrap(True)
        self._layer_saved_label.setTextFormat(QtC.RichText)
        self._layer_saved_label.setStyleSheet(
            "font-size: 11px; color: palette(text); background: transparent;"
            " border: none; padding: 4px 0 0 0;"
        )
        self._layer_saved_label.setCursor(QtC.PointingHandCursor)
        self._layer_saved_label.linkActivated.connect(self._on_layer_saved_link_clicked)
        self._layer_saved_label.setVisible(False)
        self._saved_layer_id: str | None = None
        self._result_layout.addWidget(self._layer_saved_label)

        self._result_section.setVisible(False)
        main_layout.addWidget(self._result_section)

        # Status box + CTA placed after result section so they always appear below
        main_layout.addWidget(self._status_widget)
        main_layout.addWidget(self._limit_cta_btn)

        # Trial exhausted info box
        self._trial_info_box = QFrame()
        self._trial_info_box.setStyleSheet(
            "QFrame { background: rgba(25,118,210,0.08); "
            "border: 1px solid rgba(25,118,210,0.2); "
            "border-radius: 4px; padding: 10px; }"
        )
        trial_layout = QVBoxLayout(self._trial_info_box)
        trial_layout.setContentsMargins(10, 10, 10, 10)
        trial_layout.setSpacing(6)
        self._trial_info_text = QLabel("")
        self._trial_info_text.setWordWrap(True)
        self._trial_info_text.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        trial_layout.addWidget(self._trial_info_text)
        self._trial_info_link = QLabel("")
        self._trial_info_link.setOpenExternalLinks(True)
        self._trial_info_link.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        trial_layout.addWidget(self._trial_info_link)
        self._trial_info_box.setVisible(False)
        main_layout.addWidget(self._trial_info_box)

        main_layout.addStretch()

        layout.addWidget(self._main_widget)

        # Spacer to push footer to bottom
        layout.addStretch()

        # Footer section — single row: ring + count + Subscribe pill on the
        # left, gear/help menus on the right.
        footer_widget = QWidget()
        footer_row = QHBoxLayout(footer_widget)
        footer_row.setContentsMargins(0, 4, 0, 4)
        footer_row.setSpacing(6)

        self._credit_ring = CreditRing(diameter=16, parent=footer_widget)
        self._credit_ring.setVisible(False)
        footer_row.addWidget(self._credit_ring)

        self._credits_label = QLabel()
        self._credits_label.setStyleSheet(
            "QLabel { font-size: 11px; color: palette(text);"
            " background: transparent; border: none; }"
        )
        self._credits_label.setVisible(False)
        footer_row.addWidget(self._credits_label)

        self._upgrade_cta = QPushButton(tr("Subscribe"))
        self._upgrade_cta.setToolTip(tr("Open the subscription page in your browser"))
        self._upgrade_cta.setCursor(QtC.PointingHandCursor)
        self._upgrade_cta.setStyleSheet(
            f"QPushButton {{ border: 1px solid {BRAND_BLUE}; color: {BRAND_BLUE};"
            f" border-radius: 8px; padding: 1px 8px; font-size: 11px;"
            f" background: transparent; font-weight: normal; }}"
            f"QPushButton:hover {{ background: rgba(25,118,210,0.12); }}"
        )
        self._upgrade_cta.clicked.connect(self._on_upgrade_clicked)
        self._upgrade_cta.setVisible(False)
        footer_row.addWidget(self._upgrade_cta)

        footer_row.addStretch()

        # Settings button — gear icon, opens the Account Settings dialog
        # directly. Shortcuts have moved inside that dialog.
        self._settings_btn = _FooterIconButton(footer_widget)
        self._settings_btn.setText("⚙")  # ⚙ U+2699 GEAR
        self._settings_btn.setToolTip(tr("Settings"))
        self._settings_btn.setCursor(QtC.PointingHandCursor)
        self._settings_btn.setFocusPolicy(QtC.NoFocus)
        self._settings_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
        self._settings_btn.clicked.connect(self._on_settings_btn_clicked)
        self._settings_btn.setVisible(False)  # shown when activated
        footer_row.addWidget(self._settings_btn)

        # Help menu — question mark icon, always visible.
        self._help_btn = _FooterIconButton(footer_widget)
        self._help_btn.setText("?")
        self._help_btn.setToolTip(tr("Help"))
        self._help_btn.setCursor(QtC.PointingHandCursor)
        self._help_btn.setFocusPolicy(QtC.NoFocus)
        self._help_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
        self._help_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        help_menu = QMenu(self._help_btn)
        help_menu.setStyleSheet(_FOOTER_MENU_STYLE)
        help_menu.addAction(tr("Tutorial"), self._on_open_tutorial)
        help_menu.addAction(tr("Shortcuts"), self._on_show_shortcuts)
        help_menu.addAction(tr("Contact us"), self._on_contact_us)
        self._help_btn.setMenu(help_menu)
        # Force the hover tint off when the popup closes — Qt does not
        # synthesise a Leave event in this case.
        help_menu.aboutToHide.connect(
            lambda btn=self._help_btn: (btn.setDown(False), btn.set_hovered(False))
        )
        footer_row.addWidget(self._help_btn)

        layout.addWidget(footer_widget)

        # Wrap in scroll area (matches AI Segmentation)
        scroll_area = QScrollArea()
        scroll_area.setWidget(main_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtC.FrameNoFrame)
        self.setWidget(scroll_area)

        # State
        self._zone_selected = False
        self._activated = False
        self._checking_credits = False
        self._is_free_tier = True  # default hidden until confirmed Pro
        # Seeded paid-tier default. `set_credits` downgrades to "1K" the first
        # time a free-tier user is confirmed, so the dock never lands on a
        # locked resolution.
        self._selected_resolution = "2K"
        # Credit cost per resolution. Used to suffix the Generate/Regenerate
        # button text ("Generate (30 credits)"). Overwritten by
        # set_resolution_credit_costs once the server config loads.
        self._resolution_credit_costs: dict[str, int] = {"1K": 20, "2K": 30, "4K": 40}

        # Layer monitoring
        QgsProject.instance().layersAdded.connect(self._update_layer_warning)
        QgsProject.instance().layersRemoved.connect(self._update_layer_warning)
        self._update_layer_warning()

    def _setup_title_bar(self):
        """Custom title bar matching AI Segmentation style with close button."""
        title_widget = QWidget()
        title_outer = QVBoxLayout(title_widget)
        title_outer.setContentsMargins(0, 0, 0, 0)
        title_outer.setSpacing(0)

        # Title row
        title_row = QHBoxLayout()
        title_row.setContentsMargins(4, 0, 0, 0)
        title_row.setSpacing(0)

        title_label = QLabel(
            "AI Edit by "
            f'<a href="{TERRALAB_URL}" '
            f'style="color: {BRAND_BLUE}; text-decoration: none;">TerraLab</a>'
        )
        title_label.setOpenExternalLinks(True)
        title_row.addWidget(title_label)
        title_row.addStretch()

        icon_size = self.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize)

        float_btn = QToolButton()
        float_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton)
        )
        float_btn.setToolTip(tr("Dock or undock this panel"))
        float_btn.setFixedSize(icon_size + 4, icon_size + 4)
        float_btn.setAutoRaise(True)
        float_btn.clicked.connect(lambda: self.setFloating(not self.isFloating()))
        title_row.addWidget(float_btn)

        close_btn = QToolButton()
        close_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
        )
        close_btn.setToolTip(tr("Close this panel"))
        close_btn.setFixedSize(icon_size + 4, icon_size + 4)
        close_btn.setAutoRaise(True)
        close_btn.clicked.connect(self.close)
        title_row.addWidget(close_btn)

        title_outer.addLayout(title_row)

        # Separator line (like AI Segmentation)
        separator = QFrame()
        separator.setFrameShape(QtC.FrameHLine)
        separator.setFrameShadow(QtC.FrameSunken)
        title_outer.addWidget(separator)

        self.setTitleBarWidget(title_widget)

    def _build_activation_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # --- How to set up AI Edit ---
        self._setup_header = QLabel(tr("Two steps to start using AI Edit"))
        self._setup_header.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: palette(text);"
        )
        layout.addWidget(self._setup_header)

        self._setup_desc = QLabel(
            tr("1. Sign up or sign in on terra-lab.ai to get your key")
            + "\n"
            + tr("2. Paste your key below to activate")
        )
        self._setup_desc.setWordWrap(True)
        self._setup_desc.setStyleSheet(_INSTRUCTION_BOX)
        layout.addWidget(self._setup_desc)

        layout.addSpacing(12)

        # --- Step 1: Create account (inside _signup_section) ---
        self._signup_section = QWidget()
        signup_layout = QVBoxLayout(self._signup_section)
        signup_layout.setContentsMargins(0, 0, 0, 0)
        signup_layout.setSpacing(8)

        step1_label = QLabel(tr("1. Sign up / Sign in"))
        step1_label.setStyleSheet("font-weight: bold; font-size: 12px; color: palette(text);")
        signup_layout.addWidget(step1_label)

        self._login_btn = QPushButton(tr("Get Your Key"))
        self._login_btn.setToolTip(tr("Sign up or sign in to receive your activation key"))
        self._login_btn.setMinimumHeight(36)
        self._login_btn.setCursor(QtC.PointingHandCursor)
        self._login_btn.setStyleSheet(_BTN_GREEN_AUTH)
        self._login_btn.clicked.connect(self._on_login_clicked)
        signup_layout.addWidget(self._login_btn)

        login_hint = QLabel(tr("5 free generations, no credit card required"))
        login_hint.setAlignment(QtC.AlignCenter)
        login_hint.setWordWrap(True)
        login_hint.setStyleSheet("font-size: 11px; color: palette(text);")
        signup_layout.addWidget(login_hint)

        layout.addWidget(self._signup_section)

        layout.addSpacing(8)

        # --- Step 2: Paste key (outside _signup_section for change-key mode) ---
        self._step2_label = QLabel(tr("2. Paste your activation key"))
        self._step2_label.setStyleSheet("font-weight: bold; font-size: 12px; color: palette(text);")
        layout.addWidget(self._step2_label)

        self._key_input_widget = QWidget()
        key_input_layout = QHBoxLayout(self._key_input_widget)
        key_input_layout.setContentsMargins(0, 0, 0, 0)
        key_input_layout.setSpacing(6)
        self._code_input = QLineEdit()
        self._code_input.setPlaceholderText("tl_...")
        self._code_input.setMinimumHeight(28)
        self._code_input.returnPressed.connect(self._on_unlock_clicked)
        key_input_layout.addWidget(self._code_input)

        unlock_btn = QPushButton(tr("Activate"))
        unlock_btn.setToolTip(tr("Validate the activation key you pasted"))
        unlock_btn.setMinimumHeight(28)
        unlock_btn.setMinimumWidth(70)
        unlock_btn.setStyleSheet(_BTN_BLUE_AUTH)
        unlock_btn.clicked.connect(self._on_unlock_clicked)
        key_input_layout.addWidget(unlock_btn)

        layout.addWidget(self._key_input_widget)

        # Cancel button (visible only in change-key mode)
        self._cancel_key_btn = QPushButton(tr("Cancel"))
        self._cancel_key_btn.setToolTip(tr("Discard changes and keep the current key"))
        self._cancel_key_btn.setCursor(QtC.PointingHandCursor)
        self._cancel_key_btn.setStyleSheet(_BTN_GHOST)
        self._cancel_key_btn.clicked.connect(self._on_cancel_change_key)
        self._cancel_key_btn.setVisible(False)
        layout.addWidget(self._cancel_key_btn)

        # Activation message (errors / success)
        self._activation_message = QLabel("")
        self._activation_message.setAlignment(QtC.AlignCenter)
        self._activation_message.setWordWrap(True)
        self._activation_message.setStyleSheet("font-size: 11px;")
        self._activation_message.setVisible(False)
        layout.addWidget(self._activation_message)

        # CTA button displayed on activation flow when usage limit is reached
        self._activation_limit_cta_btn = QPushButton(tr("Subscribe"))
        self._activation_limit_cta_btn.setToolTip(tr("Open the subscription page in your browser"))
        self._activation_limit_cta_btn.setCursor(QtC.PointingHandCursor)
        self._activation_limit_cta_btn.setStyleSheet(_BTN_BLUE_AUTH)
        self._activation_limit_cta_btn.clicked.connect(self._on_activation_limit_cta_clicked)
        self._activation_limit_cta_btn.setVisible(False)
        layout.addWidget(self._activation_limit_cta_btn)
        self._activation_limit_cta_url = ""

        return widget

    def _build_warning_widget(self) -> QWidget:
        """Build yellow warning widget for when no layers are available."""
        widget = QWidget()
        widget.setStyleSheet(
            "QWidget { background-color: rgb(255, 230, 150); "
            "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; }"
            "QLabel { background: transparent; border: none; color: #333333; }"
        )
        warning_layout = QHBoxLayout(widget)
        warning_layout.setContentsMargins(8, 8, 8, 8)
        warning_layout.setSpacing(8)

        icon_label = QLabel()
        style = widget.style()
        warning_icon = style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        icon_label.setPixmap(warning_icon.pixmap(16, 16))
        icon_label.setFixedSize(16, 16)
        warning_layout.addWidget(icon_label, 0, QtC.AlignTop)

        self._warning_text = QLabel(tr("No visible layer. Add imagery to your project."))
        self._warning_text.setWordWrap(True)
        warning_layout.addWidget(self._warning_text, 1)

        return widget

    def _place_reference_widget(self, target: str) -> None:
        """Inject the shared refs strip into the active prompt container.

        ``target`` is "prompt" or "result". The strip lives above the textbox
        inside the bordered container, so the whole input area reads as a
        single ChatGPT-style attachment block.
        """
        if self._reference_widget is None:
            return
        container = (
            self._prompt_container if target == "prompt" else self._result_prompt_container
        )
        container.insert_refs_widget(self._reference_widget)
        # Visibility tracks the store: hidden when 0 refs, shown when ≥1.
        self._reference_widget.setVisible(self._reference_widget.count() > 0)
        self._reference_widget.setEnabled(True)
        # `set_generating(True)` flips this flag on every run, but the
        # generation-done path (set_generation_complete / set_initial_state)
        # never calls set_generating(False), so without this reset the flag
        # stays True and silently blocks +/paste/drop on every subsequent
        # attempt.
        self._reference_widget.set_readonly(False)
        self._sync_attach_buttons()

    def _sync_attach_buttons(self) -> None:
        """Hide paperclips when the refs store is at capacity."""
        if self._reference_widget is None:
            return
        enabled = not self._reference_widget.at_capacity()
        self._prompt_container.set_attach_enabled(enabled)
        self._result_prompt_container.set_attach_enabled(enabled)

    def _open_templates_dialog(self) -> dict | None:
        """Open the prompt templates dialog. Returns selected preset or None."""
        from .prompt_templates_dialog import PromptTemplatesDialog

        dlg = PromptTemplatesDialog(self)
        if dlg.exec():
            preset = dlg.get_selected_preset()
            if preset:
                self.template_selected.emit(
                    str(preset.get("id") or ""),
                    str(preset.get("label") or ""),
                )
            return preset
        return None

    # --- Public methods ---

    def set_activated(self, activated: bool):
        self._activated = activated
        self._activation_widget.setVisible(not activated)
        self._main_widget.setVisible(activated)
        self._settings_btn.setVisible(activated)
        if activated:
            self.hide_trial_info()
            self._update_layer_warning()
            self._cancel_key_btn.setVisible(False)
            self._upgrade_cta.setVisible(self._is_free_tier)
            self.set_launch_state()
        else:
            self._setup_header.setVisible(True)
            self._setup_desc.setVisible(True)
            self._signup_section.setVisible(True)
            self._step2_label.setVisible(True)
            self._key_input_widget.setVisible(True)
            self._cancel_key_btn.setVisible(False)
            self._activation_message.setVisible(False)
            self.hide_activation_limit_cta()

    def show_change_key_mode(self):
        """Show only the key input, no signup flow. For users changing their key."""
        self._activated = False
        self._activation_widget.setVisible(True)
        self._main_widget.setVisible(False)
        self._settings_btn.setVisible(False)
        self._upgrade_cta.setVisible(False)
        self._setup_header.setVisible(False)
        self._setup_desc.setVisible(False)
        self._signup_section.setVisible(False)
        self._step2_label.setVisible(True)
        self._key_input_widget.setVisible(True)
        self._cancel_key_btn.setVisible(True)
        self._activation_message.setVisible(False)
        self.hide_activation_limit_cta()
        self._code_input.clear()
        self._code_input.setFocus()

    def hide_consent(self):
        """Hide the consent checkbox after first generation."""
        self._consent_widget.setVisible(False)

    def set_activation_message(self, text: str, is_error: bool = False):
        # Use brighter variants for dark theme readability
        self.hide_activation_limit_cta()
        color = ERROR_TEXT if is_error else SUCCESS_TEXT
        self._activation_message.setStyleSheet(f"font-size: 11px; color: {color};")
        self._activation_message.setText(text)
        self._activation_message.setVisible(True)

    def show_activation_limit_cta(self, subscribe_url: str):
        self._activation_limit_cta_url = subscribe_url
        self._activation_limit_cta_btn.setText(tr("Subscribe"))
        self._activation_limit_cta_btn.setVisible(True)

    def hide_activation_limit_cta(self):
        self._activation_limit_cta_btn.setVisible(False)
        self._activation_limit_cta_url = ""

    def set_credits(
        self,
        used: int | None = None,
        limit: int | None = None,
        is_free_tier: bool = False,
    ):
        """Update the credits ring + compact count in the footer."""
        was_free = self._is_free_tier
        self._is_free_tier = is_free_tier
        # Restore paid default on free→paid transition (overrides auto-downgrade).
        if was_free and not is_free_tier and self._selected_resolution == "1K":
            self._selected_resolution = "2K"
        if used is not None and limit is not None:
            remaining = max(0, limit - used)
            self._credits_label.setText(f"{remaining} / {limit}")
            self._credits_label.setVisible(True)
            self._credit_ring.set_credits(used, limit, free_tier=is_free_tier)
            self._credit_ring.setVisible(True)
            tooltip = tr("Credits remaining this month: {remaining} / {total}").format(
                remaining=remaining, total=limit
            )
            self._credit_ring.setToolTip(tooltip)
            self._credits_label.setToolTip(tooltip)
        else:
            self._credits_label.setVisible(False)
            self._credit_ring.setVisible(False)
        self._upgrade_cta.setVisible(is_free_tier and self._activated)
        self._refresh_resolution_triggers()
        self._update_generate_button_text()

    def set_zone_selected(self):
        """Zone drawn: show the prompt section and the Generate/Exit row."""
        self._zone_selected = True
        self._hide_status_box()
        self._layer_saved_label.setVisible(False)
        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._result_section.setVisible(False)
        self._prompt_section.setVisible(True)
        self._prompt_container.set_readonly(False)
        self._place_reference_widget("prompt")
        self._consent_widget.setVisible(not has_consent())
        self._generate_btn.setVisible(True)
        self._exit_btn.setVisible(True)
        self._refresh_resolution_triggers()
        self._update_generate_enabled()
        self._update_generate_button_text()
        self._prompt_input.setFocus()

    def set_zone_cleared(self):
        """Zone removed: return to the SELECTING_ZONE state.

        Called when the user right-clicks → Delete zone, presses Esc on the
        canvas, or clicks the × overlay on the rubber band. We go back to
        the 'Select your zone' invitation rather than all the way to
        LAUNCH — the user is mid-flow, just redrawing.
        """
        self.set_selecting_zone_state()

    def _stop_progress_animation(self):
        """Stop the smooth progress animation timer if running."""
        if hasattr(self, "_progress_timer") and self._progress_timer is not None:
            self._progress_timer.stop()

    def set_launch_state(self):
        """LAUNCH: show the entry screen with the 'Launch AI Edit' button.

        Used after activation and whenever the user clicks Exit. The selection
        tool is expected to be inactive in this state (managed by the plugin).
        """
        self._stop_progress_animation()
        self._hide_status_box()
        self._zone_selected = False

        if self._reference_widget is not None:
            self._reference_widget.clear()
            self._reference_widget.setVisible(False)

        self._launch_section.setVisible(True)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._progress_widget.setVisible(False)
        self._result_section.setVisible(False)
        self._layer_saved_label.setVisible(False)
        self._consent_widget.setVisible(False)
        self._generate_btn.setVisible(False)
        self._exit_btn.setVisible(False)

        self._prompt_container.set_readonly(False)
        self._prompt_input.clear()
        self._prompt_input.setFixedHeight(60)
        self._result_prompt_input.clear()
        self._selected_resolution = "1K" if self._is_free_tier else "2K"
        self._refresh_resolution_triggers()
        self._update_layer_warning()

    def set_selecting_zone_state(self):
        """SELECTING_ZONE: invite the user to draw a zone on the canvas.

        Entered after Launch is clicked, or after the user clears their zone.
        The selection tool should be active (managed by the plugin).
        """
        self._stop_progress_animation()
        self._hide_status_box()
        self._zone_selected = False

        if self._reference_widget is not None:
            self._reference_widget.setVisible(False)

        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(True)
        self._prompt_section.setVisible(False)
        self._progress_widget.setVisible(False)
        self._result_section.setVisible(False)
        self._layer_saved_label.setVisible(False)
        self._consent_widget.setVisible(False)
        self._generate_btn.setVisible(False)
        # No Exit in this state — the screen is just the draw invitation.
        self._exit_btn.setVisible(False)
        self._update_layer_warning()

    # Backwards-compat alias for callers that still use the old name.
    def set_prompt_state(self):
        self.set_launch_state()

    def set_generating(self, generating: bool):
        """Toggle generation state -- keep prompt visible but grayed out."""
        self._progress_widget.setVisible(generating)
        self._result_section.setVisible(False)
        self._warning_widget.setVisible(False)
        self._upgrade_cta.setVisible(False)

        if generating:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(0)
            self._hide_status_box()
            self._launch_section.setVisible(False)
            self._select_zone_section.setVisible(False)
            self._prompt_section.setVisible(True)
            self._prompt_container.set_readonly(True)
            # On regenerate the refs widget lives in the result container, so
            # hiding result_section above would also hide the thumbnails.
            # Move it back into the visible prompt container before locking.
            self._place_reference_widget("prompt")
            if self._reference_widget is not None:
                self._reference_widget.set_readonly(True)
            self._consent_widget.setVisible(False)
            self._generate_btn.setVisible(False)
            # Hide Exit during generation: the user shouldn't be tempted to
            # cancel mid-run from this row. The title-bar X still works as
            # an escape hatch.
            self._exit_btn.setVisible(False)
            self._progress_label.setText(tr("Preparing..."))
        else:
            self._prompt_container.set_readonly(False)
            if self._reference_widget is not None:
                self._reference_widget.set_readonly(False)
            self._consent_widget.setVisible(not has_consent() and self._zone_selected)
            self._generate_btn.setVisible(True)
            self._exit_btn.setVisible(True)
            self._refresh_resolution_triggers()
            self._prompt_section.setVisible(True)

    def set_generate_loading(self, loading: bool):
        """Toggle loading state on the Generate button during canvas export."""
        if loading:
            self._generate_btn_original_text = self._generate_btn.text()
            self._generate_btn.setText(tr("Preparing..."))
            self._generate_btn.setEnabled(False)
            self._generate_btn.setStyleSheet(_BTN_DISABLED)
        else:
            text = getattr(self, "_generate_btn_original_text", tr("Generate"))
            self._generate_btn.setText(text)
            self._update_generate_style()

    def set_progress_message(self, message: str, percentage: int = -1):
        """Update the progress label and bar during generation with smooth animation."""
        self._progress_label.setText(message)
        if percentage >= 0:
            self._progress_bar.setRange(0, 100)
            self._progress_target = percentage
            if not hasattr(self, "_progress_timer") or self._progress_timer is None:
                self._progress_timer = QTimer(self)
                self._progress_timer.setInterval(30)
                self._progress_timer.timeout.connect(self._animate_progress)
            if not self._progress_timer.isActive():
                self._progress_timer.start()

    def _animate_progress(self):
        """Smoothly animate progress bar toward target value."""
        current = self._progress_bar.value()
        target = getattr(self, "_progress_target", current)
        if current < target:
            self._progress_bar.setValue(current + 1)
        else:
            if hasattr(self, "_progress_timer") and self._progress_timer is not None:
                self._progress_timer.stop()

    def _show_status_box(self, message: str, box_type: str = "info"):
        """Show a styled status message box (AI Segmentation style)."""
        styles = {
            "error": (
                "QWidget { background-color: rgba(211, 47, 47, 0.25); "
                "border: 1px solid rgba(211, 47, 47, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #ef5350; }",
                QStyle.StandardPixmap.SP_MessageBoxCritical,
            ),
            "success": (
                "QWidget { background-color: rgba(46, 125, 50, 0.25); "
                "border: 1px solid rgba(46, 125, 50, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #66bb6a; }",
                QStyle.StandardPixmap.SP_DialogApplyButton,
            ),
            "warning": (
                "QWidget { background-color: rgb(255, 230, 150); "
                "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #333333; }",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            ),
            "info": (
                "QWidget { background-color: rgba(25, 118, 210, 0.08); "
                "border: 1px solid rgba(25, 118, 210, 0.2); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; }",
                QStyle.StandardPixmap.SP_MessageBoxInformation,
            ),
        }
        style_str, icon_enum = styles.get(box_type, styles["error"])
        self._status_widget.setStyleSheet(style_str)
        icon = self._status_widget.style().standardIcon(icon_enum)
        self._status_icon.setPixmap(icon.pixmap(self._status_icon_size, self._status_icon_size))
        self._status_label.setText(message)
        self._status_widget.setVisible(True)

    def _hide_status_box(self):
        self._status_widget.setVisible(False)
        self._status_label.setText("")
        self._hide_limit_cta()

    def set_status(self, message: str, is_error: bool = False):
        self._hide_limit_cta()
        if not message:
            self._hide_status_box()
        else:
            self._show_status_box(message, "error")
        self._trial_info_box.setVisible(False)

    def set_generation_complete(self, layer_name: str, layer_id: str | None = None):
        """Show RESULT state with iteration options (retry / done)."""
        self._stop_progress_animation()
        self._progress_bar.setValue(100)
        self._progress_widget.setVisible(False)
        self._hide_status_box()

        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        # The result section has its own Exit button, so suppress the prompt
        # row's Exit to avoid duplication.
        self._exit_btn.setVisible(False)
        self._consent_widget.setVisible(False)

        last_prompt = self._prompt_input.toPlainText().strip()
        self._result_prompt_input.setPlainText(last_prompt)
        self._result_prompt_input.moveCursor(QtC.CursorEnd)
        self._result_prompt_container.set_readonly(False)
        self._result_section.setVisible(True)
        self._refresh_resolution_triggers()

        self._place_reference_widget("result")

        self._saved_layer_id = layer_id
        escaped_name = html.escape(layer_name)
        if layer_id:
            link_html = (
                f'<a href="terralab:focus-layer" '
                f'style="color: {SUCCESS_TEXT}; text-decoration: underline;">'
                f'{escaped_name}</a>'
            )
        else:
            link_html = escaped_name
        self._layer_saved_label.setText(
            tr("✓ Saved as {name}").format(name=link_html))
        self._layer_saved_label.setVisible(True)

        self._upgrade_cta.setVisible(self._is_free_tier and self._activated)

    def show_trial_exhausted_info(self, message: str, subscribe_url: str):
        self._hide_limit_cta()
        self._trial_info_text.setText(message)
        self._trial_info_link.setText(
            f'<a href="{subscribe_url}" style="color: {BRAND_BLUE}; '
            f'font-weight: bold;">{tr("Subscribe")}</a>'
        )
        self._trial_info_box.setVisible(True)
        self._hide_status_box()

    def show_usage_limit_info(self, message: str, subscribe_url: str):
        self._show_status_box(message, "error")
        self._trial_info_box.setVisible(False)
        self._limit_cta_url = subscribe_url
        self._limit_cta_btn.setVisible(True)

    def set_checking_credits(self, checking: bool):
        """Show/hide a loading state while credits are being fetched."""
        self._checking_credits = checking
        if checking:
            self._trial_info_box.setVisible(False)
            self._show_status_box(tr("Checking credits..."), "info")
        else:
            self._hide_status_box()

    def hide_trial_info(self):
        self._trial_info_box.setVisible(False)
        self._hide_status_box()
        self._hide_limit_cta()

    def get_activation_key(self) -> str:
        return self._code_input.text().strip()

    def set_activation_key(self, key: str):
        self._code_input.setText(key)

    def get_prompt(self) -> str:
        return self._prompt_input.toPlainText().strip()

    # --- Private methods ---

    def _update_layer_warning(self, *_args):
        """Show/hide warning based on layer availability."""
        if self._zone_selected:
            self._warning_widget.setVisible(False)
            return
        has_layers = bool(QgsProject.instance().mapLayers())
        self._warning_widget.setVisible(not has_layers)

    def _on_settings_btn_clicked(self):
        self.settings_clicked.emit()

    def _on_upgrade_clicked(self):
        from ..core import telemetry
        telemetry.track("subscribe_link_clicked", {"source": "upgrade_cta"})
        QDesktopServices.openUrl(QUrl(get_subscribe_url()))

    def _on_exit_clicked(self):
        """Exit: ask the plugin to cancel + return to LAUNCH state."""
        self.exit_clicked.emit()

    def _on_key_toggle(self, checked: bool):
        pass

    def _on_login_clicked(self):
        """Open terra-lab.ai login page in system browser."""
        import webbrowser
        webbrowser.open("https://terra-lab.ai/login?product=ai-edit")

    def _on_cancel_change_key(self):
        """Cancel key change and restore the activated state."""
        from ..core.activation_manager import get_activation_key
        saved_key = get_activation_key()
        if saved_key:
            self._code_input.setText(saved_key)
            self.set_activated(True)
        else:
            self._signup_section.setVisible(True)
            self._cancel_key_btn.setVisible(False)

    def _on_unlock_clicked(self):
        code = self._code_input.text().strip()
        if not code:
            self.set_activation_message(tr("Enter your code"), is_error=True)
            return
        self.activation_attempted.emit(code)

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _refresh_resolution_triggers(self):
        """Push the current selection / costs / tier into both prompt containers.

        Also coerces the selection to "1K" when a free-tier user is ever
        confirmed (downgrades from the seeded "2K" paid default), so the
        Generate button never quotes a price the user can't actually pay.
        """
        if self._is_free_tier and self._selected_resolution != "1K":
            self._selected_resolution = "1K"
        for container in (self._prompt_container, self._result_prompt_container):
            container.set_resolution_state(
                self._selected_resolution,
                self._resolution_credit_costs,
                self._is_free_tier,
            )

    def _on_resolution_selected(self, label: str):
        """Handle a click inside the resolution dropdown of either container."""
        if self._is_free_tier and label != "1K":
            subscribe_url = get_subscribe_url()
            self._show_status_box(
                tr("The {} resolution is an advanced feature reserved for subscribed users.").format(label)
                + f' <a href="{subscribe_url}" style="color: {BRAND_BLUE}; font-weight: bold;">'
                + tr("Subscribe") + "</a>",
                "warning"
            )
            QTimer.singleShot(5000, self._hide_status_box)
            return

        # Clear any existing status message if switching resolutions
        self._hide_status_box()

        self._selected_resolution = label
        self._refresh_resolution_triggers()
        self._update_generate_button_text()

    def _update_generate_button_text(self):
        """Keep Generate label stable. The 'Select your zone' hint now lives
        in the SELECTING_ZONE section, so the button always reads 'Generate'.
        """
        self._generate_btn.setText(tr("Generate"))
        self._generate_btn.setToolTip(tr("Run the AI edit on your selected zone"))
        self._result_regenerate_btn.setText(tr("Regenerate"))

    def set_resolution_credit_costs(self, costs: dict[str, int]):
        """Update per-resolution credit costs (server config). Costs are
        displayed inside the resolution dropdown via the prompt containers."""
        if costs:
            self._resolution_credit_costs = costs
        self._refresh_resolution_triggers()

    def get_selected_resolution(self) -> str:
        """Return the user-selected resolution label."""
        return self._selected_resolution

    def _on_browse_templates_clicked(self):
        """Open templates dialog. Fill whichever prompt input is active."""
        preset = self._open_templates_dialog()
        if not preset:
            return

        if self._result_section.isVisible():
            self._result_prompt_input.blockSignals(True)
            self._result_prompt_input.setPlainText(_format_template_prompt(preset["prompt"]))
            self._result_prompt_input.blockSignals(False)
            self._result_prompt_input.moveCursor(QtC.CursorEnd)
            self._result_prompt_input.setFocus()
            self._update_generate_enabled()
            self._adjust_result_prompt_height()
        else:
            self._prompt_input.blockSignals(True)
            self._prompt_input.setPlainText(_format_template_prompt(preset["prompt"]))
            self._prompt_input.blockSignals(False)
            self._prompt_input.moveCursor(QtC.CursorEnd)
            self._prompt_input.setFocus()
            self._update_generate_enabled()
            self._adjust_prompt_height()

    def _on_prompt_changed(self):
        self._enforce_prompt_max_length(self._prompt_input)
        self._update_generate_enabled()

    def _on_result_prompt_changed(self):
        self._enforce_prompt_max_length(self._result_prompt_input)

    @staticmethod
    def _enforce_prompt_max_length(text_edit: QTextEdit) -> None:
        """Truncate the prompt to MAX_PROMPT_CHARS."""
        plain = text_edit.toPlainText()
        if len(plain) <= MAX_PROMPT_CHARS:
            return
        cursor_pos = text_edit.textCursor().position()
        text_edit.blockSignals(True)
        try:
            text_edit.setPlainText(plain[:MAX_PROMPT_CHARS])
            cursor = text_edit.textCursor()
            cursor.setPosition(min(cursor_pos, MAX_PROMPT_CHARS))
            text_edit.setTextCursor(cursor)
        finally:
            text_edit.blockSignals(False)

    _PROMPT_MAX_HEIGHT = 400

    def _adjust_prompt_height(self):
        """Auto-expand prompt input (60px min, 200px max). When the cap
        kicks in, snap height to a whole number of text lines so the last
        visible line isn't half-cut at the viewport bottom."""
        self._prompt_input.setFixedHeight(
            self._snapped_prompt_height(self._prompt_input, min_h=60)
        )

    def _adjust_result_prompt_height(self):
        """Auto-expand result prompt input (50px min, 200px max, line-snapped)."""
        self._result_prompt_input.setFixedHeight(
            self._snapped_prompt_height(self._result_prompt_input, min_h=50)
        )

    @classmethod
    def _snapped_prompt_height(cls, text_edit: QTextEdit, min_h: int) -> int:
        # QSS sets `padding: 4px` on QTextEdit, so the viewport is inset 4px
        # top and 4px bottom from the widget edge — 8 total. The previous
        # value of 12 was a stale comment ("6+6") and overshot the cut by 4px.
        padding = 8
        frame = 2 * text_edit.frameWidth()
        target = int(text_edit.document().size().height()) + padding + frame
        if target > cls._PROMPT_MAX_HEIGHT:
            line_h = text_edit.fontMetrics().lineSpacing()
            if line_h > 0:
                n_lines = max(1, (cls._PROMPT_MAX_HEIGHT - padding) // line_h)
                target = n_lines * line_h + padding
            else:
                target = cls._PROMPT_MAX_HEIGHT
        return max(min_h, target)

    def _on_retry_clicked(self):
        """Retry on same zone with the (possibly edited) prompt from result section."""
        prompt = self._result_prompt_input.toPlainText().strip()
        if not prompt:
            return
        if len(prompt) < 10 or len(prompt.split()) < 2:
            self._show_status_box(
                tr("Please describe what you want to change (at least 10 characters, 2 words)."),
                "warning",
            )
            return
        # Transfer prompt to main input for the generation flow
        self._prompt_input.setPlainText(prompt)
        self._result_section.setVisible(False)
        self._hide_status_box()
        self.retry_clicked.emit(prompt)

    def _on_generate_clicked(self):
        prompt = self.get_prompt()
        if not prompt:
            return
        if len(prompt) < 10 or len(prompt.split()) < 2:
            msg = tr("Please describe what you want to change (at least 10 characters, 2 words).")
            self._show_status_box(msg, "warning")
            return
        self._hide_status_box()
        self.generate_clicked.emit(prompt)

    def _on_open_tutorial(self):
        """Open the tutorial URL in the user's default browser."""
        QDesktopServices.openUrl(QUrl(get_tutorial_url()))

    def _on_layer_saved_link_clicked(self, _link: str) -> None:
        """Focus the saved layer in the QGIS Layers panel."""
        layer_id = self._saved_layer_id
        if not layer_id:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return
        try:
            from qgis.utils import iface
        except ImportError:
            return
        if iface is None:
            return
        iface.setActiveLayer(layer)
        tree_view = iface.layerTreeView()
        if tree_view is None:
            return
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer_id) if root is not None else None
        if node is None:
            return
        model = tree_view.layerTreeModel()
        if model is None:
            return
        index = model.node2index(node)
        tree_view.setCurrentIndex(index)
        tree_view.scrollTo(index)

    def _on_contact_us(self, _link=None):
        """Show a dialog with email + Calendly options."""
        from qgis.PyQt.QtWidgets import QApplication, QDialog
        from qgis.PyQt.QtWidgets import QVBoxLayout as _VBox

        calendly_url = "https://calendly.com/barbot-yvann/30min"

        dlg = QDialog(self)
        dlg.setWindowTitle("Contact us")
        dlg.setMinimumWidth(350)
        dlg.setMaximumWidth(450)
        lay = _VBox(dlg)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        msg = QLabel(
            "Bug, question, feature request?\n"
            "We'd love to hear from you!"
        )
        msg.setWordWrap(True)
        lay.addWidget(msg)

        email_label = QLabel(f"<b>{SUPPORT_EMAIL}</b>")
        email_label.setTextInteractionFlags(QtC.TextSelectableByMouse)
        lay.addWidget(email_label)

        copy_btn = QPushButton("Copy email address")
        copy_btn.clicked.connect(
            lambda: (
                QApplication.clipboard().setText(SUPPORT_EMAIL),
                copy_btn.setText("Copied!"),
            )
        )
        lay.addWidget(copy_btn)

        or_label = QLabel("or")
        or_label.setAlignment(QtC.AlignCenter)
        or_label.setStyleSheet("color: palette(text);")
        lay.addWidget(or_label)

        call_btn = QPushButton("Book a video call")
        call_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(calendly_url))
        )
        lay.addWidget(call_btn)

        dlg.exec()

    def _on_show_shortcuts(self, _link=None):
        import sys

        from qgis.PyQt.QtWidgets import QDialog
        from qgis.PyQt.QtWidgets import QVBoxLayout as _VBox

        undo_key = "Cmd+Z" if sys.platform == "darwin" else "Ctrl+Z"
        key_style = (
            "background-color: rgba(128,128,128,0.18);"
            "border: 1px solid rgba(128,128,128,0.35);"
            "border-radius: 3px; padding: 1px 5px; font-family: monospace;"
        )
        k = f"<span style='{key_style}'>{{}}</span>"

        shortcuts_html = (
            "<table cellspacing='4' cellpadding='2'>"
            f"<tr><td colspan='2' style='padding-bottom:2px;'><b>{tr('Editing')}</b></td></tr>"
            f"<tr><td>{k.format('Esc')}</td><td>{tr('Cancel selection')}</td></tr>"
            f"<tr><td>{k.format(undo_key)}</td><td>{tr('Undo')}</td></tr>"
            "</table>"
        )

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Shortcuts"))
        lay = _VBox(dlg)
        lay.setContentsMargins(16, 16, 16, 12)
        label = QLabel(shortcuts_html)
        label.setTextFormat(QtC.RichText)
        lay.addWidget(label)
        ok_btn = QPushButton("OK")
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(dlg.accept)
        lay.addWidget(ok_btn, alignment=QtC.AlignCenter)
        dlg.exec()

    def _on_limit_cta_clicked(self):
        if self._limit_cta_url:
            from ..core import telemetry
            telemetry.track("subscribe_link_clicked", {"source": "limit_cta"})
            QDesktopServices.openUrl(QUrl(self._limit_cta_url))

    def _on_activation_limit_cta_clicked(self):
        if self._activation_limit_cta_url:
            from ..core import telemetry
            telemetry.track("subscribe_link_clicked", {"source": "activation_limit_cta"})
            QDesktopServices.openUrl(QUrl(self._activation_limit_cta_url))

    def _on_consent_changed(self):
        """Re-evaluate Generate button when consent checkbox changes."""
        self._update_generate_enabled()

    def _update_generate_enabled(self):
        has_prompt = bool(self.get_prompt())
        consent_ok = has_consent() or self._consent_check.isChecked()
        enabled = self._zone_selected and has_prompt and consent_ok
        self._generate_btn.setEnabled(enabled)
        self._update_generate_style()
        self._update_generate_button_text()

    def _hide_limit_cta(self):
        self._limit_cta_btn.setVisible(False)
        self._limit_cta_url = ""

    def _update_generate_style(self):
        if self._generate_btn.isEnabled():
            self._generate_btn.setStyleSheet(_BTN_GREEN)
        else:
            self._generate_btn.setStyleSheet(_BTN_DISABLED)

    def _on_escape_pressed(self):
        """Single Escape exit for every step of the AI Edit flow.

        Fires from any focus context (canvas while drawing, prompt textarea,
        post-gen result) because the shortcut sits at the dock with
        WindowShortcut. We only act when the main flow is actually on
        screen so Escape elsewhere in QGIS keeps its default behavior.

        Explicit exception: a generation in progress should NOT be cancelable
        by an accidental Escape. The user paid credits and we already booked
        the work upstream — a stray keystroke shouldn't throw that away. The
        on-screen Stop button is the deliberate way to cancel.
        """
        if not self.isVisible() or not self._main_widget.isVisible():
            return
        if self._progress_widget.isVisible():
            return
        self.exit_clicked.emit()

    def closeEvent(self, event):
        """Cancel generation and disconnect signals on close."""
        self._stop_progress_animation()
        if self._progress_widget.isVisible():
            self.stop_clicked.emit()
        try:
            QgsProject.instance().layersAdded.disconnect(self._update_layer_warning)
            QgsProject.instance().layersRemoved.disconnect(self._update_layer_warning)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)
