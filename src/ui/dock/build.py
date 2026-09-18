"""Widget-tree construction for AIEditDockWidget, extracted from __init__.

build_ui is the single entry point; the helper order is load-bearing (Qt
construction order drives layout, stacking, and tab order), so the helpers
concatenated top to bottom replay the original __init__ statement for
statement. The result section, panels, footer, and scroll wrap live in
build_result.py to keep both files in the repo's size sweet spot.
"""

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

if TYPE_CHECKING:
    from .widget import AIEditDockWidget

# Splits the note into what leaves the machine and where to read more. A middle
# dot rather than a full stop, so the two halves read as one line at a glance.
# Glyph, so it stays outside tr().
_DOT = "·"
_NOTE_STRIP_MARGINS = (4, 2, 4, 2)
_NOTE_STRIP_INK = tokens.INK_3

# The question a step asks, AI Agent's home title: one step above the body.
_STEP_QUESTION_QSS = (
    f"font-size: {tokens.FONT_BASE + 3}px; font-weight: 600; color: {tokens.INK};"
    " background: transparent; border: none;"
)
# A field's label over its control: AI Segmentation's "Image to segment" look,
# the hint size in the second ink, so the field reads as the thing to set.
_FIELD_LABEL_QSS = (
    f"QLabel {{ font-size: {tokens.FONT_HINT}px; color: {tokens.INK_2};"
    " background: transparent; border: none; }"
)
# The secondary of a 36 px row: the ghost pill at the wide primary's height.
_BTN_GHOST_WIDE_QSS = tokens.BTN_GHOST_QSS + (
    f"QPushButton {{ min-height: {tokens.BTN_PRIMARY_WIDE_PX - 2}px;"
    f" border-radius: {tokens.RADIUS_PILL_WIDE}px; }}"
)
# A soft note card under the prompt: the tint of its kind, a hairline, 10 px.
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
    """Build the whole dock widget tree in the original construction order."""
    # Main content
    main_widget = QWidget()
    layout = QVBoxLayout(main_widget)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(8)

    # --- Activation section ---
    dock._activation_widget = dock._build_activation_section()
    layout.addWidget(dock._activation_widget)

    # Above everything: an update the user scrolls past is an update they never
    # install, and this card is the one message that outranks the current step.
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

    # Status box + CTA placed after result section so they always appear below
    main_layout.addWidget(dock._status_widget)
    main_layout.addWidget(dock._pro_limit_card)

    _build_trial_info_box(dock, main_layout)

    main_layout.addStretch()

    layout.addWidget(dock._main_widget)

    _build_side_panels(dock, layout)

    # The Before/After swipe has no dock panel: it's a toggle on the
    # footer Before/After button that arms a map tool on the canvas.
    # See SwipeController in swipe_panel.py and the wiring in plugin.py.

    # Spacer to push footer to bottom
    layout.addStretch()

    _build_prewall_banner(dock, layout)
    _build_footer(dock, layout)
    _wrap_in_scroll_area(dock, main_widget)


def _build_result_prompt_header(dock: AIEditDockWidget) -> None:
    # The result screen asks the same question as the prompt step, right above
    # its prompt, so the box under the result reads as "the next change".
    dock._result_prompt_header = _step_question(
        get_export_copy("dock.build.prompt_header", tr("What should the AI change?"))
    )
    layout = dock._result_prompt_layout
    layout.insertWidget(
        layout.indexOf(dock._result_prompt_container), dock._result_prompt_header
    )


def _build_main_section(dock: AIEditDockWidget) -> QVBoxLayout:
    # --- Main content section ---
    dock._main_widget = QWidget()
    main_layout = QVBoxLayout(dock._main_widget)
    main_layout.setContentsMargins(0, 0, 0, 0)
    main_layout.setSpacing(8)
    # Kept so the version strip can be re-homed under the progress bar while
    # a generation runs (see _place_version_strip).
    dock._main_layout = main_layout

    # Empty-canvas hero (no visible layer). Built here, added by build_ui
    # AFTER the launch section: Launch no longer hides behind the card, and on
    # a short dock the button plus its reason must be the part that stays in
    # view, with the card scrolling under it.
    dock._warning_widget = dock._build_warning_widget()
    dock._warning_widget.setVisible(False)
    return main_layout


_LAUNCH_MARK_PX = 40


