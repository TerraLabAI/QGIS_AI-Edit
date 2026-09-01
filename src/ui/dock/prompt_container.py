from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPalette
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.entitlements import is_tier_allowed
from ...core.i18n import tr
from ...core.logger import log_debug
from ...core.prompts import prompt_history
from ...core.resolution_labels import (
    DEFAULT_RESOLUTION_CREDIT_COSTS,
    resolution_chip_label,
    resolution_quality_name,
    resolution_tiers,
)
from .mime import _file_paths_from_mime, _layers_from_mime, _mime_has_droppable
from .style import (
    _BTN_LABEL_WEIGHT,
    _CHIP_HEIGHT,
    FAVORITE_STAR_COLOR,
    FOCUS_RING,
    _pencil_icon,
    _picture_plus_icon,
)
from .widgets import _FooterIconButton, _ResolutionMenuItem, _SubmitTextEdit


class _PromptContainer(QFrame):
    """Bordered frame wrapping prompt textbox + refs strip + footer row.

    Footer layout (bottom row):

        [Prompt library]                   [ 1K ⌄ ]  [ ✎ ]  [ +img Reference ]

    The whole frame is the drop target so dragging a file or layer anywhere
    over it lights up a single coherent area.
    """

    files_dropped = pyqtSignal(list)
    layers_dropped = pyqtSignal(list)
    # A project layer picked from the Reference menu (same handler as a drop).
    # The Reference chip now opens the dedicated Reference panel (2026-08-03);
    # the old two-item popup menu is gone. The panel reuses this class's
    # _project_layer_choices / _layer_icon / _ATTACH_MENU_STYLE for its own
    # "From a QGIS layer" flow, so those stay.
    reference_clicked = pyqtSignal()
    templates_clicked = pyqtSignal()
    resolution_changed = pyqtSignal(str)
    markup_clicked = pyqtSignal()

    _NORMAL_STYLE = (
        "QFrame#promptContainer { border: 1px solid rgba(128,128,128,0.3);"
        " border-radius: 4px; background-color: rgba(128,128,128,0.06); }"
    )
    _READONLY_STYLE = (
        "QFrame#promptContainer { border: 1px solid rgba(128,128,128,0.3);"
        " border-radius: 4px; background-color: rgba(128,128,128,0.10); }"
    )
    # Unified footer chip: one look for the whole prompt row (Prompt library,
    # resolution, markup, Reference). Neutral outlined pill at rest (no green),
    # leaf-green tint on hover, stronger green when pressed/active - the same
    # TerraLab-green interaction language as the bottom footer icons. The label
    # carries the shared button weight, like every other labelled button.
    _CHIP_REST = (
        "QToolButton { background: rgba(128,128,128,0.08);"
        " border: 1px solid rgba(128,128,128,0.40); border-radius: 6px;"
        f" padding: 4px 10px; font-size: 12px; color: palette(text);"
        f" {_BTN_LABEL_WEIGHT} }}"
    )
    _CHIP_HOVER = "background: rgba(139,172,39,0.18); border-color: rgba(139,172,39,0.65);"
    _CHIP_PRESSED = "background: rgba(139,172,39,0.32); border-color: rgba(139,172,39,0.85);"
    # Keyboard focus ring. The padding drops by the extra border width so a
    # focused chip keeps the width the footer-fit measurement gave it.
    _CHIP_FOCUS = (
        f"QToolButton:focus {{ border: 2px solid {FOCUS_RING};"
        " padding: 3px 9px; }"
    )
    _CHIP_TAIL = (
        "QToolButton:disabled { color: rgba(128,128,128,0.40);"
        " background: transparent; border-color: rgba(128,128,128,0.20); }"
        "QToolButton::menu-indicator { image: none; width: 0; }"
    )
    _CHIP_BTN_STYLE = "".join((
        _CHIP_REST,
        f"QToolButton:hover {{ {_CHIP_HOVER} }}",
        f"QToolButton:pressed {{ {_CHIP_PRESSED} }}",
        f'QToolButton[active="true"] {{ {_CHIP_PRESSED} }}',
        _CHIP_TAIL,
        _CHIP_FOCUS,
    ))
    # Same chip, but property-driven hover for buttons that pop a QMenu - Qt
    # eats the synthetic Leave event when a popup closes, leaving :hover stuck
    # on (see _FooterIconButton).
    _CHIP_BTN_HOVERPROP_STYLE = "".join((
        _CHIP_REST,
        f'QToolButton[hover="true"] {{ {_CHIP_HOVER} }}',
        f'QToolButton[active="true"] {{ {_CHIP_PRESSED} }}',
        _CHIP_TAIL,
        _CHIP_FOCUS,
    ))
    _MENU_STYLE = (
        "QMenu { background: palette(base); border: 1px solid rgba(128,128,128,0.35);"
        " border-radius: 6px; padding: 4px; }"
        "QMenu::item { background: transparent; padding: 0; }"
        "QMenu::item:selected { background: rgba(128,128,128,0.18); border-radius: 4px; }"
    )
    # The Reference menu uses plain text QActions (not QWidgetAction rows like
    # the resolution menu), so items need real padding; hover keeps the
    # leaf-green chip language.
    _ATTACH_MENU_STYLE = (
        "QMenu { background: palette(base); border: 1px solid rgba(128,128,128,0.35);"
        " border-radius: 6px; padding: 4px; }"
        "QMenu::item { background: transparent; padding: 6px 12px;"
        " border-radius: 4px; color: palette(text); }"
        "QMenu::item:selected { background: rgba(139,172,39,0.18); }"
        "QMenu::item:disabled { color: rgba(128,128,128,0.55); }"
        "QMenu::separator { height: 1px; background: rgba(128,128,128,0.25);"
        " margin: 4px 8px; }"
    )
    # Favorite star, browser-address-bar pattern: a frameless glyph floating in
    # the text area's top-right corner, ghost-gray until starred.
    # The resting rule keeps 1px of padding purely so the focus rule can give
    # it back to the border. Without it the ring grows the hint 48x18 -> 50x20
    # inside a setFixedSize(20, 20) and squeezes the 15px glyph.
    _FAV_STAR_FOCUS = (
        f"QToolButton:focus {{ border: 1px solid {FOCUS_RING};"
        " border-radius: 4px; padding: 0; }"
    )
    _FAV_STAR_REST_STYLE = (
        "QToolButton { border: none; background: transparent; padding: 1px;"
        " font-size: 15px; color: rgba(128,128,128,0.60); }"
        "QToolButton:hover { color: palette(text); }"
        + _FAV_STAR_FOCUS
    )
    _FAV_STAR_FILLED_STYLE = (
        "QToolButton { border: none; background: transparent; padding: 1px;"
        " font-size: 15px; color: " + FAVORITE_STAR_COLOR + "; }"
        + _FAV_STAR_FOCUS
    )

    def __init__(self, text_edit: _SubmitTextEdit, parent=None):
        super().__init__(parent)
        self.setObjectName("promptContainer")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setAcceptDrops(True)
        # Track content height, never soak up panel height: the dock lives in a
        # QScrollArea, so a Preferred policy let the box stretch taller than its
        # content and stranded the placeholder above an empty gap over the footer.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self._text_edit = text_edit
        self._base_style = self._NORMAL_STYLE
        self._readonly = False

        # Resolution state mirrored from the dock widget so the popup can be
        # rebuilt locally without re-reaching into the parent.
        self._selected_resolution = "1K"
        self._resolution_costs: dict[str, int] = dict(DEFAULT_RESOLUTION_CREDIT_COSTS)
        self._free_tier = False
        # Guards the mouse and keyboard wires of the resolution rows against
        # both firing for one pick (see _on_menu_item_clicked).
        self._resolution_pick_taken = False

        # No graphics effect attached at init: applying QGraphicsDropShadowEffect
        # to a parent of a QTextEdit silently breaks the text-insertion caret
        # (the effect pipeline intercepts the QTextEdit's blink timer paint).
        # The drag-over glow is attached on dragEnterEvent and detached on
        # dragLeave / drop - see _set_glow.

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)

        # Slot index 0 is reserved for the refs strip when injected.
        layout.addWidget(text_edit)

        # One-click favorite of the typed prompt, no library round-trip. Same
        # QSettings store as the library's Favorites tab so both stay in sync.
        # Hidden while the box is empty; glyphs stay literal (outside tr()).
        self._fav_btn = QToolButton(self)
        self._fav_btn.setFixedSize(20, 20)
        self._fav_btn.setCursor(QtC.PointingHandCursor)
        # Checkable so a screen reader announces the on/off state; the ★/☆ swap
        # is the sighted half of the same signal.
        self._fav_btn.setCheckable(True)
        self._fav_btn.setAccessibleName(tr("Favorite"))
        self._fav_btn.clicked.connect(self._on_favorite_clicked)
        self._fav_btn.hide()
        # Debounced: is_favorite re-reads QSettings, no need to run it per key.
        self._fav_refresh_timer = QTimer(self)
        self._fav_refresh_timer.setSingleShot(True)
        self._fav_refresh_timer.setInterval(250)
        self._fav_refresh_timer.timeout.connect(self._refresh_favorite_star)
        text_edit.textChanged.connect(self._fav_refresh_timer.start)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(6)

        self._templates_btn = QToolButton(self)
        self._templates_btn.setText(tr("Library"))
        self._templates_btn.setToolTip(tr("Browse templates, your recent prompts, and favorites."))
        self._templates_btn.setCursor(QtC.PointingHandCursor)
        self._templates_btn.setStyleSheet(self._CHIP_BTN_STYLE)
        self._templates_btn.setFixedHeight(_CHIP_HEIGHT)
        self._templates_btn.clicked.connect(self.templates_clicked.emit)
        footer_row.addWidget(self._templates_btn)

        footer_row.addStretch()

        self._resolution_menu = QMenu(self)
        self._resolution_menu.setStyleSheet(self._MENU_STYLE)
        # Allow per-action tooltips (Qt swallows them by default in QMenu).
        self._resolution_menu.setToolTipsVisible(True)
        self._resolution_btn = _FooterIconButton(self)
        self._resolution_btn.setToolTip(
            tr("<b>Output detail</b><br>Higher detail gives a sharper, "
               "more precise result. Standard (1K), Detailed (2K), "
               "Maximum (4K).")
        )
        self._resolution_btn.setCursor(QtC.PointingHandCursor)
        self._resolution_btn.setStyleSheet(self._CHIP_BTN_HOVERPROP_STYLE)
        self._resolution_btn.setFixedHeight(_CHIP_HEIGHT)
        self._resolution_btn.clicked.connect(self._show_resolution_menu)
        # Force the hover tint off when the popup closes - Qt does not
        # synthesise a Leave event in this case (same fix as the help menu).
        self._resolution_menu.aboutToHide.connect(
            lambda btn=self._resolution_btn: (btn.setDown(False), btn.set_hovered(False))
        )
        footer_row.addWidget(self._resolution_btn)
        self._rebuild_resolution_menu()
        self._update_resolution_label()

        # Markup chip: outlined pill, same boxed weight as resolution and
        # Reference so the whole footer reads as a row of clear controls. A
        # visible label (mirroring Reference) replaces the icon-only button -
        # a bare pencil glyph was the weakest signifier in the row.
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        self._markup_chip = QToolButton(self)
        self._markup_chip.setIcon(_pencil_icon(ink))
        self._markup_chip.setIconSize(QSize(18, 18))
        self._markup_chip.setText(tr("Mark up"))
        self._markup_chip.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._markup_chip.setCursor(QtC.PointingHandCursor)
        self._markup_chip.setStyleSheet(self._CHIP_BTN_STYLE)
        self._markup_chip.setFixedHeight(_CHIP_HEIGHT)
        self._markup_chip.setToolTip(
            tr("<b>Mark up</b><br>Draw arrows, shapes, or labels on the map to "
               "show the AI what to change and where. Your sketch is sent with "
               "the prompt as visual guidance.")
        )
        self._markup_chip.clicked.connect(self.markup_clicked.emit)
        footer_row.addWidget(self._markup_chip)

        # Reference: a labelled, outlined pill (Krea-style) so users discover
        # they can feed an image or a project layer as guidance. The icon
        # carries a "+" badge. Clicking opens a small menu: a file from disk,
        # or one of the project's layers - the layer path used to exist only
        # as an undiscoverable drag-and-drop documented in this tooltip.
        self._attach_btn = _FooterIconButton(self)
        self._attach_btn.setIcon(_picture_plus_icon(ink))
        self._attach_btn.setIconSize(QSize(18, 18))
        self._attach_btn.setText(tr("Reference"))
        self._attach_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._attach_btn.setToolTip(
            tr("<b>Reference</b><br>Add an image or data file from disk, or one "
               "of your project's layers, as guidance for the AI. You can also "
               "drag a layer from the Layers panel straight into the prompt box. "
               "Everything is cropped to your zone.")
        )
        self._attach_btn.setCursor(QtC.PointingHandCursor)
        self._attach_btn.setStyleSheet(self._CHIP_BTN_HOVERPROP_STYLE)
        self._attach_btn.setFixedHeight(_CHIP_HEIGHT)
        self._attach_btn.clicked.connect(self.reference_clicked.emit)
        footer_row.addWidget(self._attach_btn)

        # Reference counter: a small lime badge tucked INSIDE the right edge of
        # the Reference button (parented to the button so it reads as part of
        # it, not a detached pill). Shown only when references are attached; the
        # button then reserves extra right padding so the badge never overlaps
        # the label. Dark text on the lime fill reads on both light and dark
        # themes. Hidden with the button when it collapses at capacity.
        # Both paddings are widened: the resting one and the focus ring's, or a
        # focused button would re-centre its label under the badge.
        self._attach_style_badged = (
            self._CHIP_BTN_HOVERPROP_STYLE
            .replace("padding: 4px 10px", "padding: 4px 22px 4px 10px")
            .replace("padding: 3px 9px", "padding: 3px 21px 3px 9px")
        )
        self._ref_count = QLabel("", self._attach_btn)
        self._ref_count.setAttribute(QtC.WA_TransparentForMouseEvents)
        self._ref_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ref_count.setFixedHeight(14)
        self._ref_count.setStyleSheet(
            "QLabel { background: #8bac27; color: #14210a; font-size: 8px;"
            " font-weight: 800; border-radius: 7px; padding: 0 3px; }"
        )
        self._ref_count.hide()

        layout.addLayout(footer_row)
        self._footer_row = footer_row
        self.setStyleSheet(self._base_style)

    # -- public API --------------------------------------------------------

    def _apply_footer_fit(self) -> None:
        """Collapse footer labels when the dock is too narrow for the full row,
        so it never forces a horizontal scrollbar. Priority: the Resolution
        chip drops its quality word for the bare code first (Library has no
        shorter form left to give); the two guidance chips (Mark up,
        Reference) keep their labels the longest, since a bare pencil/picture icon
        is the weakest signifier in the row, and only collapse together at the
        narrowest width. Measured, not threshold-based, so it stays correct
        across font/DPI."""
        avail = self.width() - 16
        if avail <= 0:
            return

        def fits() -> bool:
            self._footer_row.invalidate()
            return self._footer_row.sizeHint().width() <= avail

        # Start from the fullest state, then collapse by priority.
        self._update_resolution_label()
        self._markup_chip.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._attach_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        if fits():
            return

        # Tier 1: Resolution drops its quality word for the bare code ("1K").
        self._resolution_btn.setText(self._resolution_label_text(short=True))
        if fits():
            return

        # Tier 2 (narrowest): the two guidance chips drop to icon-only
        # together - their tooltip still explains what each does.
        self._markup_chip.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self._attach_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        fits()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_footer_fit()
        self._position_ref_badge()
        self._position_fav_star()

    def insert_refs_widget(self, widget: QWidget) -> None:
        """Move a shared refs widget into this container at the top slot."""
        old_parent = widget.parentWidget()
        if old_parent is not None and old_parent is not self:
            old_layout = old_parent.layout()
            if old_layout is not None:
                old_layout.removeWidget(widget)
        widget.setParent(self)
        self.layout().insertWidget(0, widget)
        # The refs strip shifts the text area down; re-anchor the star to it.
        QTimer.singleShot(0, self._position_fav_star)

    def set_readonly(self, readonly: bool) -> None:
        self._readonly = readonly
        self._base_style = self._READONLY_STYLE if readonly else self._NORMAL_STYLE
        self.setStyleSheet(self._base_style)
        self._text_edit.setReadOnly(readonly)
        # Visible-but-disabled while a generation runs - matches the Claude
        # chat input pattern of leaving the footer chrome in place.
        # Templates stays clickable so the user can still browse the library
        # mid-generation; the dialog itself opens in view-only mode.
        self._templates_btn.setEnabled(True)
        self._templates_btn.setToolTip(
            tr("Browse the library (view only while generating).")
            if readonly
            else tr("Browse templates, your recent prompts, and favorites.")
        )
        self._resolution_btn.setEnabled(not readonly)
        self._markup_chip.setEnabled(not readonly)
        self._attach_btn.setEnabled(not readonly)
        self._refresh_favorite_star()

    # -- favorite star -----------------------------------------------------

    def refresh_favorite_star(self) -> None:
        """Public hook for callers that set the prompt with signals BLOCKED
        (template priming, version-prompt mirroring): textChanged never fires
        there, so the star's debounce never starts and a filled prompt showed
        no star until the first keystroke. Call this right after the set."""
        self._refresh_favorite_star()

    def _current_prompt_text(self) -> str:
        return self._text_edit.toPlainText().strip()

    def _on_favorite_clicked(self) -> None:
        text = self._current_prompt_text()
        if not text:
            self._fav_btn.setChecked(False)
            return
        now_favorited = prompt_history.toggle_favorite(text)
        self._set_favorite_visual(now_favorited)
        telemetry.track(te.FAVORITE_TOGGLED, {
            "now_favorited": now_favorited,
            "source": "prompt_box",
        })

    def _refresh_favorite_star(self) -> None:
        """Sync the star with the current text: shown only when a prompt is
        typed and the box is editable, filled when that exact prompt is
        already a favorite."""
        text = self._current_prompt_text()
        visible = bool(text) and not self._readonly
        self._fav_btn.setVisible(visible)
        if visible:
            self._set_favorite_visual(prompt_history.is_favorite(text))
            self._position_fav_star()

    def _set_favorite_visual(self, favorited: bool) -> None:
        self._fav_btn.setText("★" if favorited else "☆")
        # The click already toggled the checked state; realign it with the
        # store, which is what refuses an empty prompt.
        self._fav_btn.setChecked(favorited)
        self._fav_btn.setStyleSheet(
            self._FAV_STAR_FILLED_STYLE if favorited else self._FAV_STAR_REST_STYLE
        )
        self._fav_btn.setToolTip(
            tr("Remove this prompt from your favorites.")
            if favorited
            else tr("Save this prompt to your favorites.")
        )

    def _position_fav_star(self) -> None:
        """Anchor the star inside the text area's top-right corner (the refs
        strip above can shift the text edit down, so anchor to its geometry)."""
        geo = self._text_edit.geometry()
        self._fav_btn.move(geo.right() - self._fav_btn.width() - 2, geo.top() + 2)
        self._fav_btn.raise_()

    def is_readonly(self) -> bool:
        return self._readonly

    def set_attach_enabled(self, enabled: bool) -> None:
        """Hide the Reference chip when the server switches references off.

        Capacity does NOT hide it anymore: the chip opens the Reference
        panel, which the user needs precisely when the store is full.
        Readonly state is handled by set_readonly, which keeps the button
        visible-but-disabled - do not gate visibility on readonly here.
        """
        self._attach_btn.setVisible(enabled)

    def set_markup_available(self, available: bool) -> None:
        """Hide the Mark up chip when the server switched the feature off.

        Same rule as set_attach_enabled: readonly leaves it visible-but-
        disabled, this one removes it from the footer entirely.
        """
        self._markup_chip.setVisible(available)

    def set_reference_count(self, count: int) -> None:
        """Show a small lime badge inside the Reference button with the number
        of attached references, so the control is visibly tied to the
        thumbnails above. Hidden at zero so the footer stays clean."""
        if count > 0:
            self._ref_count.setText(str(count))
            self._ref_count.adjustSize()
            self._attach_btn.setStyleSheet(self._attach_style_badged)
            self._ref_count.show()
            self._ref_count.raise_()
            # Defer so the button has taken its padded width before we anchor.
            QTimer.singleShot(0, self._position_ref_badge)
        else:
            self._ref_count.hide()
            self._attach_btn.setStyleSheet(self._CHIP_BTN_HOVERPROP_STYLE)

    def _position_ref_badge(self) -> None:
        """Anchor the count badge to the inner right edge of the Ref button,
        vertically centered in the padding reserved for it."""
        if self._ref_count.isHidden():
            return
        btn = self._attach_btn
        badge = self._ref_count
        x = btn.width() - badge.width() - 4
        y = (btn.height() - badge.height()) // 2
        badge.move(max(0, x), max(0, y))
        badge.raise_()

    # -- Reference helpers (shared with the Reference panel) ---------------

    @staticmethod
    def _project_layer_choices() -> list:
        """Layers in layer-tree order (the order the user sees in the panel).

        AI Edit's own layers are left out: generated results already have the
        version strip and the history as their re-use paths, and a project
        full of them drowned the user's real data layers in near-identical
        names. The markup annotation layer is guidance, not data. Both stay
        reachable as references via drag-and-drop."""
        try:
            from ..layer_groups import collect_ai_edit_layer_ids
            from ..tools.markup_tools import MARKUP_LAYER_NAME
            own_ids = collect_ai_edit_layer_ids()
            return [
                layer
                for layer in QgsProject.instance().layerTreeRoot().layerOrder()
                if layer is not None
                and layer.id() not in own_ids
                and layer.name() != MARKUP_LAYER_NAME
            ]
        except Exception:
            return []

    @staticmethod
    def _layer_icon(layer) -> QIcon:
        try:
            from qgis.core import QgsIconUtils
            return QgsIconUtils.iconForLayer(layer)
        except Exception:
            return QIcon()

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
        self._resolution_btn.setText(self._resolution_label_text(short=False))

    def _resolution_label_text(self, short: bool) -> str:
        # ▾ (U+25BE) sits on the text baseline; ⌄ (U+2304) renders too low
        # in most system fonts and breaks the visual alignment.
        label = (
            self._selected_resolution
            if short
            else resolution_chip_label(self._selected_resolution)
        )
        return f"{label}  ▾"

    def _rebuild_resolution_menu(self) -> None:
        self._resolution_menu.clear()
        # Title so it reads as "this picks the output resolution", not as
        # another selectable row. Disabled action = non-clickable header.
        header = QLabel(tr("Output detail"))
        header.setStyleSheet(
            "color: palette(text); font-size: 12px; font-weight: 600; "
            "padding: 9px 14px 7px 14px; background: transparent;"
        )
        header_action = QWidgetAction(self._resolution_menu)
        header_action.setDefaultWidget(header)
        header_action.setEnabled(False)
        self._resolution_menu.addAction(header_action)
        sep = QFrame(self._resolution_menu)
        sep.setFrameShape(QtC.FrameHLine)
        sep.setStyleSheet("color: rgba(128,128,128,0.25); margin: 0 8px;")
        sep_action = QWidgetAction(self._resolution_menu)
        sep_action.setDefaultWidget(sep)
        sep_action.setEnabled(False)
        self._resolution_menu.addAction(sep_action)
        # The tier list is served, so a new tier reaches users without a
        # release. An added tier with no label of its own renders under its
        # own key (resolution_quality_name passes an unknown key through).
        for res in resolution_tiers():
            locked = not is_tier_allowed(res, self._free_tier)
            selected = res == self._selected_resolution
            credits = self._resolution_costs.get(res, 0)
            widget = _ResolutionMenuItem(
                resolution_quality_name(res), res, credits, selected, locked,
                self._resolution_menu,
            )
            widget.clicked.connect(lambda r=res: self._on_menu_item_clicked(r))
            action = QWidgetAction(self._resolution_menu)
            action.setDefaultWidget(widget)
            # The row widget only sees the mouse. Arrow keys plus Enter go
            # through the action, so the keyboard needs this second wire.
            action.triggered.connect(
                lambda _checked=False, r=res: self._on_menu_item_clicked(r)
            )
            if locked:
                action.setToolTip(tr("Pro gives you 2K and 4K, for printing and zooming in"))
            self._resolution_menu.addAction(action)

    def _on_menu_item_clicked(self, label: str) -> None:
        # One mouse click can reach both wires (the row widget's own release and
        # the action Qt triggers under it), so the first pick of a menu session
        # wins and the second call is dropped. Reset on every popup.
        if self._resolution_pick_taken:
            return
        self._resolution_pick_taken = True
        self._resolution_menu.close()
        self.resolution_changed.emit(label)

    def _show_resolution_menu(self) -> None:
        if not self._resolution_btn.isEnabled():
            return
        self._resolution_pick_taken = False
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
        mime = event.mimeData()
        if _mime_has_droppable(mime):
            # Force Copy: a Layers-panel drag proposes MoveAction, which makes
            # QGIS remove the layer from the tree once we accept. We only want a
            # copy as a reference, never to move the user's layer.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._set_glow(True)
        else:
            # Diagnostic for drags we reject (e.g. some Finder file types):
            # surface the MIME formats and URLs so we can see what arrived.
            urls = [u.toString() for u in mime.urls()] if mime.hasUrls() else []
            log_debug(f"Drag rejected: formats={list(mime.formats())} urls={urls}")

    def dragMoveEvent(self, event):  # noqa: N802
        if self._readonly:
            return
        if _mime_has_droppable(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

    def dragLeaveEvent(self, event):  # noqa: N802
        self._set_glow(False)
        event.accept()

    def dropEvent(self, event):  # noqa: N802
        self._set_glow(False)
        if self._readonly:
            event.ignore()
            return
        mime = event.mimeData()
        paths = _file_paths_from_mime(mime)
        layers = _layers_from_mime(mime)
        if paths:
            self.files_dropped.emit(paths)
        if layers:
            self.layers_dropped.emit(layers)
        if paths or layers:
            # Copy, not move - never let the source remove the user's layer.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        event.ignore()
