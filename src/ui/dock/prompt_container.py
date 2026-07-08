from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QEvent, QPoint, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QFrame,
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
from ...core.config_store import get_export_copy, get_export_dial
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
from ..icons import icon_for
from . import design_tokens as tokens
from .mime import _file_paths_from_mime, _layers_from_mime, _mime_has_droppable
from .resolution_visuals import (
    RESOLUTION_CHIP_GLYPH_PX,
    resolution_visual,
)
from .style import FAVORITE_STAR_COLOR, _tinted_svg_icon
from .widgets import (
    CHIP_CHEVRON_INSET,
    CHIP_CHEVRON_PX,
    _FooterIconButton,
    _ResolutionChipButton,
    _ResolutionMenuItem,
    _SubmitTextEdit,
)


_FAV_REFRESH_MS = 250

_PROMPT_CHIP_PX = tokens.BTN_SMALL_PX


_PROMPT_CHIP_RADIUS = tokens.RADIUS_CONTROL
_PROMPT_CHIP_GLYPH_PX = 14



_MIN_FIT_WIDTH = 240


class _PromptContainer(QFrame):










    files_dropped = pyqtSignal(list)
    layers_dropped = pyqtSignal(list)





    reference_clicked = pyqtSignal()
    templates_clicked = pyqtSignal()
    resolution_changed = pyqtSignal(str)
    markup_clicked = pyqtSignal()




    _NORMAL_STYLE = (
        f"QFrame#promptContainer {{ background: {tokens.SURFACE};"
        f" border: 1px solid {tokens.LINE_STRONG};"
        f" border-radius: {tokens.RADIUS_COMPOSER}px; }}"
        f'QFrame#promptContainer[focused="true"] {{ border-color: {tokens.ACCENT_BORDER}; }}'
        f'QFrame#promptContainer[dragging="true"] {{ border-color: {tokens.ACCENT_BORDER};'
        f" background: {tokens.ACCENT_TINT}; }}"
    )
    _READONLY_STYLE = (
        f"QFrame#promptContainer {{ background: {tokens.INSET};"
        f" border: 1px solid {tokens.LINE};"
        f" border-radius: {tokens.RADIUS_COMPOSER}px; }}"
    )









    _CHIP_REST = (
        "QToolButton { background: transparent;"
        f" border: 1px solid transparent; border-radius: {_PROMPT_CHIP_RADIUS}px;"
        f" padding: 0 8px; font-size: {tokens.FONT_BODY}px; font-weight: 500;"
        f" color: {tokens.INK_2}; }}"
    )
    _CHIP_HOVER = (
        f"background: {tokens.HOVER}; border-color: {tokens.LINE_STRONG};"
        f" color: {tokens.INK};"
    )
    _CHIP_HELD = (
        f"background: {tokens.HOVER_ON}; border-color: {tokens.LINE_STRONG};"
        f" color: {tokens.INK};"
    )
    _CHIP_OPEN = (
        f"background: {tokens.ACCENT_TINT_ON}; border-color: {tokens.ACCENT_BORDER};"
        f" color: {tokens.INK};"
    )
    _CHIP_FOCUS = f"QToolButton:focus {{ border-color: {tokens.ACCENT_BORDER}; }}"
    _CHIP_TAIL = (
        f"QToolButton:disabled {{ color: {tokens.INK_3};"
        " background: transparent; border-color: transparent; }"
        "QToolButton::menu-indicator { image: none; width: 0; }"
    )
    _CHIP_BTN_STYLE = "".join((
        _CHIP_REST,
        f"QToolButton:hover {{ {_CHIP_HOVER} }}",
        f"QToolButton:pressed {{ {_CHIP_HELD} }}",
        f'QToolButton[active="true"] {{ {_CHIP_OPEN} }}',
        _CHIP_TAIL,
        _CHIP_FOCUS,
    ))



    _CHIP_BTN_HOVERPROP_STYLE = "".join((
        _CHIP_REST,
        f'QToolButton[hover="true"] {{ {_CHIP_HOVER} }}',
        f'QToolButton[active="true"] {{ {_CHIP_OPEN} }}',
        _CHIP_TAIL,
        _CHIP_FOCUS,
    ))


    _RESOLUTION_CHIP_STYLE = _CHIP_BTN_HOVERPROP_STYLE.replace(
        "padding: 0 8px",
        f"padding: 0 {CHIP_CHEVRON_PX + CHIP_CHEVRON_INSET + 4}px 0 8px",
    )


    _MENU_STYLE = tokens.MENU_QSS + "QMenu::item { padding: 0; }"

    _ATTACH_MENU_STYLE = tokens.MENU_QSS


    _FAV_STAR_STYLE = (
        "QToolButton { border: 1px solid transparent; background: transparent; padding: 0;"
        f" border-radius: {tokens.RADIUS_CHIP}px; }}"
        f"QToolButton:hover {{ background: {tokens.HOVER}; }}"
        f"QToolButton:focus {{ border-color: {tokens.ACCENT_BORDER}; }}"
    )

    def __init__(self, text_edit: _SubmitTextEdit, parent=None):
        super().__init__(parent)
        self.setObjectName("promptContainer")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setAcceptDrops(True)



        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self._text_edit = text_edit
        self._base_style = self._NORMAL_STYLE
        self._readonly = False



        self._selected_resolution = "1K"
        self._resolution_costs: dict[str, int] = dict(DEFAULT_RESOLUTION_CREDIT_COSTS)
        self._free_tier = False


        self._resolution_pick_taken = False



        self.setProperty("focused", False)
        self.setProperty("dragging", False)


        text_edit.installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)


        layout.addWidget(text_edit)




        self._fav_btn = QToolButton(self)
        self._fav_btn.setFixedSize(22, 22)
        self._fav_btn.setIconSize(QSize(15, 15))
        self._fav_btn.setStyleSheet(self._FAV_STAR_STYLE)


        text_edit.setViewportMargins(0, 0, 24, 0)
        self._fav_btn.setCursor(QtC.PointingHandCursor)


        self._fav_btn.setCheckable(True)
        self._fav_btn.setAccessibleName(
            get_export_copy("dock.prompt_container.favorite_accessible_name", tr("Favorite"))
        )
        self._fav_btn.clicked.connect(self._on_favorite_clicked)
        self._fav_btn.hide()

        self._fav_refresh_timer = QTimer(self)
        self._fav_refresh_timer.setSingleShot(True)
        self._fav_refresh_timer.setInterval(
            get_export_dial("dock.prompt_container.fav_refresh_ms", _FAV_REFRESH_MS)
        )
        self._fav_refresh_timer.timeout.connect(self._refresh_favorite_star)
        text_edit.textChanged.connect(self._fav_refresh_timer.start)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(4)

        self._templates_btn = _FooterIconButton(self)
        self._templates_btn.setText(get_export_copy("dock.prompt_container.library_btn", tr("Library")))


        self._templates_btn.setToolTip(get_export_copy(
            "dock.prompt_container.library_btn_tooltip_v2",
            tr("<b>Library</b><br>Ready-made prompts, your recent prompts and "
               "your favorites."),
        ))
        self._templates_btn.setAccessibleName(
            get_export_copy("dock.prompt_container.library_btn", tr("Library")))
        self._templates_btn.setCursor(QtC.PointingHandCursor)
        self._templates_btn.setStyleSheet(self._CHIP_BTN_HOVERPROP_STYLE)
        self._templates_btn.setFixedHeight(_PROMPT_CHIP_PX)
        self._chip_glyph(self._templates_btn, "book")
        self._templates_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._templates_btn.clicked.connect(self.templates_clicked.emit)
        footer_row.addWidget(self._templates_btn)

        footer_row.addStretch()

        self._resolution_menu = QMenu(self)
        self._resolution_menu.setStyleSheet(self._MENU_STYLE)

        self._resolution_menu.setToolTipsVisible(True)
        self._resolution_btn = _ResolutionChipButton(self)
        self._resolution_btn.setToolTip(get_export_copy(
            "dock.prompt_container.quality_tooltip",
            tr("<b>Quality</b><br>Higher quality is sharper and more "
               "precise, and costs more credits. Standard (1K), Detailed (2K), "
               "Maximum (4K)."),
        ))
        self._resolution_btn.setCursor(QtC.PointingHandCursor)
        self._resolution_btn.setStyleSheet(self._RESOLUTION_CHIP_STYLE)
        self._resolution_btn.setFixedHeight(_PROMPT_CHIP_PX)



        self._resolution_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._resolution_btn.clicked.connect(self._show_resolution_menu)


        self._resolution_menu.aboutToHide.connect(
            lambda btn=self._resolution_btn: (btn.setDown(False), btn.set_hovered(False))
        )
        footer_row.addWidget(self._resolution_btn)
        self._rebuild_resolution_menu()
        self._update_resolution_label()



        self._markup_chip = _FooterIconButton(self)
        self._chip_glyph(self._markup_chip, "pencil")
        self._markup_chip.setText(
            get_export_copy("dock.prompt_container.markup_chip_draw", tr("Draw"))
        )
        self._markup_chip.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._markup_chip.setCursor(QtC.PointingHandCursor)
        self._markup_chip.setStyleSheet(self._CHIP_BTN_HOVERPROP_STYLE)
        self._markup_chip.setFixedHeight(_PROMPT_CHIP_PX)


        self._markup_chip.setToolTip(get_export_copy(
            "dock.prompt_container.draw_chip_tooltip",
            tr("<b>Draw</b><br>Draw lines, arrows or circles on the map to "
               "show the AI what to change and where. Your drawing is sent with "
               "the prompt as visual guidance."),
        ))
        self._markup_chip.clicked.connect(self.markup_clicked.emit)
        footer_row.addWidget(self._markup_chip)




        self._attach_btn = _FooterIconButton(self)
        self._chip_glyph(self._attach_btn, "image")
        self._attach_btn.setText(
            get_export_copy(
                "dock.prompt_container.reference_chip_plural", tr("References")
            )
        )
        self._attach_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )

        self._attach_btn.setToolTip(get_export_copy(
            "dock.prompt_container.references_chip_tooltip",
            tr("<b>References</b><br>Add an image or data file from disk, or one "
               "of your project's layers, as guidance for the AI. You can also "
               "drag a layer from the Layers panel straight into the prompt box. "
               "Everything is cropped to your zone."),
        ))
        self._attach_btn.setCursor(QtC.PointingHandCursor)
        self._attach_btn.setStyleSheet(self._CHIP_BTN_HOVERPROP_STYLE)
        self._attach_btn.setFixedHeight(_PROMPT_CHIP_PX)
        self._attach_btn.clicked.connect(self.reference_clicked.emit)
        footer_row.addWidget(self._attach_btn)







        self._attach_style_badged = (
            self._CHIP_BTN_HOVERPROP_STYLE
            .replace("padding: 0 8px", "padding: 0 26px 0 8px")
        )
        self._ref_count = QLabel("", self._attach_btn)
        self._ref_count.setAttribute(QtC.WA_TransparentForMouseEvents)
        self._ref_count.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._ref_count.setFixedHeight(16)
        self._ref_count.setMinimumWidth(16)
        self._ref_count.setStyleSheet(
            f"QLabel {{ background: {tokens.ACCENT}; color: {tokens.ON_ACCENT};"
            f" font-size: {tokens.FONT_MICRO}px; font-weight: 700;"
            " border-radius: 8px; padding: 0 4px; }"
        )
        self._ref_count.hide()

        layout.addLayout(footer_row)
        self._footer_row = footer_row
        self.setStyleSheet(self._base_style)



    def _chip_icon(self, name: str, size: int = _PROMPT_CHIP_GLYPH_PX) -> QIcon:

        return icon_for(
            self, name, size, tokens.qcolor(tokens.INK_2),
            disabled_color=tokens.qcolor(tokens.INK_3),
        )

    @staticmethod
    def _chip_glyph(button: _FooterIconButton, name: str) -> None:


        button.set_chip_glyph(
            name, _PROMPT_CHIP_GLYPH_PX, tokens.INK_2, hover_color=tokens.INK)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._text_edit and event.type() in (
            QEvent.Type.FocusIn, QEvent.Type.FocusOut
        ):
            self._set_state_property("focused", event.type() == QEvent.Type.FocusIn)
        return super().eventFilter(watched, event)

    def _set_state_property(self, name: str, value: bool) -> None:
        if bool(self.property(name)) == value:
            return
        self.setProperty(name, value)
        tokens.repolish_widget(self)

    def _apply_footer_fit(self) -> None:








        avail = self.width() - 16
        if avail <= 0:
            return

        def fits() -> bool:
            self._footer_row.invalidate()
            return self._footer_row.sizeHint().width() <= avail


        self._update_resolution_label()
        for chip in (self._templates_btn, self._markup_chip, self._attach_btn):
            chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        if fits():
            return


        self._resolution_btn.setText(self._resolution_label_text(short=True))
        if fits():
            return




        self._templates_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        if fits():
            return



        self._markup_chip.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self._attach_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        fits()

    def minimumSizeHint(self):  # noqa: N802



        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), _MIN_FIT_WIDTH), hint.height())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_footer_fit()
        self._position_ref_badge()
        self._position_fav_star()

    def insert_refs_widget(self, widget: QWidget) -> None:

        old_parent = widget.parentWidget()
        if old_parent is not None and old_parent is not self:
            old_layout = old_parent.layout()
            if old_layout is not None:
                old_layout.removeWidget(widget)
        widget.setParent(self)
        self.layout().insertWidget(0, widget)

        QtC.safe_single_shot(0, self, self._position_fav_star)

    def set_readonly(self, readonly: bool) -> None:
        self._readonly = readonly
        self._base_style = self._READONLY_STYLE if readonly else self._NORMAL_STYLE
        self.setStyleSheet(self._base_style)
        self._text_edit.setReadOnly(readonly)




        self._templates_btn.setEnabled(True)
        self._templates_btn.setToolTip(
            get_export_copy(
                "dock.prompt_container.library_btn_tooltip_readonly",
                tr("Browse the library (view only while generating)."),
            )
            if readonly
            else get_export_copy(
                "dock.prompt_container.library_btn_tooltip",
                tr("Browse templates, your recent prompts, and favorites."),
            )
        )
        self._resolution_btn.setEnabled(not readonly)
        self._markup_chip.setEnabled(not readonly)
        self._attach_btn.setEnabled(not readonly)
        self._refresh_favorite_star()



    def refresh_favorite_star(self) -> None:




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



        text = self._current_prompt_text()
        visible = bool(text) and not self._readonly
        self._fav_btn.setVisible(visible)
        if visible:
            self._set_favorite_visual(prompt_history.is_favorite(text))
            self._position_fav_star()

    def _set_favorite_visual(self, favorited: bool) -> None:
        self._fav_btn.setIcon(
            _tinted_svg_icon("star-filled.svg", tokens.qcolor(FAVORITE_STAR_COLOR))
            if favorited
            else _tinted_svg_icon("star.svg", tokens.qcolor(tokens.INK_3))
        )


        self._fav_btn.setChecked(favorited)
        self._fav_btn.setToolTip(
            get_export_copy(
                "dock.prompt_container.favorite_remove_tooltip", tr("Remove this prompt from your favorites.")
            )
            if favorited
            else get_export_copy(
                "dock.prompt_container.favorite_save_tooltip", tr("Save this prompt to your favorites.")
            )
        )

    def _position_fav_star(self) -> None:


        geo = self._text_edit.geometry()
        self._fav_btn.move(geo.right() - self._fav_btn.width(), geo.top())
        self._fav_btn.raise_()

    def is_readonly(self) -> bool:
        return self._readonly

    def set_attach_enabled(self, enabled: bool) -> None:







        self._attach_btn.setVisible(enabled)

    def set_markup_available(self, available: bool) -> None:





        self._markup_chip.setVisible(available)

    def set_reference_count(self, count: int) -> None:



        if count > 0:
            self._ref_count.setText(str(count))
            self._ref_count.adjustSize()
            self._attach_btn.setStyleSheet(self._attach_style_badged)
            self._ref_count.show()
            self._ref_count.raise_()

            QtC.safe_single_shot(0, self, self._position_ref_badge)
        else:
            self._ref_count.hide()
            self._attach_btn.setStyleSheet(self._CHIP_BTN_HOVERPROP_STYLE)

    def _position_ref_badge(self) -> None:


        if self._ref_count.isHidden():
            return
        btn = self._attach_btn
        badge = self._ref_count
        x = btn.width() - badge.width() - 4
        y = (btn.height() - badge.height()) // 2
        badge.move(max(0, x), max(0, y))
        badge.raise_()



    @staticmethod
    def _project_layer_choices() -> list:







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

        self._selected_resolution = selected
        if costs:
            self._resolution_costs = costs
        self._free_tier = free_tier
        self._rebuild_resolution_menu()

        self._apply_footer_fit()



    def _update_resolution_label(self) -> None:
        self._resolution_btn.setText(self._resolution_label_text(short=False))
        self._update_resolution_glyph()

    def _update_resolution_glyph(self) -> None:



        glyph, hue = resolution_visual(self._selected_resolution)
        self._resolution_btn.set_chip_glyph(
            glyph, RESOLUTION_CHIP_GLYPH_PX, tokens.category_ink(hue))

    def _resolution_label_text(self, short: bool) -> str:


        return (
            self._selected_resolution
            if short
            else resolution_chip_label(self._selected_resolution)
        ) or ""

    def _rebuild_resolution_menu(self) -> None:
        self._resolution_menu.clear()





        header = QLabel(get_export_copy(
            "dock.prompt_container.quality_header", tr("Quality")))
        header.setStyleSheet(
            f"color: {tokens.INK_2}; font-size: {tokens.FONT_HINT}px; font-weight: 600;"
            " padding: 6px 10px 4px 10px; background: transparent;"
        )
        header_action = QWidgetAction(self._resolution_menu)
        header_action.setDefaultWidget(header)
        header_action.setEnabled(False)
        self._resolution_menu.addAction(header_action)





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


            action.triggered.connect(
                lambda _checked=False, r=res: self._on_menu_item_clicked(r)
            )
            if locked:
                action.setToolTip(get_export_copy(
                    "dock.prompt_container.quality_pro_tooltip",
                    tr("Pro unlocks Detailed and Maximum, for printing and zooming in"),
                ))
            self._resolution_menu.addAction(action)

    def _on_menu_item_clicked(self, label: str) -> None:



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



    def _set_glow(self, active: bool) -> None:


        self._set_state_property("dragging", active)

    def dragEnterEvent(self, event):  # noqa: N802
        if self._readonly:
            return
        mime = event.mimeData()
        if _mime_has_droppable(mime):



            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._set_glow(True)
        else:


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

            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        event.ignore()
