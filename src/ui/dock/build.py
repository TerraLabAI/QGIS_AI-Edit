








from __future__ import annotations

from typing import TYPE_CHECKING

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ..icons import icon_for, logo_pixmap, logo_size
from ..onboarding_hint import (
    BLUE_TINT,
    HINT_GUIDE_AI,
    DismissibleHint,
)
from ..panel_helpers import combo_box_qss
from ..reference_images_widget import ReferenceImagesWidget
from . import design_tokens as tokens
from .blocked_reasons import _BLOCK_REASON_QSS
from .build_result import (
    _build_footer,
    _build_prewall_banner,
    _build_result_section,
    _build_side_panels,
    _build_trial_info_box,
    _wrap_in_scroll_area,
)
from .progress_loader import GenerationProgressLoader
from .prompt_container import _PromptContainer
from .quota_card import QuotaCard
from .style import _BTN_GHOST
from .widgets import _GlyphNote, _SubmitTextEdit, _ZoneGestureGlyph, set_link_ink
from .zone_sources import ZoneOfInterestCard

if TYPE_CHECKING:
    from .widget import AIEditDockWidget




_DOT = "·"
_NOTE_STRIP_MARGINS = (4, 2, 4, 2)
_NOTE_STRIP_INK = tokens.INK_3


_STEP_QUESTION_QSS = (
    f"font-size: {tokens.FONT_BASE + 3}px; font-weight: 600; color: {tokens.INK};"
    " background: transparent; border: none;"
)


_FIELD_LABEL_QSS = (
    f"QLabel {{ font-size: {tokens.FONT_HINT}px; color: {tokens.INK_2};"
    " background: transparent; border: none; }"
)

_BTN_GHOST_WIDE_QSS = tokens.BTN_GHOST_QSS + (
    f"QPushButton {{ min-height: {tokens.BTN_PRIMARY_WIDE_PX - 2}px;"
    f" border-radius: {tokens.RADIUS_PILL_WIDE}px; }}"
)

_NOTE_CARD_QSS = (
    "QLabel {{ background: {tint}; border: 1px solid {line};"
    f" border-radius: {tokens.RADIUS_CARD}px; padding: 8px 10px;"
    f" font-size: {tokens.FONT_BODY}px; color: {tokens.INK}; }}}}"
)


