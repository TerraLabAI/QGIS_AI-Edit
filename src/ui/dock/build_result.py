"""Second half of the AIEditDockWidget widget-tree build (see build.py).

Result section, trial info box, side panels, first-steps hint, footer, and
the scroll-area wrap. Called only from build.build_ui, in a fixed order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.i18n import tr
from ...core.qt_compat import QShortcut
from ..credit_ring import CreditRing
from ..onboarding_hint import (
    BLUE_TINT,
    HINT_FIRST_STEPS,
    NEUTRAL_TINT,
    DismissibleHint,
    open_guide,
)
from ..panels.markup_panel import MarkupPanel
from ..panels.reference_panel import ReferencePanel
from ..panels.vectorize_panel import VectorizePanel
from ..version_strip import VersionStrip
from .prompt_container import _PromptContainer
from .style import (
    _BTN_BLUE,
    _BTN_BLUE_OUTLINE,
    _BTN_GREEN,
    _BTN_GREEN_OUTLINE,
    _FOOTER_ICON_BTN_STYLE,
    _FOOTER_MENU_STYLE,
)
from .widgets import _FooterIconButton, _SubmitTextEdit

if TYPE_CHECKING:
    from .widget import AIEditDockWidget


def _build_result_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # --- Result section (shown after generation complete, iteration flow) ---
    # A single prompt screen. The version strip below the prompt is the base
    # picker: Original is pinned left, each result appends to the right, and
    # the selected tile is what the next edit builds on. Lives inside
    # _result_section so every state transition that hides it hides together.
    dock._result_section = QWidget()
    dock._result_layout = QVBoxLayout(dock._result_section)
    dock._result_layout.setContentsMargins(0, 0, 0, 0)
    dock._result_layout.setSpacing(6)

    # --- Prompt + version strip + Generate -----------------------------
    dock._result_prompt_widget = QWidget()
    dock._result_prompt_layout = QVBoxLayout(dock._result_prompt_widget)
    dock._result_prompt_layout.setContentsMargins(0, 0, 0, 0)
    dock._result_prompt_layout.setSpacing(6)

    # Editable prompt (edit and retry)
    dock._result_prompt_input = _SubmitTextEdit()
    dock._result_prompt_input.setPlaceholderText(
        tr("Type a new prompt to retry, or pick an action below")
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
        # Behind the same click-time kill switch as the idle container: the
        # five ways in must not diverge just because the user has a result
        # on screen.
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

    # Same soft off-rails hint as the first-run prompt, so iterating on a
    # v1/v2 gets the same guidance (vector / measure / chatbot).
    dock._result_guidance_hint = QLabel()
    dock._result_guidance_hint.setWordWrap(True)
    dock._result_guidance_hint.setStyleSheet(
        "QLabel { background-color: rgba(30, 136, 229, 0.08); "
        "border: 1px solid rgba(30, 136, 229, 0.2); border-radius: 4px; "
        "padding: 6px 8px; font-size: 11px; color: palette(text); }"
    )
    # Same AI Segmentation link handling as the first-run hint.
    dock._result_guidance_hint.setTextInteractionFlags(
        Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    dock._result_guidance_hint.setOpenExternalLinks(False)
    dock._result_guidance_hint.linkActivated.connect(dock._on_guidance_link_activated)
    dock._result_guidance_hint.setVisible(False)
    dock._result_prompt_layout.addWidget(dock._result_guidance_hint)

    # Version strip: the base picker. Original pinned left, results append
    # right, selected tile drives the next edit. Hidden until seeded.
    dock._version_strip = VersionStrip()
    dock._version_strip.version_selected.connect(dock._on_version_selected)
    dock._result_prompt_layout.addWidget(dock._version_strip)

    # Action row: Generate (primary, filled) + Finish (green outline, fixed).
    # This is the one step where leaving means the user got what they came for,
    # so the way out is named for that: "Finish", not "Exit". It keeps the same
    # handler as every other exit - the layer stays in the project and the
    # session stays resumable from Resume, which the exit message says out loud
    # - and it stays an outline, because iterating again is still the primary
    # action. A separate Keep BUTTON read as a mystery third choice (Yvann
    # 2026-07-31), and the per-version keep mark that replaced it was removed
    # too (owner call 2026-08-03): finishing needs no extra choice at all.
    result_actions_row = QHBoxLayout()
    result_actions_row.setContentsMargins(0, 4, 0, 0)
    result_actions_row.setSpacing(6)

    dock._result_regenerate_btn = QPushButton(tr("Generate"))
    dock._result_regenerate_btn.setToolTip(
        tr("Generate on the same zone using the current map view")
    )
    dock._result_regenerate_btn.setCursor(QtC.PointingHandCursor)
    dock._result_regenerate_btn.setStyleSheet(_BTN_GREEN)
    dock._result_regenerate_btn.clicked.connect(dock._on_retry_clicked)
    result_actions_row.addWidget(dock._result_regenerate_btn, 1)

    dock._result_exit_btn = QPushButton(tr("Finish"))
    dock._result_exit_btn.setToolTip(
        tr("Finish this session and return to the start")
    )
    dock._result_exit_btn.setCursor(QtC.PointingHandCursor)
    # See `_exit_btn` above for why this is a minimum rather than fixed
    # width.
    dock._result_exit_btn.setMinimumWidth(88)
    dock._result_exit_btn.setMinimumHeight(36)
    dock._result_exit_btn.setStyleSheet(_BTN_GREEN_OUTLINE)
    dock._result_exit_btn.clicked.connect(dock._on_exit_clicked)
    result_actions_row.addWidget(dock._result_exit_btn, 0)

    dock._result_prompt_layout.addLayout(result_actions_row)

    # Minimal status line - shown under the action row after generation.
    # Submitting the prompt (Enter key) and the Generate button both
    # trigger a regen on the same zone.
    dock._layer_saved_label = QLabel()
    dock._layer_saved_label.setWordWrap(True)
    dock._layer_saved_label.setTextFormat(QtC.RichText)
    dock._layer_saved_label.setStyleSheet(
        "font-size: 11px; color: palette(text); background: transparent;"
        " border: none; padding: 4px 0 0 0;"
    )
    # ArrowCursor on the wrapper; Qt switches to PointingHand over the <a>.
    dock._layer_saved_label.setCursor(QtC.ArrowCursor)
    dock._layer_saved_label.linkActivated.connect(dock._on_layer_saved_link_clicked)
    dock._layer_saved_label.setVisible(False)
    dock._saved_layer_id: str | None = None
    dock._result_prompt_layout.addWidget(dock._layer_saved_label)

    # --- Vectorize suggestion card (hidden by default) ---
    # Shown after a generation when a template carried vector hints, when a
    # free-form prompt asked to segment one target, or when the downloaded
    # result itself is a set of flat color zones (worker-side detection,
    # vectorize_detect). One click opens the Vectorize panel with the source
    # layer locked and the color pre-filled. Guidance-blue tint per the
    # design-system taxonomy; the button is the blue-outline secondary so
    # the screen's one filled primary stays the green Generate.
    dock._vectorize_cta_section = QFrame()
    dock._vectorize_cta_section.setObjectName("vectorizeCtaCard")
    dock._vectorize_cta_section.setStyleSheet(
        "QFrame#vectorizeCtaCard { background-color: rgba(30,136,229,0.08);"
        " border: 1px solid rgba(30,136,229,0.22); border-radius: 6px; }"
    )
    cta_layout = QVBoxLayout(dock._vectorize_cta_section)
    cta_layout.setContentsMargins(10, 8, 10, 10)
    cta_layout.setSpacing(6)
    cta_header = QHBoxLayout()
    cta_header.setSpacing(6)
    dock._vectorize_cta_swatch_row = QHBoxLayout()
    dock._vectorize_cta_swatch_row.setSpacing(4)
    cta_header.addLayout(dock._vectorize_cta_swatch_row)
    dock._vectorize_cta_caption = QLabel()
    dock._vectorize_cta_caption.setWordWrap(True)
    dock._vectorize_cta_caption.setStyleSheet(
        "font-size: 11px; color: palette(text);"
        " background: transparent; border: none;"
    )
    cta_header.addWidget(dock._vectorize_cta_caption, 1)
    cta_layout.addLayout(cta_header)
    dock._vectorize_cta_btn = QPushButton(tr("Vectorize this result") + "  →")
    dock._vectorize_cta_btn.setCursor(QtC.PointingHandCursor)
    dock._vectorize_cta_btn.setMinimumHeight(32)
    dock._vectorize_cta_btn.setStyleSheet(_BTN_BLUE_OUTLINE)
    dock._vectorize_cta_btn.clicked.connect(dock._on_vectorize_cta_clicked)
    cta_layout.addWidget(dock._vectorize_cta_btn)
    # One-line AI Segmentation cross-promo under the button: a segmentation-
    # style result is the moment the user judges outline quality, so the
    # dedicated plugin is named right here (#47). Closable for good with the
    # ✕ (HINT_SEG_CROSS); the row rides the card's visibility otherwise.
    dock._vectorize_seg_row = QWidget()
    seg_row = QHBoxLayout(dock._vectorize_seg_row)
    seg_row.setContentsMargins(0, 2, 0, 0)
    seg_row.setSpacing(4)
    dock._vectorize_seg_label = QLabel(
        "ⓘ  " + tr("Need cleaner outlines? Try our "
                   "<a href='ai_seg'>AI Segmentation</a> plugin.")
    )
    dock._vectorize_seg_label.setWordWrap(True)
    dock._vectorize_seg_label.setStyleSheet(
        "font-size: 11px; color: palette(text);"
        " background: transparent; border: none;"
    )
    dock._vectorize_seg_label.setTextInteractionFlags(
        Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    dock._vectorize_seg_label.setOpenExternalLinks(False)
    dock._vectorize_seg_label.linkActivated.connect(
        dock._on_vectorize_seg_link_activated
    )
    seg_row.addWidget(dock._vectorize_seg_label, 1)
    seg_close = QToolButton()
    seg_close.setText("✕")
    seg_close.setAccessibleName(tr("Dismiss"))
    seg_close.setToolTip(tr("Dismiss"))
    seg_close.setCursor(QtC.PointingHandCursor)
    seg_close.setStyleSheet(
        "QToolButton { border: none; background: transparent;"
        " color: rgba(128,128,128,0.8); font-size: 11px; padding: 0; }"
        "QToolButton:hover { color: palette(text); }"
    )
    seg_close.clicked.connect(dock._on_vectorize_seg_dismissed)
    seg_row.addWidget(seg_close, 0, Qt.AlignmentFlag.AlignTop)
    cta_layout.addWidget(dock._vectorize_seg_row)
    dock._vectorize_cta_section.setVisible(False)
    dock._vectorize_cta_pending: tuple[str, str, str, str] | None = None
    dock._result_prompt_layout.addWidget(dock._vectorize_cta_section)

    dock._result_layout.addWidget(dock._result_prompt_widget)
    dock._result_prompt_widget.setVisible(False)

    dock._result_section.setVisible(False)
    main_layout.addWidget(dock._result_section)


def _build_prewall_banner(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # Pre-wall nudge - one free generation left this month, shown a step
    # before the wall box. Same blue-tint card language (no new hue),
    # lighter content: one line + one small outline CTA, no benefits list.
    # Pinned bottom-right of the dock, above the footer icon row (sibling of
    # the update notification), so it never competes with the result area.
    # Visibility is entirely state-driven (DockAccountMixin.set_credits /
    # show_prewall_info), never a dismiss-and-forget hint: it must come back
    # every period the balance lands on exactly one generation.
    dock._prewall_banner = QFrame()
    dock._prewall_banner.setStyleSheet(
        "QFrame { background: rgba(30,136,229,0.08); "
        "border: 1px solid rgba(30,136,229,0.2); "
        "border-radius: 4px; }"
        "QLabel { background: transparent; border: none; }"
    )
    prewall_layout = QVBoxLayout(dock._prewall_banner)
    prewall_layout.setContentsMargins(12, 10, 12, 10)
    prewall_layout.setSpacing(6)
    dock._prewall_text = QLabel("")
    dock._prewall_text.setWordWrap(True)
    dock._prewall_text.setStyleSheet("font-size: 12px; color: palette(text);")
    prewall_layout.addWidget(dock._prewall_text)
    dock._prewall_btn = QPushButton(tr("Take a Pro month"))
    dock._prewall_btn.setCursor(QtC.PointingHandCursor)
    dock._prewall_btn.setMinimumHeight(28)
    dock._prewall_btn.setStyleSheet(_BTN_BLUE_OUTLINE)
    dock._prewall_btn.clicked.connect(dock._on_prewall_cta_clicked)
    prewall_layout.addWidget(dock._prewall_btn, 0, Qt.AlignmentFlag.AlignLeft)
    dock._prewall_url = ""
    dock._prewall_banner.setVisible(False)
    # Capped width + right alignment: reads as a corner nudge, not a wall.
    dock._prewall_banner.setMaximumWidth(380)
    main_layout.addWidget(dock._prewall_banner, 0, Qt.AlignmentFlag.AlignRight)


def _build_trial_info_box(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # Wall box - locked-out screen shown when a free-tier user runs out of
    # credits. Locked wording (show_trial_exhausted_info builds the title:
    # total free generations + when they return), one CTA, no benefits list
    # (Task 7 of the 60-credit paywall: a single clear action beats a bullet
    # list once the account is actually blocked).
    dock._trial_info_box = QFrame()
    dock._trial_info_box.setStyleSheet(
        "QFrame { background: rgba(30,136,229,0.08); "
        "border: 1px solid rgba(30,136,229,0.2); "
        "border-radius: 4px; }"
        "QLabel { background: transparent; border: none; }"
    )
    trial_layout = QVBoxLayout(dock._trial_info_box)
    trial_layout.setContentsMargins(12, 12, 12, 12)
    trial_layout.setSpacing(8)
    dock._trial_info_text = QLabel("")
    dock._trial_info_text.setWordWrap(True)
    dock._trial_info_text.setStyleSheet(
        "font-size: 12px; font-weight: bold; color: palette(text);"
    )
    trial_layout.addWidget(dock._trial_info_text)
    dock._trial_info_btn = QPushButton(tr("Unlock your Pro month: €29"))
    dock._trial_info_btn.setCursor(QtC.PointingHandCursor)
    dock._trial_info_btn.setMinimumHeight(32)
    dock._trial_info_btn.setStyleSheet(_BTN_BLUE)
    dock._trial_info_btn.clicked.connect(dock._on_trial_info_subscribe_clicked)
    trial_layout.addWidget(dock._trial_info_btn)
    dock._trial_info_subtext = QLabel(tr("No commitment, cancel anytime"))
    dock._trial_info_subtext.setWordWrap(True)
    dock._trial_info_subtext.setStyleSheet(
        "font-size: 11px; color: palette(text);"
    )
    trial_layout.addWidget(dock._trial_info_subtext)
    dock._trial_info_url = ""
    # Kept for backwards compatibility with show_trial_exhausted_info callers
    # that still set a link; rendered inline as a fallback if the button is
    # ever hidden by external state.
    dock._trial_info_link = QLabel("")
    dock._trial_info_link.setOpenExternalLinks(True)
    dock._trial_info_link.setStyleSheet(
        "font-size: 11px; background: transparent; border: none;"
    )
    dock._trial_info_link.setVisible(False)
    trial_layout.addWidget(dock._trial_info_link)
    dock._trial_info_box.setVisible(False)
    main_layout.addWidget(dock._trial_info_box)


def _build_side_panels(dock: AIEditDockWidget, layout: QVBoxLayout) -> None:
    # Mark up panel - full-dock workflow opened via Tools menu, hidden
    # by default; swaps with _main_widget while the user is annotating.
    dock._markup_panel = MarkupPanel(dock)
    dock._markup_panel.setVisible(False)
    dock._markup_panel.tool_changed.connect(dock.markup_tool_changed.emit)
    dock._markup_panel.color_changed.connect(dock.markup_color_changed.emit)
    dock._markup_panel.clear_clicked.connect(dock.markup_clear_clicked.emit)
    dock._markup_panel.done_clicked.connect(dock.markup_done_clicked.emit)
    layout.addWidget(dock._markup_panel)

    # Reference panel - same swap pattern; the dedicated import/notes home.
    # None without the strip (no reference store): the chip is hidden then too.
    dock._reference_panel = None
    if dock._reference_widget is not None:
        dock._reference_panel = ReferencePanel(
            dock._reference_store, dock._reference_widget, dock
        )
        dock._reference_panel.setVisible(False)
        dock._reference_panel.done_clicked.connect(dock.reference_done_clicked.emit)
        layout.addWidget(dock._reference_panel)

    # Vectorize panel - same swap pattern as Mark up.
    dock._vectorize_panel = VectorizePanel(dock)
    dock._vectorize_panel.setVisible(False)
    dock._vectorize_panel.done_clicked.connect(dock.vectorize_done_clicked.emit)
    layout.addWidget(dock._vectorize_panel)


def _build_first_steps_hint(dock: AIEditDockWidget, layout: QVBoxLayout) -> None:
    # First-steps guide banner, pinned just above the footer so it survives
    # the content view swaps and never crowds the flow. Gated to the idle
    # LAUNCH screen of a signed-in user (see _should_show_first_steps),
    # dismissible, and re-showable from Account Settings. Starts hidden; the
    # gate reveals it on the LAUNCH screen. The link opens the written guide
    # (UTM + best-effort telemetry).
    dock._first_steps_hint = DismissibleHint(
        HINT_FIRST_STEPS,
        "",
        # Says "tutorial", medium-neutral (the tutorial page has a video
        # too, so no "read"); quiet grey card + small blue button so it
        # never shouts (Yvann 2026-07-08).
        tr("New here? Our 5-minute tutorial walks you through a full "
           "edit, step by step."),
        action_text=tr("Open the tutorial"),
        tint=NEUTRAL_TINT,
        action_color=BLUE_TINT,
        visibility_gate=dock._should_show_first_steps,
    )
    dock._first_steps_hint.action.connect(lambda: open_guide("post_signin"))
    dock._first_steps_hint.setVisible(False)
    layout.addWidget(dock._first_steps_hint)


def _build_footer(dock: AIEditDockWidget, layout: QVBoxLayout) -> None:
    # Footer section - single row: ring + count + upgrade pill on the
    # left, gear/help menus on the right. As the dock narrows,
    # _apply_footer_responsive collapses the count then shortens the pill
    # so the right-side icons are never clipped.
    footer_widget = QWidget()
    footer_row = QHBoxLayout(footer_widget)
    footer_row.setContentsMargins(0, 4, 0, 4)
    footer_row.setSpacing(6)
    # Kept so resizeEvent can measure the row's natural width and collapse
    # low-priority items (count, then pill text) until it fits.
    dock._footer_row = footer_row

    dock._credit_ring = CreditRing(diameter=16, parent=footer_widget)
    dock._credit_ring.setVisible(False)
    footer_row.addWidget(dock._credit_ring)

    dock._credits_label = QLabel()
    dock._credits_label.setStyleSheet(
        "QLabel { font-size: 11px; color: palette(text);"
        " background: transparent; border: none; }"
    )
    dock._credits_label.setVisible(False)
    footer_row.addWidget(dock._credits_label)

    # The old "Unlock more detail" upgrade pill lived here; removed 2026-08-07,
    # the pre-wall banner and the wall screen carry the upsell now.
    # Tracks whether the credit ring + count have data; resizeEvent uses
    # this to keep them hidden on narrow docks.
    dock._credits_wanted = False

    footer_row.addStretch()

    # Mark up is reachable via the pencil chip next to the prompt; the
    # footer button has been removed to avoid duplication. Alt+M still
    # opens markup via the global shortcut wired below.
    dock._markup_shortcut = QShortcut(QKeySequence("Alt+M"), dock)
    dock._markup_shortcut.setContext(QtC.WindowShortcut)
    dock._markup_shortcut.activated.connect(dock.markup_clicked.emit)

    dock._vectorize_btn = _FooterIconButton(footer_widget)
    dock._vectorize_btn.setToolTip(tr("Vectorize"))
    dock._vectorize_btn.setAccessibleName(tr("Vectorize"))
    dock._vectorize_btn.setCursor(QtC.PointingHandCursor)
    dock._vectorize_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
    dock._vectorize_btn.setIcon(dock._make_polygon_glyph_icon())
    dock._vectorize_btn.setIconSize(QSize(20, 20))
    dock._vectorize_btn.clicked.connect(dock.vectorize_clicked.emit)
    vectorize_seq = QKeySequence("Alt+V")
    dock._vectorize_btn.setShortcut(vectorize_seq)
    dock._vectorize_btn.setToolTip(
        tr("Vectorize ({})").format(
            vectorize_seq.toString(QKeySequence.SequenceFormat.NativeText)
        )
    )
    dock._vectorize_btn.setVisible(False)
    footer_row.addWidget(dock._vectorize_btn)

    # Swipe button - opens the Before/after compare panel. Hidden until
    # an AI Edit output exists so the footer stays empty on first launch
    # (visibility synced by set_vectorize_button_visible together with
    # the vectorize footer button - both depend on having a generation).
    # Before/After is a checkable toggle: click once to arm the swipe
    # map tool, click again (or Esc on the canvas) to disarm. When
    # armed the button paints a green tint so the user can see at a
    # glance which tool the canvas is in.
    dock._swipe_btn = _FooterIconButton(footer_widget)
    dock._swipe_btn.setAccessibleName(tr("Before / after"))
    dock._swipe_btn.setCursor(QtC.PointingHandCursor)
    dock._swipe_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
    dock._swipe_btn.setIcon(dock._make_swipe_glyph_icon())
    dock._swipe_btn.setIconSize(QSize(20, 20))
    dock._swipe_btn.setCheckable(True)
    dock._swipe_btn.setEnabled(False)  # gated on active layer eligibility
    dock._swipe_btn.toggled.connect(dock.swipe_toggled.emit)
    swipe_seq = QKeySequence("Alt+B")
    dock._swipe_btn.setShortcut(swipe_seq)
    dock._swipe_btn.setToolTip(
        tr("Before / after ({})").format(
            swipe_seq.toString(QKeySequence.SequenceFormat.NativeText)
        )
    )
    dock._swipe_btn.setVisible(False)
    footer_row.addWidget(dock._swipe_btn)

    # Tutorial button - painted open-book glyph, always visible (signed in
    # and signed out) so a lost user can reach the step-by-step guide from
    # anywhere. Order: tutorial, gear, help - it groups with the help "?" as
    # a learn action. Opens the written guide with UTM + best-effort event.
    dock._tutorial_btn = _FooterIconButton(footer_widget)
    # A painted open book, not U+1F4D6: Windows renders that character as a
    # colour emoji at the full em box, which broke the footer's shared baseline
    # and grew the row. AI Segmentation still ships the character.
    dock._tutorial_btn.setIcon(dock._make_book_glyph_icon())
    dock._tutorial_btn.setIconSize(QSize(20, 20))
    dock._tutorial_btn.setToolTip(tr("Open the step-by-step tutorial"))
    dock._tutorial_btn.setAccessibleName(tr("Tutorial"))
    dock._tutorial_btn.setCursor(QtC.PointingHandCursor)
    dock._tutorial_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
    dock._tutorial_btn.clicked.connect(dock._on_open_guide_footer)
    footer_row.addWidget(dock._tutorial_btn)

    # Settings button - gear icon, opens the Account Settings dialog
    # directly. Shortcuts have moved inside that dialog.
    dock._settings_btn = _FooterIconButton(footer_widget)
    dock._settings_btn.setIcon(dock._make_gear_glyph_icon())
    dock._settings_btn.setIconSize(QSize(20, 20))
    dock._settings_btn.setToolTip(tr("Settings"))
    dock._settings_btn.setAccessibleName(tr("Settings"))
    dock._settings_btn.setCursor(QtC.PointingHandCursor)
    dock._settings_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
    dock._settings_btn.clicked.connect(dock._on_settings_btn_clicked)
    dock._settings_btn.setVisible(False)  # shown when activated
    footer_row.addWidget(dock._settings_btn)

    # Help menu - question mark icon, always visible.
    dock._help_btn = _FooterIconButton(footer_widget)
    dock._help_btn.setText("?")
    dock._help_btn.setToolTip(tr("Help"))
    dock._help_btn.setAccessibleName(tr("Help"))
    dock._help_btn.setCursor(QtC.PointingHandCursor)
    dock._help_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
    dock._help_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    help_menu = QMenu(dock._help_btn)
    help_menu.setStyleSheet(_FOOTER_MENU_STYLE)
    help_menu.addAction(tr("Tutorial"), dock._on_open_tutorial)
    help_menu.addAction(tr("Shortcuts"), dock._on_show_shortcuts)
    help_menu.addAction(tr("Contact us"), dock._on_contact_us)
    help_menu.addAction(tr("Report a problem"), dock._on_report_problem)
    dock._help_btn.setMenu(help_menu)
    # Force the hover tint off when the popup closes - Qt does not
    # synthesise a Leave event in this case. Also light the green
    # active tint while the menu is open and broadcast the change so
    # the plugin can disarm the swipe map tool.
    help_menu.aboutToShow.connect(
        lambda: (dock._help_btn.set_active(True),
                 dock.help_menu_open_changed.emit(True))
    )
    help_menu.aboutToHide.connect(
        lambda btn=dock._help_btn: (
            btn.setDown(False), btn.set_hovered(False), btn.set_active(False),
            dock.help_menu_open_changed.emit(False),
        )
    )
    footer_row.addWidget(dock._help_btn)

    layout.addWidget(footer_widget)


def _wrap_in_scroll_area(dock: AIEditDockWidget, main_widget: QWidget) -> None:
    # Wrap in scroll area (matches AI Segmentation)
    scroll_area = QScrollArea()
    scroll_area.setWidget(main_widget)
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QtC.FrameNoFrame)
    scroll_area.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
    dock.setWidget(scroll_area)
    # Kept so the footer responsive logic reads the true available width
    # (the viewport excludes the vertical scrollbar), not the dock width.
    dock._scroll_area = scroll_area