def _build_launch_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # --- Launch section (entry screen, matches AI Segmentation pattern) ---
    dock._launch_section = QWidget()
    launch_layout = QVBoxLayout(dock._launch_section)
    launch_layout.setContentsMargins(0, 0, 0, 0)
    launch_layout.setSpacing(8)

    # ChatGPT's home screen line: the mark, one headline and one plain line
    # above the primary, centered, so the idle dock greets instead of showing
    # a lone button at the top. Hidden with Past sessions when the
    # empty-canvas card owns the screen (tools_footer._update_layer_warning).
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
    # The layer header joins this layout between the hero and Launch on the
    # home screen (generation_state._place_layer_header), like AI
    # Segmentation's picker above Start.
    dock._launch_layout = launch_layout

    # Launch sits in a row of its own so the reason line below it lines up
    # with the button rather than with the section margin.
    launch_row = QHBoxLayout()
    # The hero's 4 px side inset, so Launch lines up with the headline column
    # and with the signed-out screen's primary in the same place.
    launch_row.setContentsMargins(4, 0, 4, 0)
    launch_row.setSpacing(6)

    dock._launch_btn = QPushButton(get_export_copy("dock.build.launch_btn", tr("Launch AI Edit")))
    dock._launch_btn.setToolTip(get_export_copy("dock.build.launch_btn_tooltip", tr("Start a new AI edit session")))
    dock._launch_btn.setCursor(QtC.PointingHandCursor)
    dock._launch_btn.setStyleSheet(tokens.BTN_PRIMARY_WIDE_QSS)
    dock._launch_btn.clicked.connect(dock.launch_clicked.emit)
    launch_row.addWidget(dock._launch_btn, 1)

    launch_layout.addLayout(launch_row)

    # Why Launch is greyed, one muted line, written by set_launch_block_reason.
    dock._launch_reason_label = QLabel("")
    dock._launch_reason_label.setWordWrap(True)
    dock._launch_reason_label.setAlignment(QtC.AlignCenter)
    dock._launch_reason_label.setStyleSheet(_BLOCK_REASON_QSS)
    dock._launch_reason_label.setVisible(False)
    launch_layout.addWidget(dock._launch_reason_label)

    # The tutorial, right under Launch (AI Agent's "Examples · Tutorial"
    # row): a quiet play link. Past sessions moved to the header clock. The
    # attribute keeps its old name because the layer-warning logic shows and
    # hides it with the Launch button.
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
    """"Image to edit": the raster the edit starts from.

    AI Segmentation's picker, same words and same look (Yvann, 2026-09-18): a
    small label over the combo, above Launch on the home screen, then locked
    with its chevron gone from the zone commit to the result so the user
    always reads which layer the run started from. The input image is this
    ONE layer rendered at the zone; the visible layers above it go to the AI
    as references. The combo follows the map view until the user picks by
    hand, and never widens the dock on a long layer name.

    Built into the flow slot (before the zone step); ``_place_layer_header``
    moves it into the launch section and back.
    """
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
    # --- Select-zone section: centered empty-state hero inviting the user
    # to draw the zone. The dock is otherwise blank in this state, so the
    # design-system Empty State pattern (gesture glyph + short warm copy,
    # centered) gives it a clear focal point instead of a lonely top box. ---
    dock._select_zone_section = QWidget()
    dock._select_zone_section.setSizePolicy(
        QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
    )
    sz_layout = QVBoxLayout(dock._select_zone_section)
    # Top-anchored at the home screens' 20 px, never centred: the step used
    # to float mid-panel while every screen around it reads from the top
    # (Yvann 2026-07-08, "the plugin reads top-to-bottom").
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

    # Full-width centered text: wrapping on the real width keeps the layout's
    # heightForWidth correct, so the copy is never clipped (a maxWidth + an
    # alignment flag would mis-size the height and cut the last lines off).
    # One short line, mirroring AI Segmentation's zone step ("Click on the
    # map to outline the area to scan."). The click-to-close mechanics are
    # discoverable on the canvas itself; three sentences of instructions
    # here read as friction, not help.
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

    # A refused zone (too small, outside the picked raster) is answered HERE,
    # under the instruction the user is reading, not only in the QGIS message
    # bar at the top of the map where nobody looking at the dock sees it.
    dock._select_zone_notice = QLabel("")
    dock._select_zone_notice.setWordWrap(True)
    dock._select_zone_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dock._select_zone_notice.setStyleSheet(
        f"QLabel {{ font-size: {tokens.FONT_BODY}px; color: {tokens.RED_TEXT};"
        " background: transparent; border: none; }"
    )
    dock._select_zone_notice.setVisible(False)
    sz_layout.addWidget(dock._select_zone_notice)

    # Compact "Cancel" so the user can always bail out of the draw step
    # without committing a zone. Ghost style + centered row mirrors AI
    # Segmentation's Automatic zone-step Exit. It routes through the SAME
    # _on_exit_clicked path as the prompt / result Exit buttons, so it
    # returns to LAUNCH, disarms the selection tool, and discards any
    # in-progress rubber band. Escape does the same via _on_escape_pressed
    # (SELECTING_ZONE falls through to exit_clicked). Living inside
    # _select_zone_section ties its visibility to this state automatically.
    # Same wide ghost as the prompt screen's Cancel: one word, one shape.
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
    # Stretch factor so the section claims vertical room (competing with the
    # trailing footer spacer) and its inner stretches can centre the hero.
    main_layout.addWidget(dock._select_zone_section, 1)