def _step_question(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(_STEP_QUESTION_QSS)
    return label


def build_ui(dock: AIEditDockWidget) -> None:


    main_widget = QWidget()
    layout = QVBoxLayout(main_widget)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(8)


    dock._activation_widget = dock._build_activation_section()
    layout.addWidget(dock._activation_widget)



    dock._setup_update_notification(layout)

    main_layout = _build_main_section(dock)
    _build_launch_section(dock, main_layout)
    main_layout.addWidget(dock._warning_widget, 1)
    _build_layer_header(dock, main_layout)
    _build_select_zone_section(dock, main_layout)
    _build_prompt_section(dock, main_layout)
    _build_reference_widget(dock)
    _build_generate_row(dock, main_layout)
    _build_generate_note_strip(dock, main_layout)
    _build_progress_section(dock, main_layout)
    _build_status_section(dock)
    _build_result_section(dock, main_layout)
    _build_result_prompt_header(dock)


    main_layout.addWidget(dock._status_widget)
    main_layout.addWidget(dock._pro_limit_card)

    _build_trial_info_box(dock, main_layout)

    main_layout.addStretch()

    layout.addWidget(dock._main_widget)

    _build_side_panels(dock, layout)






    layout.addStretch()

    _build_prewall_banner(dock, layout)
    _build_footer(dock, layout)
    _wrap_in_scroll_area(dock, main_widget)


def _build_result_prompt_header(dock: AIEditDockWidget) -> None:


    dock._result_prompt_header = _step_question(
        get_export_copy("dock.build.prompt_header", tr("What should the AI change?"))
    )
    layout = dock._result_prompt_layout
    layout.insertWidget(
        layout.indexOf(dock._result_prompt_container), dock._result_prompt_header
    )


def _build_main_section(dock: AIEditDockWidget) -> QVBoxLayout:

    dock._main_widget = QWidget()
    main_layout = QVBoxLayout(dock._main_widget)
    main_layout.setContentsMargins(0, 0, 0, 0)
    main_layout.setSpacing(8)


    dock._main_layout = main_layout





    dock._warning_widget = dock._build_warning_widget()
    dock._warning_widget.setVisible(False)
    return main_layout


_LAUNCH_MARK_PX = 40


def _build_launch_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:

    dock._launch_section = QWidget()
    launch_layout = QVBoxLayout(dock._launch_section)
    launch_layout.setContentsMargins(0, 0, 0, 0)
    launch_layout.setSpacing(8)





    dock._launch_hero = QWidget()
    hero_layout = QVBoxLayout(dock._launch_hero)
    hero_layout.setContentsMargins(4, 20, 4, 8)
    hero_layout.setSpacing(tokens.SPACE_STAGE)
    mark = QLabel()
    mark.setFixedSize(logo_size(_LAUNCH_MARK_PX))
    mark.setPixmap(logo_pixmap(mark, _LAUNCH_MARK_PX))
    hero_layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)
    headline = QLabel(get_export_copy("dock.build.launch_title", tr("Edit your map with AI")))
    headline.setAlignment(QtC.AlignCenter)
    headline.setWordWrap(True)
    headline.setStyleSheet(tokens.HEADLINE_QSS)
    hero_layout.addWidget(headline)
    subline = QLabel(get_export_copy(
        "dock.build.launch_subtitle",
        tr("Outline an area, say what to change"),
    ))
    subline.setAlignment(QtC.AlignCenter)
    subline.setWordWrap(True)
    subline.setStyleSheet(tokens.HINT_QSS)
    hero_layout.addWidget(subline)
    launch_layout.addWidget(dock._launch_hero)



    dock._launch_layout = launch_layout



    launch_row = QHBoxLayout()


    launch_row.setContentsMargins(4, 0, 4, 0)
    launch_row.setSpacing(6)

    dock._launch_btn = QPushButton(get_export_copy("dock.build.launch_btn", tr("Launch AI Edit")))
    dock._launch_btn.setToolTip(get_export_copy("dock.build.launch_btn_tooltip", tr("Start a new AI edit session")))
    dock._launch_btn.setCursor(QtC.PointingHandCursor)
    dock._launch_btn.setStyleSheet(tokens.BTN_PRIMARY_WIDE_QSS)
    dock._launch_btn.clicked.connect(dock.launch_clicked.emit)
    launch_row.addWidget(dock._launch_btn, 1)

    launch_layout.addLayout(launch_row)


    dock._launch_reason_label = QLabel("")
    dock._launch_reason_label.setWordWrap(True)
    dock._launch_reason_label.setAlignment(QtC.AlignCenter)
    dock._launch_reason_label.setStyleSheet(_BLOCK_REASON_QSS)
    dock._launch_reason_label.setVisible(False)
    launch_layout.addWidget(dock._launch_reason_label)





    dock._past_sessions_link = QPushButton(
        get_export_copy("dock.build_result.tutorial_label", tr("Tutorial"))
    )
    dock._past_sessions_link.setToolTip(
        get_export_copy("dock.build_result.tutorial_tooltip", tr("Open the step-by-step tutorial"))
    )
    dock._past_sessions_link.setCursor(QtC.PointingHandCursor)
    dock._past_sessions_link.setStyleSheet(tokens.BTN_QUIET_QSS)
    dock._past_sessions_link.setIcon(
        icon_for(dock._past_sessions_link, "play", 14, tokens.qcolor(tokens.INK_2))
    )
    dock._past_sessions_link.clicked.connect(dock._on_open_tutorial)
    launch_layout.addWidget(dock._past_sessions_link, 0, QtC.AlignCenter)

    dock._launch_section.setVisible(False)
    main_layout.addWidget(dock._launch_section)


