





from __future__ import annotations

from typing import TYPE_CHECKING

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...core.logger import log_warning
from ...core.qt_compat import QAction, QShortcut
from ..input_decisions import pick_dock_shortcuts
from ..panels.markup_panel import MarkupPanel
from ..panels.reference_panel import ReferencePanel
from ..panels.vectorize_panel import VectorizePanel
from ..version_strip import VersionStrip
from .design_tokens import (
    BTN_GHOST_QSS,
    BTN_PRIMARY_WIDE_PX,
    BTN_PRIMARY_WIDE_QSS,
    FONT_HINT,
    HINT_QSS,
    INK_2,
    INSET,
    LINE,
    RADIUS_CARD,
    RADIUS_PILL_WIDE,
    SCROLL_AREA_QSS,
)
from .prompt_container import _PromptContainer
from .quota_card import QuotaCard
from .tool_bar import (
    build_rerun_row,
    build_result_tools_row,
    build_tool_toggles,
    tool_available,
)
from .widgets import _SubmitTextEdit

if TYPE_CHECKING:
    from .widget import AIEditDockWidget


class _RetiredSavedLine(QLabel):








    def setVisible(self, visible: bool) -> None:  # noqa: N802
        del visible
        super().setVisible(False)


def _build_result_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:





    dock._result_section = QWidget()
    dock._result_layout = QVBoxLayout(dock._result_section)
    dock._result_layout.setContentsMargins(0, 0, 0, 0)
    dock._result_layout.setSpacing(6)







    dock._result_prompt_widget = QWidget()
    dock._result_prompt_layout = QVBoxLayout(dock._result_prompt_widget)
    dock._result_prompt_layout.setContentsMargins(0, 0, 0, 0)
    dock._result_prompt_layout.setSpacing(6)







    dock._layer_saved_label = _RetiredSavedLine(dock._result_section)
    dock._saved_layer_id: str | None = None






    dock._version_strip = VersionStrip()
    dock._version_strip.version_selected.connect(dock._on_version_selected)
    dock._version_strip.set_saved_layer_probe(dock.saved_layer_probe)


    dock._result_prompt_input = _SubmitTextEdit()
    dock._result_prompt_input.setPlaceholderText(
        get_export_copy(
            "dock.build_result.next_change_placeholder", tr("Describe the next change")
        )
    )
    dock._result_prompt_input.document().setDocumentMargin(0)
    dock._result_prompt_input.setMinimumHeight(50)
    dock._result_prompt_input.setMaximumHeight(50)
    dock._result_prompt_input.submitted.connect(dock._on_retry_clicked)
    dock._result_prompt_input.textChanged.connect(dock._on_result_prompt_changed)
    dock._result_prompt_input.document().documentLayout().documentSizeChanged.connect(
        dock._adjust_result_prompt_height
    )
    dock._result_prompt_container = _PromptContainer(
        dock._result_prompt_input, dock._result_section
    )
    dock._result_prompt_container.templates_clicked.connect(
        dock._on_browse_templates_clicked
    )
    dock._result_prompt_container.resolution_changed.connect(
        dock._on_resolution_selected
    )
    dock._result_prompt_container.markup_clicked.connect(dock.markup_clicked.emit)
    if dock._reference_widget is not None:



        def _gated(handler):
            return dock._gated_by_feature("references", handler)

        dock._result_prompt_container.files_dropped.connect(
            _gated(dock._reference_widget.add_paths)
        )
        dock._result_prompt_container.layers_dropped.connect(
            _gated(dock._reference_widget.add_layers)
        )
        dock._result_prompt_container.reference_clicked.connect(
            _gated(dock.reference_panel_requested.emit)
        )
        dock._result_prompt_input.images_pasted.connect(
            _gated(dock._reference_widget.add_paths)
        )
    dock._result_prompt_layout.addWidget(dock._result_prompt_container)



    dock._result_guidance_hint = QLabel()
    dock._result_guidance_hint.setWordWrap(True)

    dock._result_guidance_hint.setStyleSheet(
        f"QLabel {{ background: {INSET}; border: 1px solid {LINE};"
        f" border-radius: {RADIUS_CARD}px; padding: 8px 10px;"
        f" font-size: {FONT_HINT}px; color: {INK_2}; }}"
    )

    dock._result_guidance_hint.setTextInteractionFlags(
        Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    dock._result_guidance_hint.setOpenExternalLinks(False)
    dock._result_guidance_hint.linkActivated.connect(dock._on_guidance_link_activated)
    dock._result_guidance_hint.setVisible(False)
    dock._result_prompt_layout.addWidget(dock._result_guidance_hint)








    result_actions_row = QHBoxLayout()
    result_actions_row.setContentsMargins(0, 6, 0, 0)
    result_actions_row.setSpacing(8)

    dock._result_regenerate_btn = QPushButton(get_export_copy("dock.build_result.generate_btn", tr("Generate")))
    dock._result_regenerate_btn.setToolTip(
        get_export_copy(
            "dock.build_result.generate_from_tooltip",
            tr("Apply the next change to the picked version, on the same zone"),
        )
    )
    dock._result_regenerate_btn.setCursor(QtC.PointingHandCursor)
    dock._result_regenerate_btn.setStyleSheet(BTN_PRIMARY_WIDE_QSS)
    dock._result_regenerate_btn.clicked.connect(dock._on_retry_clicked)
    result_actions_row.addWidget(dock._result_regenerate_btn, 1)

    dock._result_exit_btn = QPushButton(
        get_export_copy("dock.build_result.new_edit_btn", tr("New edit"))
    )
    dock._result_exit_btn.setToolTip(
        get_export_copy(
            "dock.build_result.new_edit_sessions_tooltip",
            tr("Keep this result on your map and start over on a new zone. The session stays in Sessions."),
        )
    )
    dock._result_exit_btn.setCursor(QtC.PointingHandCursor)

    dock._result_exit_btn.setMinimumWidth(88)
    dock._result_exit_btn.setMinimumHeight(BTN_PRIMARY_WIDE_PX)

    dock._result_exit_btn.setStyleSheet(
        BTN_GHOST_QSS
        + f"QPushButton {{ border-radius: {RADIUS_PILL_WIDE}px;"
        f" min-height: {BTN_PRIMARY_WIDE_PX - 2}px; }}"
    )
    dock._result_exit_btn.clicked.connect(dock._on_exit_clicked)




    dock._result_exit_btn.setParent(dock._result_section)
    dock._result_exit_btn.setVisible(False)

    dock._result_go_again_btn = QPushButton(
        get_export_copy("dock.build_result.go_again_btn", tr("Go again"))
    )
    dock._result_go_again_btn.setToolTip(get_export_copy(
        "dock.build_result.go_again_tooltip",
        tr("Try again, the same edit elsewhere, or a new edit"),
    ))
    dock._result_go_again_btn.setCursor(QtC.PointingHandCursor)
    dock._result_go_again_btn.setMinimumWidth(88)
    dock._result_go_again_btn.setMinimumHeight(BTN_PRIMARY_WIDE_PX)
    dock._result_go_again_btn.setStyleSheet(
        BTN_GHOST_QSS
        + f"QPushButton {{ border-radius: {RADIUS_PILL_WIDE}px;"
        f" min-height: {BTN_PRIMARY_WIDE_PX - 2}px; }}"
        "QPushButton::menu-indicator { width: 0px; }"
    )
    go_again_menu = QMenu(dock._result_go_again_btn)
    go_again_menu.aboutToShow.connect(lambda d=dock, m=go_again_menu: _fill_go_again_menu(d, m))
    dock._result_go_again_btn.setMenu(go_again_menu)
    result_actions_row.addWidget(dock._result_go_again_btn, 0)

    dock._result_prompt_layout.addLayout(result_actions_row)





    dock._result_prompt_layout.addSpacing(4)
    dock._result_prompt_layout.addWidget(dock._version_strip)


    rerun_row = build_rerun_row(dock)
    dock._result_prompt_layout.addWidget(rerun_row)
    dock._result_prompt_layout.addWidget(build_result_tools_row(dock))




    dock._result_layer_line = QLabel()
    dock._result_layer_line.setWordWrap(True)
    dock._result_layer_line.setStyleSheet(HINT_QSS + " padding: 2px 2px 0 2px;")
    dock._result_layer_line.setVisible(False)
    dock._result_prompt_layout.addWidget(dock._result_layer_line)



    dock._after_success_label = QLabel()
    dock._after_success_label.setWordWrap(True)
    dock._after_success_label.setTextFormat(QtC.RichText)
    dock._after_success_label.setStyleSheet(HINT_QSS + " padding: 2px 2px 0 2px;")
    dock._after_success_label.setCursor(QtC.ArrowCursor)
    dock._after_success_label.linkActivated.connect(dock._on_after_success_link)
    dock._after_success_label.setVisible(False)
    dock._result_prompt_layout.addWidget(dock._after_success_label)

    dock._result_layout.addWidget(dock._result_prompt_widget)
    dock._result_prompt_widget.setVisible(False)

    dock._result_section.setVisible(False)
    main_layout.addWidget(dock._result_section)


def _fill_go_again_menu(dock: AIEditDockWidget, menu: QMenu) -> None:


    menu.clear()
    picked = dock._version_strip.selected_index()
    if picked > 0:
        act = menu.addAction(tr("Try again"))
        act.setToolTip(tr("Same prompt, same base: a new variation"))
        act.triggered.connect(dock._result_try_again_btn.click)
    if dock._version_strip.count() > 1:
        act = menu.addAction(tr("Same edit elsewhere"))
        act.setToolTip(tr("Keep this result, draw a new zone with this prompt"))
        act.triggered.connect(dock._result_elsewhere_btn.click)
    act = menu.addAction(get_export_copy("dock.build_result.new_edit_btn", tr("New edit")))
    act.setToolTip(get_export_copy(
        "dock.build_result.new_edit_sessions_tooltip",
        tr("Keep this result on your map and start over on a new zone. The session stays in Sessions."),
    ))
    act.triggered.connect(dock._result_exit_btn.click)
    menu.setToolTipsVisible(True)


def _build_prewall_banner(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:






    dock._prewall_banner = QuotaCard(object_name="aiEditLowCreditCard")
    dock._prewall_banner.primary_clicked.connect(dock._on_prewall_cta_clicked)
    dock._prewall_banner.ghost_clicked.connect(dock._on_prewall_cta_clicked)
    dock._prewall_banner.dismissed.connect(dock._on_prewall_dismissed)
    dock._prewall_url = ""
    main_layout.addWidget(dock._prewall_banner)


def _build_trial_info_box(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:



    dock._trial_info_box = QuotaCard(object_name="aiEditWallCard")
    dock._trial_info_box.primary_clicked.connect(dock._on_trial_info_subscribe_clicked)


    dock._trial_info_box.shown_changed.connect(lambda _up: dock._update_generate_style())
    dock._trial_info_box.shown_changed.connect(lambda _up: dock._update_launch_style())
    dock._trial_info_box.shown_changed.connect(lambda _up: dock._update_result_generate_enabled())

    dock._trial_info_box.shown_changed.connect(dock._on_wall_shown_changed)
    dock._trial_info_box.not_now_clicked.connect(dock._on_wall_not_now)

    dock._trial_info_box.wide_button.setObjectName("aiEditWallGetPro")
    dock._trial_info_box.not_now_button.setObjectName("aiEditWallNotNow")
    dock._trial_info_box.compact_button.setObjectName("aiEditWallRowGetPro")
    dock._trial_info_url = ""
    main_layout.addWidget(dock._trial_info_box)


def _build_side_panels(dock: AIEditDockWidget, layout: QVBoxLayout) -> None:


    dock._markup_panel = MarkupPanel(dock)
    dock._markup_panel.setVisible(False)
    dock._markup_panel.tool_changed.connect(dock.markup_tool_changed.emit)
    dock._markup_panel.color_changed.connect(dock.markup_color_changed.emit)
    dock._markup_panel.clear_clicked.connect(dock.markup_clear_clicked.emit)
    dock._markup_panel.undo_clicked.connect(dock.markup_undo_clicked.emit)
    dock._markup_panel.done_clicked.connect(dock.markup_done_clicked.emit)
    layout.addWidget(dock._markup_panel)



    dock._reference_panel = None
    if dock._reference_widget is not None:
        dock._reference_panel = ReferencePanel(
            dock._reference_store, dock._reference_widget, dock
        )
        dock._reference_panel.setVisible(False)
        dock._reference_panel.done_clicked.connect(dock.reference_done_clicked.emit)
        dock._reference_panel.map_capture_requested.connect(
            dock.reference_capture_requested.emit
        )
        layout.addWidget(dock._reference_panel)


    dock._vectorize_panel = VectorizePanel(dock)
    dock._vectorize_panel.setVisible(False)
    dock._vectorize_panel.done_clicked.connect(dock.vectorize_done_clicked.emit)
    layout.addWidget(dock._vectorize_panel)


def _build_footer(dock: AIEditDockWidget, layout: QVBoxLayout) -> None:





    del layout


    keys = pick_dock_shortcuts(_taken_key_sequences())
    dock._markup_key_seq = QKeySequence(keys["markup"])
    dock._vectorize_key_seq = QKeySequence(keys["vectorize"])
    dock._swipe_key_seq = QKeySequence(keys["swipe"])



    dock._markup_shortcut = _make_dock_shortcut(
        dock, dock._markup_key_seq, dock.markup_clicked.emit
    )

    build_tool_toggles(dock, dock._vectorize_key_seq, dock._swipe_key_seq)
    dock._vectorize_shortcut = _make_dock_shortcut(
        dock,
        dock._vectorize_key_seq,
        lambda btn=dock._vectorize_btn: _click_if_available(btn),
    )
    dock._swipe_shortcut = _make_dock_shortcut(
        dock,
        dock._swipe_key_seq,
        lambda btn=dock._swipe_btn: _click_if_available(btn),
    )


def _taken_key_sequences() -> set[str]:


    taken: set[str] = set()
    portable = QKeySequence.SequenceFormat.PortableText
    try:
        from qgis.utils import iface

        main_window = iface.mainWindow() if iface is not None else None
        if main_window is None:
            return taken
        for action in main_window.menuBar().actions():
            mnemonic = QKeySequence.mnemonic(action.text()).toString(portable)
            if mnemonic:
                taken.add(mnemonic)
        for action in main_window.findChildren(QAction):
            for seq in action.shortcuts():
                text = seq.toString(portable)
                if text:
                    taken.add(text)
    except Exception as err:  # nosec B110
        log_warning(f"Shortcut scan failed: {err}")
    return taken


def _make_dock_shortcut(dock: AIEditDockWidget, seq: QKeySequence, slot) -> QShortcut:
    shortcut = QShortcut(seq, dock)
    shortcut.setContext(QtC.WindowShortcut)
    shortcut.activated.connect(slot)


    shortcut.activatedAmbiguously.connect(slot)
    return shortcut


def _click_if_available(btn) -> None:


    if tool_available(btn) and btn.isEnabled():
        btn.click()


def _wrap_in_scroll_area(dock: AIEditDockWidget, main_widget: QWidget) -> None:


    scroll_area = QScrollArea()
    scroll_area.setWidget(main_widget)
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QtC.FrameNoFrame)
    scroll_area.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
    scroll_area.setStyleSheet(SCROLL_AREA_QSS)
    root = QWidget()
    root_layout = QVBoxLayout(root)
    root_layout.setContentsMargins(0, 0, 0, 0)
    root_layout.setSpacing(0)
    root_layout.addWidget(dock._build_update_gate(), 1)
    root_layout.addWidget(scroll_area, 1)
    dock.setWidget(root)


    dock._scroll_area = scroll_area
