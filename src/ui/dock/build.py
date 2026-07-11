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
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.i18n import tr
from ..panel_helpers import make_section_header as _make_section_header
from ..reference_images_widget import ReferenceImagesWidget
from .build_result import (
    _build_first_steps_hint,
    _build_footer,
    _build_result_section,
    _build_side_panels,
    _build_trial_info_box,
    _wrap_in_scroll_area,
)
from .prompt_container import _PromptContainer
from .style import _BTN_BLUE, _BTN_GHOST, _BTN_GREEN_AUTH, BRAND_BLUE
from .widgets import _SubmitTextEdit, _ZoneGestureGlyph

if TYPE_CHECKING:
    from .widget import AIEditDockWidget


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

    main_layout = _build_main_section(dock)
    _build_launch_section(dock, main_layout)
    _build_select_zone_section(dock, main_layout)
    _build_prompt_section(dock, main_layout)
    _build_reference_widget(dock)
    _build_consent_section(dock, main_layout)
    _build_generate_row(dock, main_layout)
    _build_progress_section(dock, main_layout)
    _build_status_section(dock)
    _build_result_section(dock, main_layout)

    # Status box + CTA placed after result section so they always appear below
    main_layout.addWidget(dock._status_widget)
    main_layout.addWidget(dock._limit_cta_btn)

    _build_trial_info_box(dock, main_layout)

    main_layout.addStretch()

    layout.addWidget(dock._main_widget)

    _build_side_panels(dock, layout)

    # The Before/After swipe has no dock panel: it's a toggle on the
    # footer Before/After button that arms a map tool on the canvas.
    # See SwipeController in swipe_panel.py and the wiring in plugin.py.

    # Spacer to push footer to bottom
    layout.addStretch()

    # --- Update notification, pinned at the bottom (above the footer) so it
    # stays visible in every state (idle, generating, result). Most users
    # never check for plugin updates, so this is how they learn one exists.
    dock._setup_update_notification(layout)

    _build_first_steps_hint(dock, layout)
    _build_footer(dock, layout)
    _wrap_in_scroll_area(dock, main_widget)


def _build_main_section(dock: AIEditDockWidget) -> QVBoxLayout:
    # --- Main content section ---
    dock._main_widget = QWidget()
    main_layout = QVBoxLayout(dock._main_widget)
    main_layout.setContentsMargins(0, 0, 0, 0)
    main_layout.setSpacing(8)
    # Kept so the version strip can be re-homed under the progress bar while
    # a generation runs (see _place_version_strip).
    dock._main_layout = main_layout

    # Empty-canvas hero (no visible layer). Added with stretch factor 1 and
    # built as a vertically-expanding wrapper so the card CENTERS in the
    # otherwise-blank panel (same pattern as _select_zone_section below)
    # instead of clinging to the top.
    dock._warning_widget = dock._build_warning_widget()
    dock._warning_widget.setVisible(False)
    main_layout.addWidget(dock._warning_widget, 1)
    return main_layout