def _build_layer_header(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:













    from ..layer_tree_combobox import LayerTreeComboBox

    dock._layer_header = QWidget()
    header_layout = QVBoxLayout(dock._layer_header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.setSpacing(4)

    dock._layer_label = QLabel(get_export_copy("dock.build.layer_label", tr("Image to edit")))
    dock._layer_label.setStyleSheet(_FIELD_LABEL_QSS)
    header_layout.addWidget(dock._layer_label)

    dock._layer_combo = LayerTreeComboBox()
    dock._layer_combo.setToolTip(get_export_copy(
        "dock.build.layer_combo_above_tooltip",
        tr("The layer the AI edits. Visible layers above it are sent as references."),
    ))
    dock._layer_combo.setAccessibleName(tr("Image to edit"))
    dock._layer_combo.setStyleSheet(combo_box_qss())
    dock._layer_combo.setSizePolicy(
        QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
    )
    dock._layer_combo.setMinimumWidth(0)
    header_layout.addWidget(dock._layer_combo)

    dock._layer_header.setVisible(False)
    main_layout.addWidget(dock._layer_header)


def _build_select_zone_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:




    dock._select_zone_section = QWidget()
    dock._select_zone_section.setSizePolicy(
        QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
    )
    sz_layout = QVBoxLayout(dock._select_zone_section)



    sz_layout.setContentsMargins(16, 20, 16, 0)
    sz_layout.setSpacing(10)

    dock._select_zone_icon = _ZoneGestureGlyph(tokens.qcolor(tokens.BRAND_BLUE))
    sz_layout.addWidget(
        dock._select_zone_icon, 0, Qt.AlignmentFlag.AlignHCenter
    )

    dock._select_zone_header = _step_question(
        get_export_copy("dock.build.select_zone_header", tr("Where should the AI edit?"))
    )
    dock._select_zone_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sz_layout.addWidget(dock._select_zone_header)








    dock._select_zone_hint = QLabel(
        get_export_copy("dock.build.select_zone_hint", tr("Click on the map to outline the area to edit."))
    )
    dock._select_zone_hint.setWordWrap(True)
    dock._select_zone_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dock._select_zone_hint.setStyleSheet(
        f"QLabel {{ font-size: {tokens.FONT_BODY}px; color: {tokens.INK_2};"
        " background: transparent; border: none; }"
    )
    sz_layout.addWidget(dock._select_zone_hint)





    dock._zone_source_card = ZoneOfInterestCard(dock._select_zone_section)
    dock._zone_source_card.use_button.clicked.connect(dock._on_zone_card_use_clicked)
    sz_layout.addWidget(dock._zone_source_card)




    zone_pick_row = QHBoxLayout()
    zone_pick_row.setContentsMargins(0, 0, 0, 0)
    zone_pick_row.addStretch()
    dock._zone_source_link = QPushButton(
        get_export_copy("dock.build.zone_source_link", tr("or use an existing zone"))
    )
    dock._zone_source_link.setToolTip(
        get_export_copy(
            "dock.build.zone_source_link_tooltip",
            tr("Pick a zone, a selection or a polygon layer already in the project"),
        )
    )
    dock._zone_source_link.setCursor(QtC.PointingHandCursor)
    dock._zone_source_link.setStyleSheet(tokens.BTN_LINK_QSS)
    dock._zone_source_link.setVisible(False)
    dock._zone_source_link.clicked.connect(dock._on_zone_source_link_clicked)
    zone_pick_row.addWidget(dock._zone_source_link)
    zone_pick_row.addStretch()
    sz_layout.addLayout(zone_pick_row)




    dock._select_zone_notice = QLabel("")
    dock._select_zone_notice.setWordWrap(True)
    dock._select_zone_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dock._select_zone_notice.setStyleSheet(
        f"QLabel {{ font-size: {tokens.FONT_BODY}px; color: {tokens.RED_TEXT};"
        " background: transparent; border: none; }"
    )
    dock._select_zone_notice.setVisible(False)
    sz_layout.addWidget(dock._select_zone_notice)










    zone_exit_row = QHBoxLayout()
    zone_exit_row.setContentsMargins(0, 6, 0, 0)
    zone_exit_row.addStretch()
    dock._select_zone_exit_btn = QPushButton(
        get_export_copy("dock.build.cancel_btn", tr("Cancel"))
    )
    dock._select_zone_exit_btn.setToolTip(
        get_export_copy(
            "dock.build.cancel_btn_tooltip",
            tr("Drop this zone and go back to the start"),
        )
    )
    dock._select_zone_exit_btn.setCursor(QtC.PointingHandCursor)
    dock._select_zone_exit_btn.setMinimumWidth(88)
    dock._select_zone_exit_btn.setStyleSheet(_BTN_GHOST_WIDE_QSS)
    dock._select_zone_exit_btn.clicked.connect(dock._on_exit_clicked)
    zone_exit_row.addWidget(dock._select_zone_exit_btn)
    zone_exit_row.addStretch()
    sz_layout.addLayout(zone_exit_row)

    sz_layout.addStretch(1)

    dock._select_zone_section.setVisible(False)


    main_layout.addWidget(dock._select_zone_section, 1)


def _build_prompt_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:

    dock._prompt_section = QWidget()
    dock._prompt_section.setContentsMargins(0, 0, 0, 0)
    dock._prompt_layout = QVBoxLayout(dock._prompt_section)


    dock._prompt_layout.setContentsMargins(0, 4, 0, 0)
    dock._prompt_layout.setSpacing(6)

    dock._prompt_header = _step_question(
        get_export_copy("dock.build.prompt_header", tr("What should the AI change?"))
    )
    dock._prompt_header.setVisible(True)
    dock._prompt_layout.addWidget(dock._prompt_header)

    dock._prompt_input = _SubmitTextEdit()


    dock._prompt_input.setPlaceholderText(
        get_export_copy(
            "dock.build.prompt_placeholder",
            tr("Describe the change, e.g. turn the fields into a forest"),
        )
    )
    dock._prompt_input.document().setDocumentMargin(0)
    dock._prompt_input.setMinimumHeight(60)
    dock._prompt_input.setMaximumHeight(60)
    dock._prompt_input.textChanged.connect(dock._on_prompt_changed)
    dock._prompt_input.submitted.connect(dock._on_generate_clicked)
    dock._prompt_input.document().documentLayout().documentSizeChanged.connect(
        dock._adjust_prompt_height
    )
    dock._prompt_container = _PromptContainer(dock._prompt_input, dock._prompt_section)
    dock._prompt_container.templates_clicked.connect(dock._on_browse_templates_clicked)
    dock._prompt_container.resolution_changed.connect(dock._on_resolution_selected)
    dock._prompt_container.markup_clicked.connect(dock.markup_clicked.emit)
    dock._prompt_layout.addWidget(dock._prompt_container)






    dock._prompt_guidance_hint = QLabel()
    dock._prompt_guidance_hint.setWordWrap(True)


    dock._prompt_guidance_hint.setStyleSheet(
        _NOTE_CARD_QSS.format(
            tint=tokens.category_tint("sky"), line=tokens.category_line("sky"))
    )
    set_link_ink(dock._prompt_guidance_hint)


    dock._prompt_guidance_hint.setTextInteractionFlags(
        Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    dock._prompt_guidance_hint.setOpenExternalLinks(False)
    dock._prompt_guidance_hint.linkActivated.connect(dock._on_guidance_link_activated)
    dock._prompt_guidance_hint.setVisible(False)
    dock._prompt_layout.addWidget(dock._prompt_guidance_hint)



    _build_guide_ai_hint(dock)

    dock._build_markup_prompt_tip()








    dock._zone_guidance_hint = _GlyphNote(
        "warning", tokens.category_tint("amber"), tokens.category_ink("amber"),
        line=tokens.category_line("amber"))
    dock._zone_guidance_hint.setVisible(False)
    dock._prompt_layout.addWidget(dock._zone_guidance_hint)



    dock._prompt_section.setVisible(False)
    main_layout.addWidget(dock._prompt_section)


def _build_guide_ai_hint(dock: AIEditDockWidget) -> None:













    body = get_export_copy(
        "dock.build.guide_ai_hint_body_v2",
        tr("{ref} and {markup} show the AI what you mean. The {library} has "
           "ready-made prompts."),
        escape=True,
    )
    for token, value in (
        ("{ref}", "<b>{}</b>".format(get_export_copy(
            "dock.prompt_container.reference_chip_plural", tr("References"), escape=True))),
        ("{markup}", "<b>{}</b>".format(get_export_copy(
            "dock.prompt_container.markup_chip_draw", tr("Draw"), escape=True))),
        ("{library}", "<b>{}</b>".format(get_export_copy(
            "dock.prompt_container.library_btn", tr("Library"), escape=True))),
    ):
        body = body.replace(token, value)
    dock._guide_ai_hint = DismissibleHint(
        HINT_GUIDE_AI,
        get_export_copy("dock.build.guide_ai_hint_title", tr("Get better results")),
        body,
        rich_body=True,
        visibility_gate=dock._guide_ai_tip_visible,
        tint=BLUE_TINT,
        parent=dock._prompt_section,
    )
    dock._guide_ai_hint.setVisible(False)
    dock._guide_ai_hint.dismissed.connect(dock._on_guide_ai_dismissed)
    dock._prompt_layout.addWidget(dock._guide_ai_hint)


def _build_reference_widget(dock: AIEditDockWidget) -> None:


    if dock._reference_store is not None:
        dock._reference_widget = ReferenceImagesWidget(
            dock._reference_store, dock
        )
        dock._reference_widget.error_occurred.connect(
            lambda msg: dock._show_status_box(msg, "error")
        )
        dock._reference_widget.error_cleared.connect(dock._hide_status_box)
        dock._reference_widget.images_changed.connect(dock._sync_attach_buttons)
        dock._reference_widget.upsell_requested.connect(dock._show_reference_upsell)






        def _gated(handler):
            return dock._gated_by_feature("references", handler)

        dock._prompt_container.files_dropped.connect(
            _gated(dock._reference_widget.add_paths)
        )
        dock._prompt_container.layers_dropped.connect(
            _gated(dock._reference_widget.add_layers)
        )
        dock._prompt_container.reference_clicked.connect(
            _gated(dock.reference_panel_requested.emit)
        )
        dock._prompt_input.images_pasted.connect(
            _gated(dock._reference_widget.add_paths)
        )




        dock._prompt_container.insert_refs_widget(dock._reference_widget)
        dock._reference_widget.setVisible(dock._reference_widget.count() > 0)
    else:
        dock._reference_widget = None


def _build_generate_row(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:



    generate_row = QHBoxLayout()
    generate_row.setContentsMargins(0, 0, 0, 0)
    generate_row.setSpacing(6)

    dock._generate_btn = QPushButton(get_export_copy("dock.build.generate_btn", tr("Generate")))
    dock._generate_btn.setToolTip(
        get_export_copy("dock.build.generate_btn_tooltip", tr("Edit the selected area with AI (Enter)"))
    )
    dock._generate_btn.setCursor(QtC.PointingHandCursor)
    dock._generate_btn.setEnabled(False)
    dock._update_generate_style()
    dock._generate_btn.clicked.connect(dock._on_generate_clicked)
    dock._generate_btn.setVisible(False)
    generate_row.addWidget(dock._generate_btn, 1)

    dock._exit_btn = QPushButton(get_export_copy("dock.build.cancel_btn", tr("Cancel")))
    dock._exit_btn.setToolTip(
        get_export_copy(
            "dock.build.cancel_btn_tooltip",
            tr("Drop this zone and go back to the start"),
        )
    )
    dock._exit_btn.setCursor(QtC.PointingHandCursor)



    dock._exit_btn.setMinimumWidth(88)
    dock._exit_btn.setStyleSheet(_BTN_GHOST_WIDE_QSS)
    dock._exit_btn.clicked.connect(dock._on_exit_clicked)
    dock._exit_btn.setVisible(False)
    generate_row.addWidget(dock._exit_btn, 0)

    main_layout.addLayout(generate_row)



    dock._generate_reason_label = QLabel("")
    dock._generate_reason_label.setWordWrap(True)
    dock._generate_reason_label.setAlignment(QtC.AlignCenter)
    dock._generate_reason_label.setStyleSheet(_BLOCK_REASON_QSS)
    dock._generate_reason_label.setVisible(False)
    main_layout.addWidget(dock._generate_reason_label)


def _build_generate_note_strip(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:





    note_box = QWidget()
    note_layout = QVBoxLayout(note_box)
    note_layout.setContentsMargins(*_NOTE_STRIP_MARGINS)
    note_layout.setSpacing(4)

    from ...core.auth.activation_manager import get_privacy_url

    privacy_url = get_privacy_url(
        "https://terra-lab.ai/privacy-policy"
        "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit"
        "&utm_content=generate_privacy"
    )
    privacy_link = (
        f'<a href="{privacy_url}" style="color: {tokens.LINK_INK}; text-decoration: none;">'
        f'{tr("Privacy")}</a>'
    )



    body = get_export_copy(
        "privacy_line",
        tr("Your selection and prompt may be processed outside the EU "
           "{dot} {privacy}"),
        escape=True,
    )
    for token, value in (("{dot}", _DOT), ("{privacy}", privacy_link)):
        body = body.replace(token, value)
    dock._generate_privacy_line = QLabel(body)
    dock._generate_privacy_line.setWordWrap(True)
    dock._generate_privacy_line.setTextFormat(Qt.TextFormat.RichText)
    dock._generate_privacy_line.setOpenExternalLinks(True)
    dock._generate_privacy_line.setAlignment(QtC.AlignCenter)
    dock._generate_privacy_line.setStyleSheet(
        f"font-size: {tokens.FONT_HINT}px; color: {_NOTE_STRIP_INK}; background: transparent;"
    )
    note_layout.addWidget(dock._generate_privacy_line)

    dock._generate_note_box = note_box


    note_box.setVisible(False)
    main_layout.addWidget(note_box)


def _build_progress_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:




    dock._progress_widget = QWidget()
    progress_layout = QVBoxLayout(dock._progress_widget)
    progress_layout.setContentsMargins(2, 4, 2, 4)
    progress_layout.setSpacing(0)
    dock._progress_loader = GenerationProgressLoader(
        get_export_copy("dock.build.progress_label", tr("Preparing...")), dock._progress_widget
    )
    dock._progress_bar = dock._progress_loader.bar
    progress_layout.addWidget(dock._progress_loader)
    dock._progress_widget.setVisible(False)
    main_layout.addWidget(dock._progress_widget)


def _build_status_section(dock: AIEditDockWidget) -> None:

    dock._status_widget = QWidget()
    dock._status_widget.setObjectName("statusBox")
    dock._status_widget.setAttribute(QtC.WA_StyledBackground, True)
    dock._status_widget.setVisible(False)
    status_box_layout = QHBoxLayout(dock._status_widget)
    status_box_layout.setContentsMargins(12, 10, 10, 10)
    status_box_layout.setSpacing(8)
    dock._status_icon = QLabel()
    dock._status_icon.setStyleSheet("background: transparent; border: none;")
    _ico = 16
    dock._status_icon.setFixedSize(_ico, _ico)
    dock._status_icon_size = _ico
    status_box_layout.addWidget(
        dock._status_icon, 0, QtC.AlignTop
    )
    dock._status_label = QLabel("")
    dock._status_label.setWordWrap(True)



    dock._status_label.linkActivated.connect(dock._on_status_link)
    dock._status_label.setStyleSheet(
        f"font-size: {tokens.FONT_BODY}px; color: {tokens.INK}; background: transparent; border: none;"
    )
    status_box_layout.addWidget(dock._status_label, 1)





    dock._status_action_btn = QPushButton("")
    dock._status_action_btn.setCursor(QtC.PointingHandCursor)
    dock._status_action_btn.setStyleSheet(_BTN_GHOST)
    dock._status_action_btn.setAutoDefault(False)
    dock._status_action_btn.setVisible(False)
    dock._status_action_handler = None
    dock._status_action_btn.clicked.connect(dock._on_status_action_clicked)
    status_box_layout.addWidget(dock._status_action_btn, 0, QtC.AlignVCenter)




    dock._pro_limit_card = QuotaCard(object_name="aiEditPaidLimitCard")
    dock._pro_limit_card.ghost_clicked.connect(
        lambda: dock._on_pro_contact_clicked(dock._pro_limit_card.ghost_button)
    )
    dock._pro_limit_card.manage_clicked.connect(dock._on_limit_cta_clicked)
    dock._pro_limit_card.manage_button.setToolTip(
        get_export_copy("dock.build.manage_plan_tooltip", tr("Open your dashboard to upgrade or wait for renewal."))
    )
    dock._limit_cta_url = ""
