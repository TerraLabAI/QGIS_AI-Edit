from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QKeySequence, QShortcut
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.i18n import tr
from ...core.reference_image_store import ReferenceImageStore
from ..credit_ring import CreditRing
from ..onboarding_hint import (
    BLUE_TINT,
    HINT_FIRST_STEPS,
    NEUTRAL_TINT,
    DismissibleHint,
    open_guide,
)
from ..panel_helpers import make_section_header
from ..panels.markup_panel import MarkupPanel
from ..panels.vectorize_panel import VectorizePanel
from ..reference_images_widget import ReferenceImagesWidget
from ..version_strip import VersionStrip
from .account import DockAccountMixin
from .chrome import DockChromeMixin
from .generation_state import DockGenerationStateMixin
from .library import DockLibraryMixin
from .prompt_container import _PromptContainer
from .prompts import DockPromptMixin
from .style import (
    _BTN_BLUE,
    _BTN_GHOST,
    _BTN_GREEN,
    _BTN_GREEN_AUTH,
    _FOOTER_ICON_BTN_STYLE,
    _FOOTER_ICON_TOGGLE_STYLE,
    _FOOTER_MENU_STYLE,
    BRAND_BLUE,
    BRAND_BLUE_HOVER,
)
from .tools_footer import DockToolsFooterMixin
from .versions import DockVersionsMixin
from .widgets import _FooterIconButton, _SubmitTextEdit, _ZoneGestureGlyph

_make_section_header = make_section_header  # backward-compat alias