def _build_prompt_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # --- Prompt section (shown after zone selected) ---
    dock._prompt_section = QWidget()
    dock._prompt_section.setContentsMargins(0, 0, 0, 0)
    dock._prompt_layout = QVBoxLayout(dock._prompt_section)
    # 4 px above the question: the title sat flush under the header rule,
    # where every other screen leaves air above its first line.
    dock._prompt_layout.setContentsMargins(0, 4, 0, 0)
    dock._prompt_layout.setSpacing(6)

    dock._prompt_header = _step_question(
        get_export_copy("dock.build.prompt_header", tr("What should the AI change?"))
    )
    dock._prompt_header.setVisible(True)
    dock._prompt_layout.addWidget(dock._prompt_header)

    dock._prompt_input = _SubmitTextEdit()
    # "describe a change" over "type your prompt": the box takes an edit, not a
    # question, and users who typed a question got a failed generation.
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

    # Soft, non-blocking guidance hint shown live under the prompt when the
    # text looks off-rails (asks for a vector file, or talks to the tool
    # like a Q&A/counting bot). Steers the user without blocking Generate.
    # Detection is high-precision (see detect_prompt_guidance); a valid
    # edit/detect/segment instruction never shows this.
    dock._prompt_guidance_hint = QLabel()
    dock._prompt_guidance_hint.setWordWrap(True)
    # A tip, so the sky tip hue (ui.md: tips leaf or sky, blue is for focus
    # and links). It used the interaction blue's wash, the focus colour.
    dock._prompt_guidance_hint.setStyleSheet(
        _NOTE_CARD_QSS.format(
            tint=tokens.category_tint("sky"), line=tokens.category_line("sky"))
    )
    set_link_ink(dock._prompt_guidance_hint)
    # The measure / vector_file hints embed an <a href='ai_seg'> link to the
    # AI Segmentation plugin; QLabel auto-detects the rich text.
    dock._prompt_guidance_hint.setTextInteractionFlags(
        Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    dock._prompt_guidance_hint.setOpenExternalLinks(False)
    dock._prompt_guidance_hint.linkActivated.connect(dock._on_guidance_link_activated)
    dock._prompt_guidance_hint.setVisible(False)
    dock._prompt_layout.addWidget(dock._prompt_guidance_hint)

    # "Guide the AI" tip: its own card below the prompt block, between the
    # prompt and Generate.
    _build_guide_ai_hint(dock)
    # "Name your marks in the prompt" tip: same slot, wins over guide-AI.
    dock._build_markup_prompt_tip()

    # Soft, non-blocking warning about the drawn zone itself: it covers a very
    # large ground area, or the view is so zoomed out the model can't resolve
    # small features (set by the plugin on zone selection). Amber to read as
    # "heads up", not an error. Last in the section (Yvann 2026-08-03): it used
    # to open the dock, so an amber block was the first thing a new user met,
    # above the question it is answering. It now sits where it is read, just
    # before Generate.
    dock._zone_guidance_hint = _GlyphNote(
        "warning", tokens.category_tint("amber"), tokens.category_ink("amber"),
        line=tokens.category_line("amber"))
    dock._zone_guidance_hint.setVisible(False)
    dock._prompt_layout.addWidget(dock._zone_guidance_hint)

    # Hidden by default: revealed by set_zone_selected() once the user
    # draws a polygon zone.
    dock._prompt_section.setVisible(False)
    main_layout.addWidget(dock._prompt_section)


def _build_guide_ai_hint(dock: AIEditDockWidget) -> None:
    # "Guide the AI" tip: its own blue card below the prompt block, sitting
    # between the prompt and Generate, so the prompt keeps its footer chips as
    # one unit instead of being split by the tip. Blue tint (an optional
    # booster, not a warning). Retires once the user attaches a reference or
    # touches markup this edit group (see _guide_ai_tip_visible /
    # _mark_guide_ai_touched in generation_state.py), or is closed for the
    # session with its own glyph.
    # The body NAMES the three features with the words on their chips
    # (References, Draw, Library), in bold so the sentence reads as "these are
    # controls you can click" (Yvann 2026-07-31: most users never find the
    # Library). The chip labels are read from the chips' own copy keys, so a
    # served rename reaches the tip too. Served under a new key: the old body
    # still named "Reference" and "Mark up" (2026-09-18).
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
    # Reference images widget - created once, moved between the prompt
    # container and the result container as state changes.
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

        # Forward container actions: drop on container + paste in textbox +
        # Reference-menu picks (file or project layer) all funnel into the
        # reference widget, behind the click-time server kill switch
        # (fail-open when the key is absent). The result container wires the
        # same five through the same gate.
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
        # Home it in the prompt box from the start. Parented to the dock with
        # no layout, the strip showed itself at (0, 0) over the header as soon
        # as an image was added before the first placement (from the Reference
        # panel, say). Inside a host layout it only shows with its host.
        dock._prompt_container.insert_refs_widget(dock._reference_widget)
        dock._reference_widget.setVisible(dock._reference_widget.count() > 0)
    else:
        dock._reference_widget = None


def _build_generate_row(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # Generate + Cancel row. Cancel is shown in the PROMPT state (zone
    # selected) so the user always has a one-click way back to LAUNCH,
    # and hidden while a generation is in flight: credits are booked by then.
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
    # Width: hold a longer label ("Annuler", "Abbrechen") without
    # clipping. We use minimumWidth instead of fixedWidth so future
    # translations longer than the current set still fit.
    dock._exit_btn.setMinimumWidth(88)
    dock._exit_btn.setStyleSheet(_BTN_GHOST_WIDE_QSS)
    dock._exit_btn.clicked.connect(dock._on_exit_clicked)
    dock._exit_btn.setVisible(False)
    generate_row.addWidget(dock._exit_btn, 0)

    main_layout.addLayout(generate_row)

    # What is still missing before Generate can run, written live under the
    # button. The click-time warning in the status box stays as the backstop.
    dock._generate_reason_label = QLabel("")
    dock._generate_reason_label.setWordWrap(True)
    dock._generate_reason_label.setAlignment(QtC.AlignCenter)
    dock._generate_reason_label.setStyleSheet(_BLOCK_REASON_QSS)
    dock._generate_reason_label.setVisible(False)
    main_layout.addWidget(dock._generate_reason_label)


def _build_generate_note_strip(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # Where the imagery goes, said on the panel instead of only in the website
    # policy: Generate sends the zone and the prompt to a provider that may sit
    # outside the EU. Its own container because loose text on the dock is
    # banned, but no border and no tint, so it reads as a footnote to the
    # button above rather than a second card competing with it.
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
    # Served, so the wording can be retuned without a plugin release. Tokens are
    # substituted with replace() rather than format(), so a served line carrying
    # a stray brace cannot raise while the dock is being built.
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
    # Shown with the Generate button until the first generation completes, then
    # retired for good (has_seen_privacy_notice). The state machine owns that.
    note_box.setVisible(False)
    main_layout.addWidget(note_box)


def _build_progress_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # Under the prompt, no card (Yvann, 2026-09-18: the titled card with its
    # four steps was too big): AI Segmentation's run line, the pulsing dots,
    # the phase, the clock and the percent, over an 8 px bar that turns from
    # sky to green as it fills (progress_loader.py).
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
    # Status message box (same pattern as AI Segmentation info boxes)
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
    # Manual link routing (not setOpenExternalLinks): http links still open
    # in the browser, but the "Report a problem" sentinel opens the in-app
    # log-report dialog instead of being handed to the OS as a bad URL.
    dock._status_label.linkActivated.connect(dock._on_status_link)
    dock._status_label.setStyleSheet(
        f"font-size: {tokens.FONT_BODY}px; color: {tokens.INK}; background: transparent; border: none;"
    )
    status_box_layout.addWidget(dock._status_label, 1)

    # One action per message. A failure that only names what went wrong leaves
    # the user to work out the next move on their own, which is where AI Edit
    # stopped and AI Segmentation does not. Filled by set_status_action, and
    # cleared by every set_status, so it can never outlive its message.
    dock._status_action_btn = QPushButton("")
    dock._status_action_btn.setCursor(QtC.PointingHandCursor)
    dock._status_action_btn.setStyleSheet(_BTN_GHOST)
    dock._status_action_btn.setAutoDefault(False)
    dock._status_action_btn.setVisible(False)
    dock._status_action_handler = None
    dock._status_action_btn.clicked.connect(dock._on_status_action_clicked)
    status_box_layout.addWidget(dock._status_action_btn, 0, QtC.AlignVCenter)

    # The subscriber's end-of-month card (quota_card.QuotaCard): the fact,
    # the served line, "Copy email" and a quiet "Manage plan". Filled by
    # DockProCeilingMixin.show_pro_limit_info and show_usage_limit_info.
    dock._pro_limit_card = QuotaCard(object_name="aiEditPaidLimitCard")
    dock._pro_limit_card.ghost_clicked.connect(
        lambda: dock._on_pro_contact_clicked(dock._pro_limit_card.ghost_button)
    )
    dock._pro_limit_card.manage_clicked.connect(dock._on_limit_cta_clicked)
    dock._pro_limit_card.manage_button.setToolTip(
        get_export_copy("dock.build.manage_plan_tooltip", tr("Open your dashboard to upgrade or wait for renewal."))
    )
    dock._limit_cta_url = ""