def _build_launch_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # --- Launch section (entry screen, matches AI Segmentation pattern) ---
    dock._launch_section = QWidget()
    launch_layout = QVBoxLayout(dock._launch_section)
    launch_layout.setContentsMargins(0, 0, 0, 0)
    launch_layout.setSpacing(8)

    dock._launch_btn = QPushButton(tr("Launch AI Edit"))
    dock._launch_btn.setToolTip(tr("Start a new AI edit session"))
    dock._launch_btn.setCursor(QtC.PointingHandCursor)
    dock._launch_btn.setMinimumHeight(36)
    dock._launch_btn.setStyleSheet(_BTN_GREEN_AUTH)
    dock._launch_btn.clicked.connect(dock.launch_clicked.emit)
    launch_layout.addWidget(dock._launch_btn)

    dock._launch_section.setVisible(False)
    main_layout.addWidget(dock._launch_section)


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
    sz_layout.setContentsMargins(16, 0, 16, 0)
    sz_layout.setSpacing(10)
    sz_layout.addStretch(1)

    dock._select_zone_icon = _ZoneGestureGlyph(QColor(BRAND_BLUE))
    sz_layout.addWidget(
        dock._select_zone_icon, 0, Qt.AlignmentFlag.AlignHCenter
    )

    dock._select_zone_header = _make_section_header(tr("Draw your zone"))
    dock._select_zone_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sz_layout.addWidget(dock._select_zone_header)

    # Full-width centered text: wrapping on the real width keeps the layout's
    # heightForWidth correct, so the copy is never clipped (a maxWidth + an
    # alignment flag would mis-size the height and cut the last lines off).
    dock._select_zone_hint = QLabel(
        tr("Hold the left mouse button and drag to draw a box on the map. Then describe the change you want.")
    )
    dock._select_zone_hint.setWordWrap(True)
    dock._select_zone_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dock._select_zone_hint.setStyleSheet(
        "QLabel { font-size: 12px; color: palette(text);"
        " background: transparent; border: none; }"
    )
    sz_layout.addWidget(dock._select_zone_hint)

    # Compact "Exit" so the user can always bail out of the draw step
    # without committing a zone. Ghost style + centered row mirrors AI
    # Segmentation's Automatic zone-step Exit. It routes through the SAME
    # _on_exit_clicked path as the prompt / result Exit buttons, so it
    # returns to LAUNCH, disarms the selection tool, and discards any
    # in-progress rubber band. Escape does the same via _on_escape_pressed
    # (SELECTING_ZONE falls through to exit_clicked). Living inside
    # _select_zone_section ties its visibility to this state automatically.
    zone_exit_row = QHBoxLayout()
    zone_exit_row.setContentsMargins(0, 6, 0, 0)
    zone_exit_row.addStretch()
    dock._select_zone_exit_btn = QPushButton(tr("Exit"))
    dock._select_zone_exit_btn.setToolTip(tr("Exit and return to the start"))
    dock._select_zone_exit_btn.setCursor(QtC.PointingHandCursor)
    dock._select_zone_exit_btn.setMinimumWidth(88)
    dock._select_zone_exit_btn.setMinimumHeight(32)
    dock._select_zone_exit_btn.setStyleSheet(_BTN_GHOST)
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
    dock._prompt_layout.setContentsMargins(0, 0, 0, 0)
    dock._prompt_layout.setSpacing(6)

    # Soft, non-blocking warning shown at the top when the drawn zone is so
    # zoomed out the model can't resolve small features (set by the plugin
    # on zone selection). Amber to read as "heads up", not an error.
    dock._zone_guidance_hint = QLabel()
    dock._zone_guidance_hint.setWordWrap(True)
    dock._zone_guidance_hint.setStyleSheet(
        "QLabel { background-color: rgb(255, 230, 150); "
        "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; "
        "padding: 6px 8px; font-size: 11px; color: #333333; }"
    )
    dock._zone_guidance_hint.setVisible(False)
    dock._prompt_layout.addWidget(dock._zone_guidance_hint)

    dock._prompt_header = _make_section_header(tr("What should AI change?"))
    dock._prompt_header.setVisible(True)
    dock._prompt_layout.addWidget(dock._prompt_header)

    dock._prompt_input = _SubmitTextEdit()
    dock._prompt_input.setPlaceholderText(
        tr("type your prompt or pick from the library...")
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
    dock._prompt_guidance_hint.setStyleSheet(
        "QLabel { background-color: rgba(25, 118, 210, 0.08); "
        "border: 1px solid rgba(25, 118, 210, 0.2); border-radius: 4px; "
        "padding: 6px 8px; font-size: 11px; color: palette(text); }"
    )
    dock._prompt_guidance_hint.setVisible(False)
    dock._prompt_layout.addWidget(dock._prompt_guidance_hint)

    # Hidden by default: revealed by set_zone_selected() once the user
    # draws a rectangle. Initial dock state only shows the "Select your
    # zone" button.
    dock._prompt_section.setVisible(False)
    main_layout.addWidget(dock._prompt_section)


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
        # paperclip click all funnel into the reference widget.
        dock._prompt_container.files_dropped.connect(dock._reference_widget.add_paths)
        dock._prompt_container.layers_dropped.connect(dock._reference_widget.add_layers)
        dock._prompt_container.attach_clicked.connect(
            dock._reference_widget.open_file_picker
        )
        dock._prompt_input.images_pasted.connect(dock._reference_widget.add_paths)
        # Idle: keep the widget out of the title bar by hiding it until placed.
        dock._reference_widget.setVisible(False)
    else:
        dock._reference_widget = None


def _build_consent_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # Consent checkbox (shown only until first generation). Use native
    # QGIS style so the checkmark glyph renders correctly.
    dock._consent_check = QCheckBox()
    # Pre-ticked to cut first-run friction: the box shows checked, Generate
    # is enabled, and the affirmative act (clicking Generate with the terms
    # right there) is what records consent. Unticking re-adds the gate.
    dock._consent_check.setChecked(True)
    dock._consent_check.setText("")  # text set via label below
    # Bigger, easier-to-hit indicator (the default is tiny and hard to click).
    # Size only, no border/background, so the native checkmark still renders.
    dock._consent_check.setStyleSheet(
        "QCheckBox::indicator { width: 18px; height: 18px; }"
    )
    dock._consent_check.setCursor(QtC.PointingHandCursor)
    consent_layout = QHBoxLayout()
    consent_layout.setContentsMargins(0, 0, 0, 0)
    consent_layout.setSpacing(8)
    consent_layout.addWidget(dock._consent_check, 0, QtC.AlignTop)
    _terms_url = (
        "https://terra-lab.ai/terms-of-sale"
        "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=consent_terms"
    )
    _privacy_url = (
        "https://terra-lab.ai/privacy-policy"
        "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=consent_privacy"
    )
    # Short consent line with clickable Terms + Privacy links. The full
    # disclosure (upload, EU storage, retention) lives behind those links
    # so the panel stays calm. {terms} and {privacy} are placeholders so
    # the linked words can be reordered in translations.
    _consent_template = tr(
        "I agree to the {terms} and {privacy}"
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
    dock._consent_widget = QWidget()
    dock._consent_widget.setLayout(consent_layout)
    dock._consent_widget.setVisible(False)
    dock._consent_check.stateChanged.connect(dock._on_consent_changed)
    main_layout.addWidget(dock._consent_widget)


def _build_generate_row(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # Generate + Exit row. Exit is shown in the PROMPT state (zone
    # selected) so the user always has a one-click way back to LAUNCH,
    # but is hidden while generation is in flight to avoid a "cancel mid-
    # run" footgun.
    generate_row = QHBoxLayout()
    generate_row.setContentsMargins(0, 0, 0, 0)
    generate_row.setSpacing(6)

    dock._generate_btn = QPushButton(tr("Generate"))
    dock._generate_btn.setToolTip(tr("Run the AI edit on your selected zone"))
    dock._generate_btn.setCursor(QtC.PointingHandCursor)
    dock._generate_btn.setEnabled(False)
    dock._update_generate_style()
    dock._generate_btn.clicked.connect(dock._on_generate_clicked)
    dock._generate_btn.setVisible(False)
    generate_row.addWidget(dock._generate_btn, 1)

    dock._exit_btn = QPushButton(tr("Exit"))
    dock._exit_btn.setToolTip(tr("Exit and return to the start"))
    dock._exit_btn.setCursor(QtC.PointingHandCursor)
    # Width: hold a longer label ("Quitter", "Salir", "Sair") without
    # clipping. We use minimumWidth instead of fixedWidth so future
    # translations longer than the current set still fit.
    dock._exit_btn.setMinimumWidth(88)
    dock._exit_btn.setMinimumHeight(36)
    dock._exit_btn.setStyleSheet(_BTN_GHOST)
    dock._exit_btn.clicked.connect(dock._on_exit_clicked)
    dock._exit_btn.setVisible(False)
    generate_row.addWidget(dock._exit_btn, 0)

    main_layout.addLayout(generate_row)


def _build_progress_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # Progress section
    dock._progress_widget = QWidget()
    progress_layout = QVBoxLayout(dock._progress_widget)
    progress_layout.setContentsMargins(0, 0, 0, 0)
    progress_layout.setSpacing(4)
    dock._progress_label = QLabel(tr("Preparing..."))
    dock._progress_label.setStyleSheet("font-size: 11px; color: palette(text);")
    progress_layout.addWidget(dock._progress_label)
    dock._progress_bar = QProgressBar()
    dock._progress_bar.setRange(0, 100)
    dock._progress_bar.setValue(0)
    dock._progress_bar.setTextVisible(False)
    progress_layout.addWidget(dock._progress_bar)

    dock._progress_widget.setVisible(False)
    main_layout.addWidget(dock._progress_widget)


def _build_status_section(dock: AIEditDockWidget) -> None:
    # Status message box (same pattern as AI Segmentation info boxes)
    dock._status_widget = QWidget()
    dock._status_widget.setVisible(False)
    status_box_layout = QHBoxLayout(dock._status_widget)
    status_box_layout.setContentsMargins(8, 6, 8, 6)
    status_box_layout.setSpacing(8)
    dock._status_icon = QLabel()
    _ico = dock._status_widget.style().pixelMetric(
        QStyle.PixelMetric.PM_SmallIconSize
    )
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
        "font-size: 11px; background: transparent; border: none;"
    )
    status_box_layout.addWidget(dock._status_label, 1)

    # CTA button displayed for paid-tier monthly quota exhaustion.
    # Paid users are already subscribed - the action here is plan management,
    # not subscription.
    dock._limit_cta_btn = QPushButton(tr("Manage plan"))
    dock._limit_cta_btn.setToolTip(tr("Open your dashboard to upgrade or wait for renewal."))
    dock._limit_cta_btn.setCursor(QtC.PointingHandCursor)
    dock._limit_cta_btn.setStyleSheet(_BTN_BLUE)
    dock._limit_cta_btn.clicked.connect(dock._on_limit_cta_clicked)
    dock._limit_cta_btn.setVisible(False)
    dock._limit_cta_url = ""