class AIEditDockWidget(
    DockChromeMixin,
    DockAccountMixin,
    DockLibraryMixin,
    DockGenerationStateMixin,
    DockVersionsMixin,
    DockPromptMixin,
    DockToolsFooterMixin,
    QDockWidget,
):
    """Dock widget with prompt-first flow.

    The prompt view is always visible after activation. The selection tool
    stays active so the user can draw a zone at any time. The Generate
    button is disabled (shows "Select your zone") until a zone is drawn.
    """

    stop_clicked = pyqtSignal()
    generate_clicked = pyqtSignal(str)
    # Post-generation base picked in the version strip (0 = Original, i = the
    # i-th generated version). The plugin mirrors it on the canvas and uses it
    # to pick the export base + parent for the next edit.
    base_version_selected = pyqtSignal(int)
    retry_clicked = pyqtSignal(str)       # retry on same zone with (possibly edited) prompt
    pairing_requested = pyqtSignal(str)        # one-click connect: emits the minted pairing code
    pairing_cancel_requested = pyqtSignal(str)  # user cancelled the browser handoff (emits the code)
    settings_clicked = pyqtSignal()
    launch_clicked = pyqtSignal()          # user clicked "Launch AI Edit" on entry screen
    try_example_requested = pyqtSignal()   # empty-canvas one-click onboarding (basemap + demo zone + prompt)
    exit_clicked = pyqtSignal()            # user clicked the always-visible Exit button
    zone_clear_requested = pyqtSignal()    # Escape pressed while a zone was selected
    markup_clicked = pyqtSignal()          # user picked Tools → Mark up
    vectorize_clicked = pyqtSignal()       # user picked Tools → Vectorize
    # (layer_id, color_hex, class_label) from the "Extract regions" CTA in
    # the result panel. class_label seeds the class_name attribute on
    # every produced polygon (empty for mono-class templates that lack
    # a server-side label).
    vectorize_suggestion_clicked = pyqtSignal(str, str, str)
    # Footer Before/After is a checkable toggle: True = user wants the
    # swipe map tool armed, False = user wants it disarmed. The plugin
    # routes both states to the SwipeController.
    swipe_toggled = pyqtSignal(bool)
    markup_done_clicked = pyqtSignal()     # user clicked Done in Mark up panel
    markup_clear_clicked = pyqtSignal()    # user clicked Clear all in Mark up
    markup_tool_changed = pyqtSignal(str)  # 'pencil' | 'arrow' | 'circle'
    markup_color_changed = pyqtSignal(QColor)
    vectorize_done_clicked = pyqtSignal()  # user clicked Done in Vectorize panel
    # (template_id, template_name) for analytics - id is stable, name is human-readable.
    template_selected = pyqtSignal(str, str)
    # Fired when the prompt library opens; plugin listens and kicks off a
    # background catalog refetch so the NEXT open shows the latest server
    # state. Stale-while-revalidate: this open uses whatever the dock has
    # cached, the refetch updates `self._server_catalog` for next time.
    catalog_refresh_requested = pyqtSignal()
    # A past generation (history row dict) the user wants re-added to the map
    # as a georeferenced layer, or downloaded to disk. The plugin owns the
    # download + write + layer-add orchestration.
    history_add_to_map = pyqtSignal(dict)
    history_download = pyqtSignal(dict)
    # A past generation the user chose to fully reproduce: the plugin restores
    # the prompt, the reference image(s), and the original zone on the map.
    history_restore = pyqtSignal(dict)
    # Fired when the Help (?) menu opens (True) or closes (False). The
    # plugin uses this to light the green active tint on the help button
    # and to disarm the swipe map tool when the user opens another action.
    help_menu_open_changed = pyqtSignal(bool)

    def __init__(self, parent=None, reference_store: ReferenceImageStore | None = None):
        super().__init__(tr("AI Edit by TerraLab"), parent)
        # Stable objectName lets QGIS save/restore the dock (position + visibility) across
        # sessions, like the native Layers panel.
        self.setObjectName("AIEditDockWidget")
        self.setAllowedAreas(QtC.LeftDockWidgetArea | QtC.RightDockWidgetArea)
        # Scale min width with font so hi-DPI displays don't crop the footer.
        try:
            char_w = self.fontMetrics().averageCharWidth()
            self.setMinimumWidth(max(300, int(char_w * 50)))
        except Exception:
            self.setMinimumWidth(300)
        self._reference_store = reference_store
        self._library_client = None
        self._library_auth_manager = None
        self._server_catalog: dict | None = None

        # Cache of the prompt library's Recent + Favorites, so reopening the
        # library is instant instead of refetching + blank-then-fill each time.
        # Seeded from a persistent disk cache so even the FIRST open of a session
        # renders immediately (then a background refresh picks up any changes);
        # marked dirty so that refresh always runs once per session and after a
        # new generation.
        from ...core.prompts import history_cache as _history_cache

        self._library_recent_cache: list = _history_cache.get_recent_jobs()
        self._library_favorite_cache: list = _history_cache.get_favorite_jobs()
        self._library_history_loaded = bool(
            self._library_recent_cache or self._library_favorite_cache
        )
        self._library_history_dirty = True

        # Armed template: set when the user picks a preset from the prompt
        # library so edits to the prompt text don't drop the association
        # (used by plugin.py to keep vector hints + Vectorize CTA active).
        self._active_template_id: str | None = None
        self._active_template_name: str | None = None

        # Parented so the 12 s shot dies with the dock, not against a deleted widget.
        self._status_hide_timer: QTimer | None = None

        # Global Escape: exit the flow no matter where focus is (canvas while
        # drawing a zone, prompt textarea, progress bar, etc.). WindowShortcut
        # context lets the shortcut fire on the parent main window's key events
        # via ShortcutOverride, which beats the map tool's local Escape handler.
        self._escape_shortcut = QShortcut(QKeySequence(QtC.Key_Escape), self)
        self._escape_shortcut.setContext(QtC.WindowShortcut)
        self._escape_shortcut.activated.connect(self._on_escape_pressed)

        # Global Enter / Return: launch generation from anywhere in the dock.
        # The prompt textarea consumes Return in its own keyPressEvent so this
        # shortcut only fires when focus is on a non-text-input child.
        self._generate_shortcut_return = QShortcut(QKeySequence(QtC.Key_Return), self)
        self._generate_shortcut_return.setContext(QtC.WindowShortcut)
        self._generate_shortcut_return.activated.connect(self._on_generate_shortcut)
        self._generate_shortcut_enter = QShortcut(QKeySequence(QtC.Key_Enter), self)
        self._generate_shortcut_enter.setContext(QtC.WindowShortcut)
        self._generate_shortcut_enter.activated.connect(self._on_generate_shortcut)

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
        # Kept so the version strip can be re-homed under the progress bar while
        # a generation runs (see _place_version_strip).
        self._main_layout = main_layout

        # Empty-canvas hero (no visible layer). Added with stretch factor 1 and
        # built as a vertically-expanding wrapper so the card CENTERS in the
        # otherwise-blank panel (same pattern as _select_zone_section below)
        # instead of clinging to the top.
        self._warning_widget = self._build_warning_widget()
        self._warning_widget.setVisible(False)
        main_layout.addWidget(self._warning_widget, 1)

        # --- Launch section (entry screen, matches AI Segmentation pattern) ---
        self._launch_section = QWidget()
        launch_layout = QVBoxLayout(self._launch_section)
        launch_layout.setContentsMargins(0, 0, 0, 0)
        launch_layout.setSpacing(8)

        self._launch_btn = QPushButton(tr("Launch AI Edit"))
        self._launch_btn.setToolTip(tr("Start a new AI edit session"))
        self._launch_btn.setCursor(QtC.PointingHandCursor)
        self._launch_btn.setMinimumHeight(36)
        self._launch_btn.setStyleSheet(_BTN_GREEN_AUTH)
        self._launch_btn.clicked.connect(self.launch_clicked.emit)
        launch_layout.addWidget(self._launch_btn)

        self._launch_section.setVisible(False)
        main_layout.addWidget(self._launch_section)

        # --- Select-zone section: centered empty-state hero inviting the user
        # to draw the zone. The dock is otherwise blank in this state, so the
        # design-system Empty State pattern (gesture glyph + short warm copy,
        # centered) gives it a clear focal point instead of a lonely top box. ---
        self._select_zone_section = QWidget()
        self._select_zone_section.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        sz_layout = QVBoxLayout(self._select_zone_section)
        sz_layout.setContentsMargins(16, 0, 16, 0)
        sz_layout.setSpacing(10)
        sz_layout.addStretch(1)

        self._select_zone_icon = _ZoneGestureGlyph(QColor(BRAND_BLUE))
        sz_layout.addWidget(
            self._select_zone_icon, 0, Qt.AlignmentFlag.AlignHCenter
        )

        self._select_zone_header = _make_section_header(tr("Draw your zone"))
        self._select_zone_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sz_layout.addWidget(self._select_zone_header)

        # Full-width centered text: wrapping on the real width keeps the layout's
        # heightForWidth correct, so the copy is never clipped (a maxWidth + an
        # alignment flag would mis-size the height and cut the last lines off).
        self._select_zone_hint = QLabel(
            tr("Hold the left mouse button and drag to draw a box on the map. Then describe the change you want.")
        )
        self._select_zone_hint.setWordWrap(True)
        self._select_zone_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._select_zone_hint.setStyleSheet(
            "QLabel { font-size: 12px; color: palette(text);"
            " background: transparent; border: none; }"
        )
        sz_layout.addWidget(self._select_zone_hint)

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
        self._select_zone_exit_btn = QPushButton(tr("Exit"))
        self._select_zone_exit_btn.setToolTip(tr("Exit and return to the start"))
        self._select_zone_exit_btn.setCursor(QtC.PointingHandCursor)
        self._select_zone_exit_btn.setMinimumWidth(88)
        self._select_zone_exit_btn.setMinimumHeight(32)
        self._select_zone_exit_btn.setStyleSheet(_BTN_GHOST)
        self._select_zone_exit_btn.clicked.connect(self._on_exit_clicked)
        zone_exit_row.addWidget(self._select_zone_exit_btn)
        zone_exit_row.addStretch()
        sz_layout.addLayout(zone_exit_row)

        sz_layout.addStretch(1)

        self._select_zone_section.setVisible(False)
        # Stretch factor so the section claims vertical room (competing with the
        # trailing footer spacer) and its inner stretches can centre the hero.
        main_layout.addWidget(self._select_zone_section, 1)

        # --- Prompt section (shown after zone selected) ---
        self._prompt_section = QWidget()
        self._prompt_section.setContentsMargins(0, 0, 0, 0)
        self._prompt_layout = QVBoxLayout(self._prompt_section)
        self._prompt_layout.setContentsMargins(0, 0, 0, 0)
        self._prompt_layout.setSpacing(6)

        # Soft, non-blocking warning shown at the top when the drawn zone is so
        # zoomed out the model can't resolve small features (set by the plugin
        # on zone selection). Amber to read as "heads up", not an error.
        self._zone_guidance_hint = QLabel()
        self._zone_guidance_hint.setWordWrap(True)
        self._zone_guidance_hint.setStyleSheet(
            "QLabel { background-color: rgb(255, 230, 150); "
            "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; "
            "padding: 6px 8px; font-size: 11px; color: #333333; }"
        )
        self._zone_guidance_hint.setVisible(False)
        self._prompt_layout.addWidget(self._zone_guidance_hint)

        self._prompt_header = _make_section_header(tr("What should AI change?"))
        self._prompt_header.setVisible(True)
        self._prompt_layout.addWidget(self._prompt_header)

        self._prompt_input = _SubmitTextEdit()
        self._prompt_input.setPlaceholderText(
            tr("type your prompt or pick from the library...")
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
        self._prompt_container.markup_clicked.connect(self.markup_clicked.emit)
        self._prompt_layout.addWidget(self._prompt_container)

        # Soft, non-blocking guidance hint shown live under the prompt when the
        # text looks off-rails (asks for a vector file, or talks to the tool
        # like a Q&A/counting bot). Steers the user without blocking Generate.
        # Detection is high-precision (see detect_prompt_guidance); a valid
        # edit/detect/segment instruction never shows this.
        self._prompt_guidance_hint = QLabel()
        self._prompt_guidance_hint.setWordWrap(True)
        self._prompt_guidance_hint.setStyleSheet(
            "QLabel { background-color: rgba(25, 118, 210, 0.08); "
            "border: 1px solid rgba(25, 118, 210, 0.2); border-radius: 4px; "
            "padding: 6px 8px; font-size: 11px; color: palette(text); }"
        )
        self._prompt_guidance_hint.setVisible(False)
        self._prompt_layout.addWidget(self._prompt_guidance_hint)

        # Hidden by default: revealed by set_zone_selected() once the user
        # draws a rectangle. Initial dock state only shows the "Select your
        # zone" button.
        self._prompt_section.setVisible(False)
        main_layout.addWidget(self._prompt_section)

        # Reference images widget - created once, moved between the prompt
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
            self._reference_widget.upsell_requested.connect(self._show_reference_upsell)
            # Forward container actions: drop on container + paste in textbox +
            # paperclip click all funnel into the reference widget.
            self._prompt_container.files_dropped.connect(self._reference_widget.add_paths)
            self._prompt_container.layers_dropped.connect(self._reference_widget.add_layers)
            self._prompt_container.attach_clicked.connect(
                self._reference_widget.open_file_picker
            )
            self._prompt_input.images_pasted.connect(self._reference_widget.add_paths)
            # Idle: keep the widget out of the title bar by hiding it until placed.
            self._reference_widget.setVisible(False)
        else:
            self._reference_widget = None

        # Consent checkbox (shown only until first generation). Use native
        # QGIS style so the checkmark glyph renders correctly.
        self._consent_check = QCheckBox()
        # Pre-ticked to cut first-run friction: the box shows checked, Generate
        # is enabled, and the affirmative act (clicking Generate with the terms
        # right there) is what records consent. Unticking re-adds the gate.
        self._consent_check.setChecked(True)
        self._consent_check.setText("")  # text set via label below
        # Bigger, easier-to-hit indicator (the default is tiny and hard to click).
        # Size only, no border/background, so the native checkmark still renders.
        self._consent_check.setStyleSheet(
            "QCheckBox::indicator { width: 18px; height: 18px; }"
        )
        self._consent_check.setCursor(QtC.PointingHandCursor)
        consent_layout = QHBoxLayout()
        consent_layout.setContentsMargins(0, 0, 0, 0)
        consent_layout.setSpacing(8)
        consent_layout.addWidget(self._consent_check, 0, QtC.AlignTop)
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
        # Width: hold a longer label ("Quitter", "Salir", "Sair") without
        # clipping. We use minimumWidth instead of fixedWidth so future
        # translations longer than the current set still fit.
        self._exit_btn.setMinimumWidth(88)
        self._exit_btn.setMinimumHeight(36)
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
        # Manual link routing (not setOpenExternalLinks): http links still open
        # in the browser, but the "Report a problem" sentinel opens the in-app
        # log-report dialog instead of being handed to the OS as a bad URL.
        self._status_label.linkActivated.connect(self._on_status_link)
        self._status_label.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        status_box_layout.addWidget(self._status_label, 1)

        # CTA button displayed for paid-tier monthly quota exhaustion.
        # Paid users are already subscribed - the action here is plan management,
        # not subscription.
        self._limit_cta_btn = QPushButton(tr("Manage plan"))
        self._limit_cta_btn.setToolTip(tr("Open your dashboard to upgrade or wait for renewal."))
        self._limit_cta_btn.setCursor(QtC.PointingHandCursor)
        self._limit_cta_btn.setStyleSheet(_BTN_BLUE)
        self._limit_cta_btn.clicked.connect(self._on_limit_cta_clicked)
        self._limit_cta_btn.setVisible(False)
        self._limit_cta_url = ""

        # --- Result section (shown after generation complete, iteration flow) ---
        # A single prompt screen. The version strip below the prompt is the base
        # picker: Original is pinned left, each result appends to the right, and
        # the selected tile is what the next edit builds on. Lives inside
        # _result_section so every state transition that hides it hides together.
        self._result_section = QWidget()
        self._result_layout = QVBoxLayout(self._result_section)
        self._result_layout.setContentsMargins(0, 0, 0, 0)
        self._result_layout.setSpacing(6)

        # --- Prompt + version strip + Generate -----------------------------
        self._result_prompt_widget = QWidget()
        self._result_prompt_layout = QVBoxLayout(self._result_prompt_widget)
        self._result_prompt_layout.setContentsMargins(0, 0, 0, 0)
        self._result_prompt_layout.setSpacing(6)

        # Editable prompt (edit and retry)
        self._result_prompt_input = _SubmitTextEdit()
        self._result_prompt_input.setPlaceholderText(
            tr("Type a new prompt to retry, or pick an action below")
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
        self._result_prompt_container.markup_clicked.connect(self.markup_clicked.emit)
        if self._reference_widget is not None:
            self._result_prompt_container.files_dropped.connect(
                self._reference_widget.add_paths
            )
            self._result_prompt_container.layers_dropped.connect(
                self._reference_widget.add_layers
            )
            self._result_prompt_container.attach_clicked.connect(
                self._reference_widget.open_file_picker
            )
            self._result_prompt_input.images_pasted.connect(
                self._reference_widget.add_paths
            )
        self._result_prompt_layout.addWidget(self._result_prompt_container)

        # Same soft off-rails hint as the first-run prompt, so iterating on a
        # v1/v2 gets the same guidance (vector / measure / chatbot).
        self._result_guidance_hint = QLabel()
        self._result_guidance_hint.setWordWrap(True)
        self._result_guidance_hint.setStyleSheet(
            "QLabel { background-color: rgba(25, 118, 210, 0.08); "
            "border: 1px solid rgba(25, 118, 210, 0.2); border-radius: 4px; "
            "padding: 6px 8px; font-size: 11px; color: palette(text); }"
        )
        self._result_guidance_hint.setVisible(False)
        self._result_prompt_layout.addWidget(self._result_guidance_hint)

        # Version strip: the base picker. Original pinned left, results append
        # right, selected tile drives the next edit. Hidden until seeded.
        self._version_strip = VersionStrip()
        self._version_strip.version_selected.connect(self._on_version_selected)
        self._result_prompt_layout.addWidget(self._version_strip)

        # Action row: Generate (primary, flex) + Exit (ghost, fixed).
        result_actions_row = QHBoxLayout()
        result_actions_row.setContentsMargins(0, 4, 0, 0)
        result_actions_row.setSpacing(6)

        self._result_regenerate_btn = QPushButton(tr("Generate"))
        self._result_regenerate_btn.setToolTip(
            tr("Generate on the same zone using the current map view")
        )
        self._result_regenerate_btn.setCursor(QtC.PointingHandCursor)
        self._result_regenerate_btn.setStyleSheet(_BTN_GREEN)
        self._result_regenerate_btn.clicked.connect(self._on_retry_clicked)
        result_actions_row.addWidget(self._result_regenerate_btn, 1)

        self._result_exit_btn = QPushButton(tr("Exit"))
        self._result_exit_btn.setToolTip(tr("Exit and return to the start"))
        self._result_exit_btn.setCursor(QtC.PointingHandCursor)
        # See `_exit_btn` above for why this is a minimum rather than fixed
        # width.
        self._result_exit_btn.setMinimumWidth(88)
        self._result_exit_btn.setMinimumHeight(36)
        self._result_exit_btn.setStyleSheet(_BTN_GHOST)
        self._result_exit_btn.clicked.connect(self._on_exit_clicked)
        result_actions_row.addWidget(self._result_exit_btn, 0)

        self._result_prompt_layout.addLayout(result_actions_row)

        # Minimal status line - shown under the action row after generation.
        # Submitting the prompt (Enter key) and the Generate button both
        # trigger a regen on the same zone.
        self._layer_saved_label = QLabel()
        self._layer_saved_label.setWordWrap(True)
        self._layer_saved_label.setTextFormat(QtC.RichText)
        self._layer_saved_label.setStyleSheet(
            "font-size: 11px; color: palette(text); background: transparent;"
            " border: none; padding: 4px 0 0 0;"
        )
        # ArrowCursor on the wrapper; Qt switches to PointingHand over the <a>.
        self._layer_saved_label.setCursor(QtC.ArrowCursor)
        self._layer_saved_label.linkActivated.connect(self._on_layer_saved_link_clicked)
        self._layer_saved_label.setVisible(False)
        self._saved_layer_id: str | None = None
        self._result_prompt_layout.addWidget(self._layer_saved_label)

        # --- Vectorize suggestion row (template-driven, hidden by default) ---
        # Shown after a generation when the template carried a vector_color
        # in the catalog. One click opens the Vectorize panel with the
        # source layer locked and the swatch pre-filled. Hidden in every
        # other case so it doesn't add noise to ad-hoc prompts.
        self._vectorize_cta_section = QWidget()
        cta_layout = QHBoxLayout(self._vectorize_cta_section)
        cta_layout.setContentsMargins(0, 4, 0, 0)
        cta_layout.setSpacing(6)
        self._vectorize_cta_swatch = QLabel()
        self._vectorize_cta_swatch.setFixedSize(14, 14)
        self._vectorize_cta_swatch.setStyleSheet(
            "background: rgba(128,128,128,0.3); border: 1px solid rgba(128,128,128,0.5);"
            " border-radius: 3px;"
        )
        cta_layout.addWidget(self._vectorize_cta_swatch)
        self._vectorize_cta_btn = QPushButton()
        self._vectorize_cta_btn.setText(tr("Vectorize this result") + " →")
        self._vectorize_cta_btn.setFlat(True)
        self._vectorize_cta_btn.setCursor(QtC.PointingHandCursor)
        self._vectorize_cta_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            f" color: {BRAND_BLUE}; padding: 4px 0px;"
            " font-size: 12px; text-align: left; }"
            f"QPushButton:hover {{ color: {BRAND_BLUE_HOVER};"
            " text-decoration: underline; }}"
        )
        self._vectorize_cta_btn.clicked.connect(self._on_vectorize_cta_clicked)
        cta_layout.addWidget(self._vectorize_cta_btn, 1)
        self._vectorize_cta_section.setVisible(False)
        self._vectorize_cta_pending: tuple[str, str, str] | None = None
        self._result_prompt_layout.addWidget(self._vectorize_cta_section)

        self._result_layout.addWidget(self._result_prompt_widget)
        self._result_prompt_widget.setVisible(False)

        self._result_section.setVisible(False)
        main_layout.addWidget(self._result_section)

        # Status box + CTA placed after result section so they always appear below
        main_layout.addWidget(self._status_widget)
        main_layout.addWidget(self._limit_cta_btn)

        # Trial exhausted info box - conversion panel shown when a free-tier
        # user runs out of credits. Title + 3 benefit bullets + primary button.
        self._trial_info_box = QFrame()
        self._trial_info_box.setStyleSheet(
            "QFrame { background: rgba(25,118,210,0.08); "
            "border: 1px solid rgba(25,118,210,0.2); "
            "border-radius: 4px; }"
            "QLabel { background: transparent; border: none; }"
        )
        trial_layout = QVBoxLayout(self._trial_info_box)
        trial_layout.setContentsMargins(12, 12, 12, 12)
        trial_layout.setSpacing(8)
        self._trial_info_text = QLabel("")
        self._trial_info_text.setWordWrap(True)
        self._trial_info_text.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: palette(text);"
        )
        trial_layout.addWidget(self._trial_info_text)
        benefits_html = "<br>".join((
            tr("Subscribe to unlock:"),
            "&nbsp;&nbsp;✓&nbsp; " + tr("3,000 credits every month"),
            "&nbsp;&nbsp;✓&nbsp; " + tr("Detailed and Maximum output"),
            "&nbsp;&nbsp;✓&nbsp; " + tr("Cancel anytime"),
        ))
        self._trial_info_benefits = QLabel(benefits_html)
        self._trial_info_benefits.setWordWrap(True)
        self._trial_info_benefits.setTextFormat(QtC.RichText)
        self._trial_info_benefits.setStyleSheet(
            "font-size: 11px; color: palette(text);"
        )
        trial_layout.addWidget(self._trial_info_benefits)
        self._trial_info_btn = QPushButton(tr("Subscribe"))
        self._trial_info_btn.setCursor(QtC.PointingHandCursor)
        self._trial_info_btn.setMinimumHeight(32)
        self._trial_info_btn.setStyleSheet(_BTN_BLUE)
        self._trial_info_btn.clicked.connect(self._on_trial_info_subscribe_clicked)
        trial_layout.addWidget(self._trial_info_btn)
        self._trial_info_url = ""
        # Kept for backwards compatibility with show_trial_exhausted_info callers
        # that still set a link; rendered inline as a fallback if the button is
        # ever hidden by external state.
        self._trial_info_link = QLabel("")
        self._trial_info_link.setOpenExternalLinks(True)
        self._trial_info_link.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        self._trial_info_link.setVisible(False)
        trial_layout.addWidget(self._trial_info_link)
        self._trial_info_box.setVisible(False)
        main_layout.addWidget(self._trial_info_box)

        main_layout.addStretch()

        layout.addWidget(self._main_widget)

        # Mark up panel - full-dock workflow opened via Tools menu, hidden
        # by default; swaps with _main_widget while the user is annotating.
        self._markup_panel = MarkupPanel(self)
        self._markup_panel.setVisible(False)
        self._markup_panel.tool_changed.connect(self.markup_tool_changed.emit)
        self._markup_panel.color_changed.connect(self.markup_color_changed.emit)
        self._markup_panel.clear_clicked.connect(self.markup_clear_clicked.emit)
        self._markup_panel.done_clicked.connect(self.markup_done_clicked.emit)
        layout.addWidget(self._markup_panel)

        # Vectorize panel - same swap pattern as Mark up.
        self._vectorize_panel = VectorizePanel(self)
        self._vectorize_panel.setVisible(False)
        self._vectorize_panel.done_clicked.connect(self.vectorize_done_clicked.emit)
        layout.addWidget(self._vectorize_panel)

        # The Before/After swipe has no dock panel: it's a toggle on the
        # footer Before/After button that arms a map tool on the canvas.
        # See SwipeController in swipe_panel.py and the wiring in plugin.py.

        # Spacer to push footer to bottom
        layout.addStretch()

        # --- Update notification, pinned at the bottom (above the footer) so it
        # stays visible in every state (idle, generating, result). Most users
        # never check for plugin updates, so this is how they learn one exists.
        self._setup_update_notification(layout)

        # First-steps guide banner, pinned just above the footer so it survives
        # the content view swaps and never crowds the flow. Gated to the idle
        # LAUNCH screen of a signed-in user (see _should_show_first_steps),
        # dismissible, and re-showable from Account Settings. Starts hidden; the
        # gate reveals it on the LAUNCH screen. The link opens the written guide
        # (UTM + best-effort telemetry).
        self._first_steps_hint = DismissibleHint(
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
            visibility_gate=self._should_show_first_steps,
        )
        self._first_steps_hint.action.connect(lambda: open_guide("post_signin"))
        self._first_steps_hint.setVisible(False)
        layout.addWidget(self._first_steps_hint)

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
        self._footer_row = footer_row

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

        # "&&" so Qt renders a literal ampersand instead of consuming "&" as
        # a mnemonic accelerator (which would underline the next character).
        # Text is (re)set by _apply_footer_responsive, which shortens it to
        # "Upgrade" when the dock is too narrow for the full label.
        self._upgrade_cta = QPushButton(tr("Unlock more detail"))
        self._upgrade_cta.setToolTip(
            tr("Subscribe to unlock Detailed and Maximum output, 3,000 credits per month, cancel anytime.")
        )
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
        # Tracks whether the upsell *should* be shown (subscribers shouldn't).
        self._upgrade_cta_wanted = False
        # Tracks whether the credit ring + count have data; resizeEvent uses
        # this to keep them hidden on narrow docks.
        self._credits_wanted = False

        footer_row.addStretch()

        # Mark up is reachable via the pencil chip next to the prompt; the
        # footer button has been removed to avoid duplication. Alt+M still
        # opens markup via the global shortcut wired below.
        self._markup_shortcut = QShortcut(QKeySequence("Alt+M"), self)
        self._markup_shortcut.setContext(QtC.WindowShortcut)
        self._markup_shortcut.activated.connect(self.markup_clicked.emit)

        self._vectorize_btn = _FooterIconButton(footer_widget)
        self._vectorize_btn.setToolTip(tr("Vectorize"))
        self._vectorize_btn.setAccessibleName(tr("Vectorize"))
        self._vectorize_btn.setCursor(QtC.PointingHandCursor)
        self._vectorize_btn.setFocusPolicy(QtC.NoFocus)
        self._vectorize_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
        self._vectorize_btn.setIcon(self._make_polygon_glyph_icon())
        self._vectorize_btn.setIconSize(QSize(20, 20))
        self._vectorize_btn.clicked.connect(self.vectorize_clicked.emit)
        vectorize_seq = QKeySequence("Alt+V")
        self._vectorize_btn.setShortcut(vectorize_seq)
        self._vectorize_btn.setToolTip(
            tr("Vectorize ({})").format(
                vectorize_seq.toString(QKeySequence.SequenceFormat.NativeText)
            )
        )
        self._vectorize_btn.setVisible(False)
        footer_row.addWidget(self._vectorize_btn)

        # Swipe button - opens the Before/after compare panel. Hidden until
        # an AI Edit output exists so the footer stays empty on first launch
        # (visibility synced by set_vectorize_button_visible together with
        # the vectorize footer button - both depend on having a generation).
        # Before/After is a checkable toggle: click once to arm the swipe
        # map tool, click again (or Esc on the canvas) to disarm. When
        # armed the button paints a green tint so the user can see at a
        # glance which tool the canvas is in.
        self._swipe_btn = _FooterIconButton(footer_widget)
        self._swipe_btn.setAccessibleName(tr("Before / after"))
        self._swipe_btn.setCursor(QtC.PointingHandCursor)
        self._swipe_btn.setFocusPolicy(QtC.NoFocus)
        self._swipe_btn.setStyleSheet(_FOOTER_ICON_TOGGLE_STYLE)
        self._swipe_btn.setIcon(self._make_swipe_glyph_icon())
        self._swipe_btn.setIconSize(QSize(20, 20))
        self._swipe_btn.setCheckable(True)
        self._swipe_btn.setEnabled(False)  # gated on active layer eligibility
        self._swipe_btn.toggled.connect(self.swipe_toggled.emit)
        swipe_seq = QKeySequence("Alt+B")
        self._swipe_btn.setShortcut(swipe_seq)
        self._swipe_btn.setToolTip(
            tr("Before / after ({})").format(
                swipe_seq.toString(QKeySequence.SequenceFormat.NativeText)
            )
        )
        self._swipe_btn.setVisible(False)
        footer_row.addWidget(self._swipe_btn)

        # Tutorial button - painted open-book glyph, always visible (signed in
        # and signed out) so a lost user can reach the step-by-step guide from
        # anywhere. Order: tutorial, gear, help - it groups with the help "?" as
        # a learn action. Opens the written guide with UTM + best-effort event.
        self._tutorial_btn = _FooterIconButton(footer_widget)
        # U+1F4D6 OPEN BOOK as text (the footer style is already 22px), matching
        # AI Segmentation's footer tutorial glyph exactly so the two plugins'
        # tutorial icons look identical.
        self._tutorial_btn.setText("\U0001F4D6")
        self._tutorial_btn.setToolTip(tr("Open the step-by-step tutorial"))
        self._tutorial_btn.setCursor(QtC.PointingHandCursor)
        self._tutorial_btn.setFocusPolicy(QtC.NoFocus)
        self._tutorial_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
        self._tutorial_btn.clicked.connect(self._on_open_guide_footer)
        footer_row.addWidget(self._tutorial_btn)

        # Settings button - gear icon, opens the Account Settings dialog
        # directly. Shortcuts have moved inside that dialog.
        self._settings_btn = _FooterIconButton(footer_widget)
        self._settings_btn.setIcon(self._make_gear_glyph_icon())
        self._settings_btn.setIconSize(QSize(20, 20))
        self._settings_btn.setToolTip(tr("Settings"))
        self._settings_btn.setCursor(QtC.PointingHandCursor)
        self._settings_btn.setFocusPolicy(QtC.NoFocus)
        self._settings_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
        self._settings_btn.clicked.connect(self._on_settings_btn_clicked)
        self._settings_btn.setVisible(False)  # shown when activated
        footer_row.addWidget(self._settings_btn)

        # Help menu - question mark icon, always visible.
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
        help_menu.addAction(tr("Report a problem"), self._on_report_problem)
        self._help_btn.setMenu(help_menu)
        # Force the hover tint off when the popup closes - Qt does not
        # synthesise a Leave event in this case. Also light the green
        # active tint while the menu is open and broadcast the change so
        # the plugin can disarm the swipe map tool.
        help_menu.aboutToShow.connect(
            lambda: (self._help_btn.set_active(True),
                     self.help_menu_open_changed.emit(True))
        )
        help_menu.aboutToHide.connect(
            lambda btn=self._help_btn: (
                btn.setDown(False), btn.set_hovered(False), btn.set_active(False),
                self.help_menu_open_changed.emit(False),
            )
        )
        footer_row.addWidget(self._help_btn)

        layout.addWidget(footer_widget)

        # Wrap in scroll area (matches AI Segmentation)
        scroll_area = QScrollArea()
        scroll_area.setWidget(main_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtC.FrameNoFrame)
        scroll_area.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        self.setWidget(scroll_area)
        # Kept so the footer responsive logic reads the true available width
        # (the viewport excludes the vertical scrollbar), not the dock width.
        self._scroll_area = scroll_area

        # State
        self._zone_selected = False
        # While an onboarding basemap warms its online tiles, Generate is held
        # so the first-run demo cannot export a blank input (crop error).
        self._imagery_loading = False
        self._activated = False
        self._checking_credits = False
        self._swipe_eligible = False
        self._swipe_panel_lock = False
        self._is_free_tier = True  # default hidden until confirmed Pro
        self._cached_used: int | None = None
        self._cached_limit: int | None = None
        # Universal default. Every tier starts on "1K"; paid users can still
        # bump to 2K/4K by hand, but the dock never opens on a higher tier by
        # default. Free-tier confirmation keeps coercing to "1K" anyway.
        self._selected_resolution = "1K"
        # Credit cost per resolution. Used to suffix the Generate/Regenerate
        # button text ("Generate (30 credits)"). Overwritten by
        # set_resolution_credit_costs once the server config loads.
        self._resolution_credit_costs: dict[str, int] = {"1K": 20, "2K": 30, "4K": 40}

        # Layer monitoring. We listen to add/remove, visibility-changed in the
        # legend, AND project lifecycle (readProject/cleared) so the Launch
        # button stays in sync when the user starts a new project or opens a
        # different one - those transitions replace the layerTreeRoot, which
        # invalidates any visibilityChanged binding made before.
        # layersAdded/layersRemoved fire before QGIS finishes syncing the layer
        # tree, so the new node is not yet in layerTreeRoot().findLayers() when a
        # synchronous handler runs. Defer the gate re-check by one event loop tick
        # (same pattern as _on_project_loaded) so adding the first basemap on a
        # fresh session actually enables the Launch button.
        QgsProject.instance().layersAdded.connect(self._schedule_layer_warning_update)
        QgsProject.instance().layersRemoved.connect(self._schedule_layer_warning_update)
        QgsProject.instance().layerTreeRoot().visibilityChanged.connect(
            self._update_layer_warning
        )
        QgsProject.instance().readProject.connect(self._on_project_loaded)
        QgsProject.instance().cleared.connect(self._on_project_loaded)
        self._update_layer_warning()

    def _on_escape_pressed(self):
        """Escape walks the flow back one step at a time.

        SWIPE ACTIVE → disarm swipe (highest priority — the canvas-tool
        Escape handler only fires when canvas has focus, but the swipe
        button stays checked otherwise; route the dock-level Escape
        through here so swipe always exits cleanly).
        ZONE_SELECTED → SELECTING_ZONE (drop the zone, keep the panel open).
        SELECTING_ZONE / LAUNCH / RESULT → exit to LAUNCH.
        A generation in progress is never cancelable by Escape - credits are
        already booked; only the Stop button can cancel.
        """
        if not self.isVisible() or not self._main_widget.isVisible():
            return
        if self._progress_widget.isVisible():
            return
        # WindowShortcut means the dock receives Escape from anywhere in the
        # QGIS main window. Bail out unless the user is genuinely interacting
        # with AI Edit (canvas focused with our map tool, or focus is inside
        # the dock itself) so we don't steal Escape from QGIS digitizing,
        # measure tool, identify panel, etc.
        if not self._is_escape_for_us():
            return
        # Swipe takes priority: clicking the (already-checked) button
        # toggles it off, which routes through swipe_toggled → plugin →
        # swipe_controller.stop().
        if self._swipe_btn.isChecked():
            self._swipe_btn.click()
            return
        if self._zone_selected and self._prompt_section.isVisible():
            self.zone_clear_requested.emit()
            return
        self.exit_clicked.emit()

    def _is_escape_for_us(self) -> bool:
        """Decide whether an Escape keypress should drive AI Edit's flow.

        True when focus is inside the dock, OR the canvas currently runs
        one of our map tools (rectangle selection / Mark up pencil/arrow/
        circle). Anywhere else, Escape belongs to the active QGIS tool.
        """
        from qgis.PyQt.QtWidgets import QApplication

        focus = QApplication.focusWidget()
        if focus is not None:
            w = focus
            while w is not None:
                if w is self:
                    return True
                w = w.parent()
        try:
            from qgis.utils import iface as _iface
            if _iface is None:
                return False
            tool = _iface.mapCanvas().mapTool()
        except Exception:
            return False
        if tool is None:
            return False
        from ..panels.swipe_panel import _SwipeMapTool
        from ..tools.markup_tools import _MarkupBaseMapTool
        from ..tools.selection_map_tool import RectangleSelectionTool
        return isinstance(
            tool, (RectangleSelectionTool, _MarkupBaseMapTool, _SwipeMapTool)
        )

    def closeEvent(self, event):
        """Visibility-only teardown. Persistent disconnects live in cleanup()."""
        self._stop_progress_animation()
        if self._progress_widget.isVisible():
            self.stop_clicked.emit()
        self._vectorize_panel.deactivate()
        super().closeEvent(event)

    def cleanup(self):
        """Called once from plugin.unload() before the dock is removed."""
        try:
            QgsProject.instance().layersAdded.disconnect(self._schedule_layer_warning_update)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layersRemoved.disconnect(self._schedule_layer_warning_update)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layerTreeRoot().visibilityChanged.disconnect(
                self._update_layer_warning
            )
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().readProject.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().cleared.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass
        # LayerTreeComboBox hooks its own QgsProject signals; nothing else cleans it.
        try:
            combo = getattr(self._vectorize_panel, "_layer_combo", None)
            if combo is not None and hasattr(combo, "cleanup"):
                combo.cleanup()
        except Exception:  # nosec B110
            pass
