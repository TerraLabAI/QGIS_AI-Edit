from __future__ import annotations

from qgis.PyQt.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPalette
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
from ...core.i18n import tr
from ...core.logger import log_debug
from ...core.resolution_labels import (
    DEFAULT_RESOLUTION_CREDIT_COSTS,
    resolution_chip_label,
    resolution_quality_name,
)
from .mime import _file_paths_from_mime, _layers_from_mime, _mime_has_droppable
from .style import _CHIP_HEIGHT, _pencil_icon, _picture_plus_icon
from .widgets import _FooterIconButton, _ResolutionMenuItem, _SubmitTextEdit


class _PromptContainer(QFrame):
    """Bordered frame wrapping prompt textbox + refs strip + footer row.

    Footer layout (bottom row):

        [Prompt library]                   [ 1K ⌄ ]  [ ✎ ]  [ +img Ref image ]

    The whole frame is the drop target so dragging a file or layer anywhere
    over it lights up a single coherent area.
    """

    files_dropped = pyqtSignal(list)
    layers_dropped = pyqtSignal(list)
    attach_clicked = pyqtSignal()
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
    # resolution, markup, Reference). Neutral outlined pill at rest (no bold,
    # no green), leaf-green tint on hover, stronger green when pressed/active -
    # the same TerraLab-green interaction language as the bottom footer icons.
    _CHIP_REST = (
        "QToolButton { background: rgba(128,128,128,0.08);"
        " border: 1px solid rgba(128,128,128,0.40); border-radius: 6px;"
        " padding: 4px 10px; font-size: 12px; color: palette(text); }"
    )
    _CHIP_HOVER = "background: rgba(139,172,39,0.18); border-color: rgba(139,172,39,0.65);"
    _CHIP_PRESSED = "background: rgba(139,172,39,0.32); border-color: rgba(139,172,39,0.85);"
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
    ))
    # Same chip, but property-driven hover for buttons that pop a QMenu - Qt
    # eats the synthetic Leave event when a popup closes, leaving :hover stuck
    # on (see _FooterIconButton).
    _CHIP_BTN_HOVERPROP_STYLE = "".join((
        _CHIP_REST,
        f'QToolButton[hover="true"] {{ {_CHIP_HOVER} }}',
        f'QToolButton[active="true"] {{ {_CHIP_PRESSED} }}',
        _CHIP_TAIL,
    ))
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

        # Markup chip: outlined icon button, same boxed weight as resolution
        # and Reference so the whole footer reads as a row of clear controls.
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        self._markup_chip = QToolButton(self)
        self._markup_chip.setIcon(_pencil_icon(ink))
        self._markup_chip.setIconSize(QSize(18, 18))
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
        # carries a "+" badge; the tooltip explains what it does.
        self._attach_btn = QToolButton(self)
        self._attach_btn.setIcon(_picture_plus_icon(ink))
        self._attach_btn.setIconSize(QSize(18, 18))
        self._attach_btn.setText(tr("Ref image"))
        self._attach_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._attach_btn.setToolTip(
            tr("<b>Reference image</b><br>Click to pick an image or data file from "
               "disk. To use a QGIS layer already in your project, drag it from the "
               "Layers panel into the prompt box. Everything is cropped to your zone.")
        )
        self._attach_btn.setCursor(QtC.PointingHandCursor)
        self._attach_btn.setStyleSheet(self._CHIP_BTN_STYLE)
        self._attach_btn.setFixedHeight(_CHIP_HEIGHT)
        self._attach_btn.clicked.connect(self.attach_clicked.emit)
        footer_row.addWidget(self._attach_btn)

        # Reference counter: a small lime badge tucked INSIDE the right edge of
        # the Ref image button (parented to the button so it reads as part of
        # it, not a detached pill). Shown only when references are attached; the
        # button then reserves extra right padding so the badge never overlaps
        # the label. Dark text on the lime fill reads on both light and dark
        # themes. Hidden with the button when it collapses at capacity.
        self._attach_style_badged = self._CHIP_BTN_STYLE.replace(
            "padding: 4px 10px", "padding: 4px 22px 4px 10px"
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
        so it never forces a horizontal scrollbar. Reference drops to icon-only
        (its tooltip still explains it). Measured, not threshold-based, so it
        stays correct across font/DPI."""
        avail = self.width() - 16
        if avail <= 0:
            return

        def fits() -> bool:
            self._footer_row.invalidate()
            return self._footer_row.sizeHint().width() <= avail

        # Start from the fullest state, then collapse by priority.
        self._attach_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        if not fits():
            self._attach_btn.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonIconOnly
            )

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_footer_fit()
        self._position_ref_badge()

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

    def is_readonly(self) -> bool:
        return self._readonly

    def set_attach_enabled(self, enabled: bool) -> None:
        """Hide the + button when the refs store is at capacity.

        Readonly state is handled by set_readonly, which keeps the button
        visible-but-disabled - do not gate visibility on readonly here.
        """
        self._attach_btn.setVisible(enabled)

    def set_reference_count(self, count: int) -> None:
        """Show a small lime badge inside the Ref image button with the number
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
            self._attach_btn.setStyleSheet(self._CHIP_BTN_STYLE)

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
        self._resolution_btn.setText(
            f"{resolution_chip_label(self._selected_resolution)}  ▾"
        )

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
        for res in ("1K", "2K", "4K"):
            locked = self._free_tier and res != "1K"
            selected = res == self._selected_resolution
            credits = self._resolution_costs.get(res, 0)
            widget = _ResolutionMenuItem(
                resolution_quality_name(res), res, credits, selected, locked,
                self._resolution_menu,
            )
            widget.clicked.connect(lambda r=res: self._on_menu_item_clicked(r))
            action = QWidgetAction(self._resolution_menu)
            action.setDefaultWidget(widget)
            if locked:
                action.setToolTip(tr("Subscribe for more detail"))
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
