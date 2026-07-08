from __future__ import annotations

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ..icons import logo_pixmap, logo_size, pixmap_for
from .design_tokens import (
    BODY_QSS,
    BTN_GHOST_QSS,
    BTN_LINK_QSS,
    BTN_PRIMARY_WIDE_PX,
    BTN_PRIMARY_WIDE_QSS,
    BTN_QUIET_QSS,
    CARD_QSS,
    GREEN,
    HEADLINE_QSS,
    HINT_QSS,
    SPACE_CARD,
    SPACE_STAGE,
    qcolor,
)
from .widgets import _Spinner


_PAIRING_SPINNER_MS = 80

_SIGNIN_MARK_PX = 40


def _make_card(object_name: str = "card") -> QFrame:

    card = QFrame()
    card.setObjectName(object_name)
    card.setAttribute(QtC.WA_StyledBackground, True)
    card.setStyleSheet(CARD_QSS + "QLabel { background: transparent; border: none; }")
    return card


class DockChromeMixin:



    def _setup_title_bar(self):

        from .dock_header import build_dock_header

        self._header_bar = build_dock_header(self)
        self.setTitleBarWidget(self._header_bar)

    def _build_activation_section(self) -> QWidget:



        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 20, 4, 4)
        layout.setSpacing(SPACE_STAGE)

        mark = QLabel()
        mark.setFixedSize(logo_size(_SIGNIN_MARK_PX))
        mark.setPixmap(logo_pixmap(mark, _SIGNIN_MARK_PX))
        layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)

        self._setup_header = QLabel(
            get_export_copy("dock.chrome.setup_header", tr("Edit your map with AI"))
        )
        self._setup_header.setAlignment(QtC.AlignCenter)
        self._setup_header.setWordWrap(True)
        self._setup_header.setStyleSheet(HEADLINE_QSS)
        layout.addWidget(self._setup_header)
        layout.addSpacing(4)


        self._connect_section = QWidget()
        connect_layout = QVBoxLayout(self._connect_section)
        connect_layout.setContentsMargins(0, 0, 0, 0)
        connect_layout.setSpacing(SPACE_STAGE)

        self._connect_btn = QPushButton(
            get_export_copy("dock.chrome.connect_btn", tr("Sign in / Sign up to start"))
        )
        self._connect_btn.setToolTip(get_export_copy(
            "dock.chrome.connect_btn_tooltip", tr("Sign in via your browser to start using AI Edit")
        ))
        self._connect_btn.setFixedHeight(BTN_PRIMARY_WIDE_PX)
        self._connect_btn.setCursor(QtC.PointingHandCursor)
        self._connect_btn.setStyleSheet(BTN_PRIMARY_WIDE_QSS)
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        connect_layout.addWidget(self._connect_btn)




        hint_card = _make_card("card")
        hint_card_layout = QVBoxLayout(hint_card)
        hint_card_layout.setContentsMargins(12, 10, 12, 10)
        hint_card_layout.setSpacing(SPACE_CARD + 2)
        from ...core.paywall_state import advertised_free_generations

        for line in (
            tr("Free plan, {n} AI edits every month. Signing up takes 15 "
               "seconds in your browser.").replace(
                   "{n}", str(advertised_free_generations())),
            get_export_copy(
                "dock.chrome.signin_hint_line2",
                tr("Then type what to change on your imagery, and get the result "
                   "back as a georeferenced layer."),
            ),
        ):
            row = QHBoxLayout()
            row.setSpacing(8)
            check = QLabel()
            check.setFixedSize(14, 16)
            check.setPixmap(pixmap_for(check, "check", 14, qcolor(GREEN)))
            row.addWidget(check, 0, QtC.AlignTop)
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(BODY_QSS)
            row.addWidget(lbl, 1)
            hint_card_layout.addLayout(row)
        connect_layout.addWidget(hint_card)

        layout.addWidget(self._connect_section)


        self._pairing_wait_section = _make_card("card")
        wait_layout = QVBoxLayout(self._pairing_wait_section)
        wait_layout.setContentsMargins(14, 14, 14, 12)
        wait_layout.setSpacing(SPACE_STAGE)


        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._pairing_spinner = _Spinner(16)
        status_row.addWidget(self._pairing_spinner, 0, QtC.AlignTop)
        self._pairing_status = QLabel(
            get_export_copy("dock.chrome.pairing_waiting", tr("Finish signing in on the page that just opened"))
        )
        self._pairing_status.setWordWrap(True)
        self._pairing_status.setStyleSheet(BODY_QSS)
        status_row.addWidget(self._pairing_status, 1)
        wait_layout.addLayout(status_row)



        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACE_CARD)
        self._pairing_reopen_btn = QPushButton(get_export_copy("dock.chrome.pairing_reopen_btn", tr("Open again")))
        self._pairing_reopen_btn.setToolTip(get_export_copy(
            "dock.chrome.pairing_reopen_tooltip", tr("Didn't open? Open the page again")
        ))
        self._pairing_reopen_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_reopen_btn.setStyleSheet(BTN_GHOST_QSS)
        self._pairing_reopen_btn.clicked.connect(self._on_pairing_reopen_clicked)
        btn_row.addWidget(self._pairing_reopen_btn, 1)

        self._pairing_cancel_btn = QPushButton(get_export_copy("dock.chrome.pairing_cancel_btn", tr("Cancel")))
        self._pairing_cancel_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_cancel_btn.setStyleSheet(BTN_QUIET_QSS)
        self._pairing_cancel_btn.clicked.connect(self._on_pairing_cancel_clicked)
        btn_row.addWidget(self._pairing_cancel_btn, 0)
        wait_layout.addLayout(btn_row)




        self._pairing_copy_btn = QPushButton(
            get_export_copy("dock.chrome.pairing_copy_link_btn", tr("Link not opening? Copy link"))
        )
        self._pairing_copy_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_copy_btn.setStyleSheet(BTN_LINK_QSS)
        self._pairing_copy_btn.clicked.connect(self._on_pairing_copy_clicked)
        wait_layout.addWidget(self._pairing_copy_btn, 0, QtC.AlignCenter)

        self._pairing_wait_section.setVisible(False)
        self._pairing_active = False
        layout.addWidget(self._pairing_wait_section)



        self._pairing_anim_timer = QTimer(self)
        self._pairing_anim_timer.setInterval(
            get_export_dial("dock.chrome.pairing_spinner_ms", _PAIRING_SPINNER_MS)
        )
        self._pairing_anim_timer.timeout.connect(self._pairing_spinner.advance)
        self._pending_pairing_code = ""
        self._pairing_link = ""

        layout.addStretch(1)


        self._activation_message = QLabel("")
        self._activation_message.setAlignment(QtC.AlignCenter)
        self._activation_message.setWordWrap(True)
        self._activation_message.setStyleSheet(HINT_QSS)
        self._activation_message.setVisible(False)
        layout.addWidget(self._activation_message)

        return widget

    def _build_warning_widget(self) -> QWidget:





















        wrapper = QWidget()
        wrapper.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        from ..icons import icon_for, pixmap_for
        from . import design_tokens as tokens

        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)




        card = QWidget()
        card.setObjectName("firstRunHero")
        card.setMaximumWidth(320)
        card.setStyleSheet(
            "QWidget#firstRunHero { background: transparent; border: none; }"
            "QLabel { background: transparent; border: none; }"
        )
        col = QVBoxLayout(card)


        col.setContentsMargins(8, 20, 8, 16)
        col.setSpacing(6)

        glyph = QLabel()
        glyph.setAlignment(QtC.AlignCenter)
        glyph.setPixmap(pixmap_for(glyph, "layers", 28, tokens.qcolor(tokens.INK_2)))
        col.addWidget(glyph)
        col.addSpacing(SPACE_STAGE - 6)

        self._warning_title = QLabel(
            get_export_copy("dock.chrome.warning_title", tr("What would you like to edit?"))
        )
        self._warning_title.setWordWrap(True)
        self._warning_title.setAlignment(QtC.AlignCenter)

        self._warning_title.setStyleSheet(HEADLINE_QSS)
        col.addWidget(self._warning_title)







        self._warning_text = QLabel(
            get_export_copy(
                "dock.chrome.warning_text_no_layer",
                tr("Add a layer, or start with a sample."),
            )
        )
        self._warning_text.setWordWrap(True)
        self._warning_text.setAlignment(QtC.AlignCenter)


        self._warning_text.setStyleSheet(HINT_QSS)
        col.addWidget(self._warning_text)
        col.addSpacing(14)





        wide_ghost = tokens.BTN_GHOST_QSS + (
            f"QPushButton {{ min-height: {tokens.BTN_PRIMARY_WIDE_PX - 2}px;"
            f" border-radius: {tokens.RADIUS_PILL_WIDE}px; font-size: {tokens.FONT_BASE}px; }}"
        )
        self._warning_show_layers_mode = False
        self._warning_error_text_active = False
        self._add_layer_btn = QPushButton(get_export_copy("dock.chrome.add_layer_btn", tr("Add a layer…")))
        self._add_layer_btn.setToolTip(
            get_export_copy(
                "dock.chrome.add_layer_btn_data_tooltip",
                tr("Open QGIS's Data Source Manager to add data"),
            )
        )
        self._add_layer_btn.setCursor(QtC.PointingHandCursor)
        self._add_layer_btn.setStyleSheet(wide_ghost)
        self._add_layer_btn.setIcon(icon_for(self._add_layer_btn, "plus", 14, tokens.qcolor(tokens.INK_2)))
        self._add_layer_btn.clicked.connect(self._on_add_layer_clicked)
        col.addWidget(self._add_layer_btn)




        def _rule():
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background-color: {tokens.LINE_STRONG}; border: none;")
            return line

        div = QHBoxLayout()
        div.setContentsMargins(12, 0, 12, 0)
        div.setSpacing(10)
        or_lbl = QLabel(get_export_copy("dock.chrome.or_label", tr("or")))
        or_lbl.setStyleSheet(f"font-size: {tokens.FONT_HINT}px; color: {tokens.INK_3};")
        div.addWidget(_rule(), 1)
        div.addWidget(or_lbl, 0)
        div.addWidget(_rule(), 1)
        col.addSpacing(4)
        col.addLayout(div)
        col.addSpacing(4)





        self._basemap_btn = QPushButton(
            get_export_copy("dock.chrome.basemap_btn", tr("Load a sample image"))
        )
        self._basemap_btn.setCursor(QtC.PointingHandCursor)
        self._basemap_btn.setStyleSheet(tokens.BTN_PRIMARY_WIDE_QSS)
        self._basemap_btn.clicked.connect(self._on_try_example_clicked)
        col.addWidget(self._basemap_btn)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(card, 100)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        return wrapper

    def _sync_warning_actions(self) -> None:











        from qgis.core import QgsProject, QgsRasterLayer

        root = QgsProject.instance().layerTreeRoot()
        has_layers = any(
            isinstance(node.layer(), QgsRasterLayer) for node in root.findLayers()
        )
        self._warning_show_layers_mode = has_layers
        if has_layers:
            self._warning_title.setText(
                get_export_copy("dock.chrome.warning_title_hidden_layers", tr("Your layers are hidden"))
            )
            self._add_layer_btn.setText(get_export_copy("dock.chrome.show_layers_btn", tr("Show my layers")))
            self._add_layer_btn.setToolTip(get_export_copy(
                "dock.chrome.show_layers_btn_tooltip", tr("Re-check your topmost layer in the Layers panel")
            ))


            self._warning_error_text_active = False



            self._warning_text.setText(get_export_copy(
                "dock.chrome.warning_text_hidden_layers_v2",
                tr("Show a layer, or start with a sample."),
            ))
        else:
            self._warning_title.setText(
                get_export_copy("dock.chrome.warning_title", tr("What would you like to edit?"))
            )
            self._add_layer_btn.setText(get_export_copy("dock.chrome.add_layer_btn", tr("Add a layer…")))
            self._add_layer_btn.setToolTip(
                get_export_copy(
                    "dock.chrome.add_layer_btn_data_tooltip",
                    tr("Open QGIS's Data Source Manager to add data"),
                )
            )
            if not self._warning_error_text_active:
                self._warning_text.setText(get_export_copy(
                    "dock.chrome.warning_text_no_layer",
                    tr("Add a layer, or start with a sample."),
                ))

    def _reveal_topmost_layer(self) -> None:





        from qgis.core import QgsProject, QgsRasterLayer

        root = QgsProject.instance().layerTreeRoot()
        nodes = [n for n in root.findLayers() if n.layer() is not None]
        if not nodes:
            return
        node = next(
            (n for n in nodes if isinstance(n.layer(), QgsRasterLayer)),
            nodes[0],
        )
        node.setItemVisibilityChecked(True)
        parent = node.parent()
        while parent is not None and parent is not root:
            parent.setItemVisibilityChecked(True)
            parent = parent.parent()

    def _on_add_layer_clicked(self) -> None:








        if self._warning_show_layers_mode:
            self._reveal_topmost_layer()
            return
        try:
            from qgis.utils import iface

            action = iface.mainWindow().findChild(
                QtC.QAction, "mActionDataSourceManager"
            )
            if action is not None:
                action.trigger()
                return
            iface.openDataSourceManagerPage(None)
        except Exception:
            pass  # nosec B110

    def _on_try_example_clicked(self):








        if self._feature_blocked("demo"):
            return
        self.try_example_requested.emit()

    def _sync_demo_button(self) -> None:


        from ...core.auth.activation_manager import is_feature_enabled

        if getattr(self, "_basemap_btn", None) is not None:
            self._basemap_btn.setVisible(is_feature_enabled("demo"))

    def show_basemap_error(self):




        self._warning_error_text_active = True
        self._warning_text.setText(get_export_copy(
            "dock.chrome.basemap_load_error",
            tr("Couldn't load the example basemap. Check your internet "
               "connection, or add your own layer (GeoTIFF, WMS, XYZ)."),
        ))

    def _place_reference_widget(self, target: str) -> None:






        if self._reference_widget is None:
            return
        container = (
            self._prompt_container if target == "prompt" else self._result_prompt_container
        )
        container.insert_refs_widget(self._reference_widget)

        self._reference_widget.setVisible(self._reference_widget.count() > 0)
        self._reference_widget.setEnabled(True)





        self._reference_widget.set_readonly(False)






        panel = getattr(self, "_reference_panel", None)
        if panel is not None:
            panel.set_readonly(False)
        self._sync_attach_buttons()

    def _place_version_strip(self, target: str) -> None:


















        self._main_layout.removeWidget(self._version_strip)
        self._result_prompt_layout.removeWidget(self._version_strip)
        if target == "generating":

            idx = self._main_layout.indexOf(self._progress_widget)
            self._main_layout.insertWidget(idx, self._version_strip)
        else:


            tools = getattr(self, "_result_tools_row", None)
            idx = (
                self._result_prompt_layout.indexOf(tools) if isinstance(tools, QWidget) else -1
            )
            if idx < 0:
                self._result_prompt_layout.addWidget(self._version_strip)
            else:
                self._result_prompt_layout.insertWidget(idx, self._version_strip)
        self._version_strip.setVisible(
            target != "launch" and self._version_strip.count() > 0
        )

    def _sync_attach_buttons(self) -> None:







        if self._reference_widget is None:
            return
        from ...core.auth.activation_manager import is_feature_enabled

        enabled = is_feature_enabled("references")
        count = self._reference_widget.count()
        if count > 0:
            self._mark_guide_ai_touched()
        for container in (self._prompt_container, self._result_prompt_container):
            container.set_attach_enabled(enabled)
            container.set_reference_count(count)
