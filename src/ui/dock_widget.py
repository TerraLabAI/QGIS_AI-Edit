from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices, QKeySequence
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QShortcut,
    QStyle,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import qt_compat as QtC
from ..core.activation_manager import (
    get_subscribe_url,
    get_tutorial_url,
    has_consent,
)
from ..core.i18n import tr

# ---------------------------------------------------------------------------
# Brand colors (Material Design 2 — shared with AI Segmentation)
# ---------------------------------------------------------------------------
BRAND_GREEN = "#2e7d32"
BRAND_GREEN_HOVER = "#1b5e20"
BRAND_GREEN_DISABLED = "#c8e6c9"
BRAND_BLUE = "#1976d2"
BRAND_BLUE_HOVER = "#1565c0"
BRAND_RED = "#d32f2f"
BRAND_RED_HOVER = "#b71c1c"
BRAND_GRAY = "#757575"
BRAND_GRAY_HOVER = "#616161"
BRAND_DISABLED = "#b0bec5"
DISABLED_TEXT = "#666666"
ERROR_TEXT = "#ef5350"
SUCCESS_TEXT = "#66bb6a"

MAX_PROMPT_CHARS = 2000

TERRALAB_URL = (
    "https://terra-lab.ai/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dock_branding"
)
SUPPORT_EMAIL = "yvann.barbot@terra-lab.ai"

# ---------------------------------------------------------------------------
# Reusable QSS style constants (design system)
# ---------------------------------------------------------------------------
_BTN_GREEN = (
    f"QPushButton {{ background-color: {BRAND_GREEN}; color: #000000;"
    f" padding: 8px 16px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GREEN_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_GREEN_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_GREEN_AUTH = (
    f"QPushButton {{ background-color: {BRAND_GREEN}; color: white;"
    f" font-weight: bold; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GREEN_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; }}"
)

_BTN_BLUE = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" padding: 6px 12px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE_AUTH = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: white;"
    f" font-weight: bold; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; }}"
)

_BTN_GRAY = (
    f"QPushButton {{ background-color: {BRAND_GRAY}; color: #000000;"
    f" padding: 4px 8px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GRAY_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT}; }}"
)

_BTN_DISABLED = (
    f"QPushButton {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT};"
    f" padding: 8px 16px; }}"
)

_BTN_GHOST = (
    "QPushButton { background-color: transparent; color: palette(text);"
    " padding: 8px 16px; border-radius: 4px;"
    " border: 1px solid rgba(128, 128, 128, 0.35); }"
    "QPushButton:hover { background-color: rgba(128, 128, 128, 0.15);"
    " border: 1px solid rgba(128, 128, 128, 0.5); }"
    f"QPushButton:disabled {{ background-color: rgba(128, 128, 128, 0.08);"
    f" border: 1px solid rgba(128, 128, 128, 0.15); color: {DISABLED_TEXT}; }}"
)

_RES_BTN_NEUTRAL = (
    "QPushButton { background-color: rgba(128, 128, 128, 0.12);"
    " padding: 6px 12px; border: 1px solid rgba(128, 128, 128, 0.2);"
    " border-radius: 4px; color: palette(text); font-size: 11px; }"
    "QPushButton:hover { background-color: rgba(128, 128, 128, 0.2); }"
)

_RES_BTN_SELECTED = (
    "QPushButton { background-color: rgba(66, 133, 244, 0.25);"
    " padding: 6px 12px; border: 1px solid rgba(66, 133, 244, 0.6);"
    " border-radius: 4px; color: palette(text); font-size: 11px; font-weight: bold; }"
)

_RES_BTN_LOCKED = (
    "QPushButton { background-color: rgba(128, 128, 128, 0.06);"
    " padding: 6px 12px; border: 1px solid rgba(128, 128, 128, 0.12);"
    f" border-radius: 4px; color: {DISABLED_TEXT}; font-size: 11px; }}"
)

_PROMPT_INPUT_NORMAL = (
    "QTextEdit { border: 1px solid rgba(128,128,128,0.3);"
    " border-radius: 4px; padding: 6px; color: palette(text); }"
)

_PROMPT_INPUT_READONLY = (
    "QTextEdit { border: 1px solid rgba(128,128,128,0.3);"
    " border-radius: 4px; padding: 6px;"
    " background-color: rgba(128,128,128,0.1); color: #888888; }"
)

_INSTRUCTION_BOX = (
    "QLabel {"
    "  background-color: rgba(128, 128, 128, 0.12);"
    "  border: 1px solid rgba(128, 128, 128, 0.25);"
    "  border-radius: 4px;"
    "  padding: 8px;"
    "  font-size: 10px;"
    "  color: palette(text);"
    "}"
)

_SECTION_HEADER = (
    "font-weight: bold; font-size: 12px; color: palette(text);"
    " margin: 0px; padding: 0px 0px 2px 0px;"
)

_SECTION_HEADER_EXTRA_TOP = (
    "font-weight: bold; font-size: 12px; color: palette(text); padding-top: 6px;"
)


def _make_section_header(text: str, extra_top: bool = False) -> QLabel:
    """Create a section header label."""
    label = QLabel(text)
    label.setStyleSheet(_SECTION_HEADER_EXTRA_TOP if extra_top else _SECTION_HEADER)
    label.setContentsMargins(0, 0, 0, 0)
    return label


class _SubmitTextEdit(QTextEdit):
    """QTextEdit where Enter submits and Shift+Enter inserts a newline."""

    submitted = pyqtSignal()

    def keyPressEvent(self, event):  # noqa: N802
        if (
            event.key() in (QtC.Key_Return, QtC.Key_Enter)
            and not event.modifiers() & QtC.ShiftModifier  # noqa: W503
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class AIEditDockWidget(QDockWidget):
    """Dock widget with dynamic flow matching AI Segmentation pattern."""

    select_zone_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    generate_clicked = pyqtSignal(str)
    retry_clicked = pyqtSignal(str)       # retry on same zone with (possibly edited) prompt
    new_zone_clicked = pyqtSignal(str)    # keep prompt, select new zone
    activation_attempted = pyqtSignal(str)
    change_key_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    # (template_id, template_name) for analytics — id is stable, name is human-readable.
    template_selected = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(tr("AI Edit by TerraLab"), parent)
        self.setAllowedAreas(QtC.LeftDockWidgetArea | QtC.RightDockWidgetArea)
        self.setMinimumWidth(260)

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

        # Section header
        self._edit_header = _make_section_header(tr("Get started with AI Edit:"))
        main_layout.addWidget(self._edit_header)

        # Warning widget (no visible layer) — above start button
        self._warning_widget = self._build_warning_widget()
        self._warning_widget.setVisible(False)
        main_layout.addWidget(self._warning_widget)

        # --- IDLE entry points ---
        self._idle_section = QWidget()
        idle_layout = QVBoxLayout(self._idle_section)
        idle_layout.setContentsMargins(0, 0, 0, 0)
        idle_layout.setSpacing(6)

        # Primary: browse templates (recommended path)
        self._browse_btn = QPushButton(tr("Browse AI Prompts"))
        self._browse_btn.setCursor(QtC.PointingHandCursor)
        self._browse_btn.setStyleSheet(_BTN_GREEN)
        self._browse_btn.clicked.connect(self._on_browse_templates_clicked)
        idle_layout.addWidget(self._browse_btn)

        # Separator "or"
        or_label = QLabel(tr("or"))
        or_label.setAlignment(QtC.AlignCenter)
        or_label.setStyleSheet("font-size: 11px; color: palette(text); padding: 0px;")
        idle_layout.addWidget(or_label)

        # Secondary: write own prompt (free edit)
        self._start_btn = QPushButton(tr("Write Your Own Prompt"))
        self._start_btn.setCursor(QtC.PointingHandCursor)
        self._start_btn.setStyleSheet(_BTN_BLUE)
        self._start_btn.clicked.connect(self._on_start_clicked)
        idle_layout.addWidget(self._start_btn)

        main_layout.addWidget(self._idle_section)

        # Instruction info box (shown during drawing)
        self._instruction_box = QLabel()
        self._instruction_box.setWordWrap(True)
        self._instruction_box.setStyleSheet(_INSTRUCTION_BOX)
        self._instruction_box.setVisible(False)
        main_layout.addWidget(self._instruction_box)

        # --- Prompt section (shown after zone selected) ---
        self._prompt_section = QWidget()
        self._prompt_section.setContentsMargins(0, 0, 0, 0)
        prompt_layout = QVBoxLayout(self._prompt_section)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        prompt_layout.setSpacing(0)

        self._prompt_header = _make_section_header(tr("What should AI change?"))
        prompt_layout.addWidget(self._prompt_header)

        self._prompt_input = _SubmitTextEdit()
        self._prompt_input.setPlaceholderText(tr("Type your prompt or use a template..."))
        self._prompt_input.document().setDocumentMargin(0)
        self._prompt_input.setMinimumHeight(60)
        self._prompt_input.setMaximumHeight(60)
        self._prompt_input.setStyleSheet(_PROMPT_INPUT_NORMAL)
        self._prompt_input.textChanged.connect(self._on_prompt_changed)
        self._prompt_input.submitted.connect(self._on_generate_clicked)
        self._prompt_input.document().documentLayout().documentSizeChanged.connect(
            self._adjust_prompt_height
        )
        prompt_layout.addWidget(self._prompt_input)

        self._templates_btn = QPushButton(tr("Browse Templates"))
        self._templates_btn.setCursor(QtC.PointingHandCursor)
        self._templates_btn.setStyleSheet(
            "QPushButton { text-align: left; color: palette(text); "
            "font-size: 11px; border: 1px solid rgba(128,128,128,0.25); "
            "border-radius: 4px; padding: 4px 8px; "
            "background-color: rgba(128,128,128,0.08); }"
            "QPushButton:hover { background-color: rgba(128,128,128,0.15); }"
            f"QPushButton:disabled {{ background-color: rgba(128,128,128,0.1); "
            f"border: 1px solid rgba(128,128,128,0.15); color: {DISABLED_TEXT}; }}"
        )
        self._templates_btn.clicked.connect(self._on_browse_templates_clicked)
        prompt_layout.addWidget(self._templates_btn)

        # Resolution selector (Pro only — hidden for free tier)
        self._resolution_selector, self._res_btns = self._build_resolution_selector()
        self._resolution_selector.setVisible(False)
        prompt_layout.addWidget(self._resolution_selector)

        self._prompt_section.setVisible(False)
        main_layout.addWidget(self._prompt_section)

        # Consent checkbox (shown only until first generation)
        self._consent_check = QCheckBox()
        self._consent_check.setStyleSheet(
            "QCheckBox::indicator {"
            "  width: 16px; height: 16px;"
            "  border: 1px solid palette(text);"
            "  border-radius: 3px;"
            "  background-color: palette(base);"
            "}"
            f"QCheckBox::indicator:checked {{"
            f"  background-color: {BRAND_BLUE};"
            f"  border-color: {BRAND_BLUE};"
            f"}}"
        )
        self._consent_check.setText("")  # text set via label below
        consent_layout = QHBoxLayout()
        consent_layout.setContentsMargins(0, 0, 0, 0)
        consent_layout.setSpacing(4)
        consent_layout.addWidget(self._consent_check, 0)
        _terms_url = (
            "https://terra-lab.ai/terms-of-sale"
            "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=consent_terms"
        )
        _privacy_url = (
            "https://terra-lab.ai/privacy-policy"
            "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=consent_privacy"
        )
        consent_text = QLabel(
            f'I agree to the <a href="{_terms_url}" '
            f'style="color: {BRAND_BLUE};">Terms</a> and '
            f'<a href="{_privacy_url}" '
            f'style="color: {BRAND_BLUE};">Privacy Policy</a>'
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

        # Generate button
        self._generate_btn = QPushButton(tr("Generate"))
        self._generate_btn.setCursor(QtC.PointingHandCursor)
        self._generate_btn.setEnabled(False)
        self._update_generate_style()
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        self._generate_btn.setVisible(False)
        main_layout.addWidget(self._generate_btn)

        # Stop button
        self._stop_btn = QPushButton(tr("Stop"))
        self._stop_btn.setCursor(QtC.PointingHandCursor)
        self._stop_btn.setStyleSheet(_BTN_GRAY)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.setVisible(False)
        main_layout.addWidget(self._stop_btn)

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
        self._status_label.setOpenExternalLinks(True)
        self._status_label.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        status_box_layout.addWidget(self._status_label, 1)

        # CTA button displayed for quota exhaustion
        self._limit_cta_btn = QPushButton(tr("Subscribe"))
        self._limit_cta_btn.setCursor(QtC.PointingHandCursor)
        self._limit_cta_btn.setStyleSheet(_BTN_BLUE)
        self._limit_cta_btn.clicked.connect(self._on_limit_cta_clicked)
        self._limit_cta_btn.setVisible(False)
        self._limit_cta_url = ""

        # --- Result section (shown after generation complete, iteration flow) ---
        self._result_section = QWidget()
        result_layout = QVBoxLayout(self._result_section)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(6)

        # "What's next?" header
        result_header = _make_section_header(tr("What's next?"))
        result_layout.addWidget(result_header)

        # Editable prompt (edit and retry)
        self._result_prompt_input = _SubmitTextEdit()
        self._result_prompt_input.setPlaceholderText(
            tr("Edit the prompt above and retry, or pick a new action below")
        )
        self._result_prompt_input.document().setDocumentMargin(0)
        self._result_prompt_input.setMinimumHeight(50)
        self._result_prompt_input.setMaximumHeight(50)
        self._result_prompt_input.setStyleSheet(_PROMPT_INPUT_NORMAL)
        self._result_prompt_input.submitted.connect(self._on_retry_clicked)
        self._result_prompt_input.textChanged.connect(self._on_result_prompt_changed)
        self._result_prompt_input.document().documentLayout().documentSizeChanged.connect(
            self._adjust_result_prompt_height
        )
        result_layout.addWidget(self._result_prompt_input)

        # Browse Templates in result section
        self._result_templates_btn = QPushButton(tr("Browse Templates"))
        self._result_templates_btn.setCursor(QtC.PointingHandCursor)
        self._result_templates_btn.setStyleSheet(
            "QPushButton { text-align: left; color: palette(text); "
            "font-size: 11px; border: 1px solid rgba(128,128,128,0.25); "
            "border-radius: 4px; padding: 4px 8px; "
            "background-color: rgba(128,128,128,0.08); }"
            "QPushButton:hover { background-color: rgba(128,128,128,0.15); }"
        )
        self._result_templates_btn.clicked.connect(self._on_browse_templates_clicked)
        result_layout.addWidget(self._result_templates_btn)

        # Resolution selector for retry (Pro only)
        self._retry_resolution_selector, self._retry_res_btns = self._build_resolution_selector()
        self._retry_resolution_selector.setVisible(False)
        result_layout.addWidget(self._retry_resolution_selector)

        # Primary: retry with edited prompt
        self._retry_btn = QPushButton(tr("Retry on Same Area"))
        self._retry_btn.setCursor(QtC.PointingHandCursor)
        self._retry_btn.setStyleSheet(_BTN_GREEN)
        self._retry_btn.clicked.connect(self._on_retry_clicked)
        result_layout.addWidget(self._retry_btn)

        # Secondary row: new area + done
        secondary_row = QHBoxLayout()
        secondary_row.setSpacing(6)

        self._new_zone_btn = QPushButton(tr("New Area"))
        self._new_zone_btn.setCursor(QtC.PointingHandCursor)
        self._new_zone_btn.setStyleSheet(_BTN_BLUE)
        self._new_zone_btn.clicked.connect(self._on_new_zone_clicked)
        secondary_row.addWidget(self._new_zone_btn)

        self._done_btn = QPushButton(tr("Done"))
        self._done_btn.setCursor(QtC.PointingHandCursor)
        self._done_btn.setStyleSheet(_BTN_GHOST)
        self._done_btn.clicked.connect(self._on_done_clicked)
        secondary_row.addWidget(self._done_btn)

        result_layout.addLayout(secondary_row)

        # Layer saved info (shown until user clicks Done or New Area)
        self._layer_saved_label = QLabel()
        self._layer_saved_label.setWordWrap(True)
        self._layer_saved_label.setStyleSheet(
            "font-size: 11px; color: palette(text); background: rgba(102,187,106,0.10);"
            " border: 1px solid rgba(102,187,106,0.3); border-radius: 4px;"
            " padding: 6px 8px;"
        )
        self._layer_saved_label.setVisible(False)
        result_layout.addWidget(self._layer_saved_label)

        self._result_section.setVisible(False)
        main_layout.addWidget(self._result_section)

        # Status box + CTA placed after result section so they always appear below
        main_layout.addWidget(self._status_widget)
        main_layout.addWidget(self._limit_cta_btn)

        # Trial exhausted info box
        self._trial_info_box = QFrame()
        self._trial_info_box.setStyleSheet(
            "QFrame { background: rgba(25,118,210,0.08); "
            "border: 1px solid rgba(25,118,210,0.2); "
            "border-radius: 4px; padding: 10px; }"
        )
        trial_layout = QVBoxLayout(self._trial_info_box)
        trial_layout.setContentsMargins(10, 10, 10, 10)
        trial_layout.setSpacing(6)
        self._trial_info_text = QLabel("")
        self._trial_info_text.setWordWrap(True)
        self._trial_info_text.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        trial_layout.addWidget(self._trial_info_text)
        self._trial_info_link = QLabel("")
        self._trial_info_link.setOpenExternalLinks(True)
        self._trial_info_link.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        trial_layout.addWidget(self._trial_info_link)
        self._trial_info_box.setVisible(False)
        main_layout.addWidget(self._trial_info_box)

        main_layout.addStretch()

        layout.addWidget(self._main_widget)

        # Spacer to push footer to bottom
        layout.addStretch()

        # Footer section — two rows: credits on top, links below
        footer_widget = QWidget()
        footer_vbox = QVBoxLayout(footer_widget)
        footer_vbox.setContentsMargins(0, 0, 0, 4)
        footer_vbox.setSpacing(2)

        # Row 1: credits + upgrade pill
        credits_row = QHBoxLayout()
        credits_row.setContentsMargins(0, 0, 0, 0)
        credits_row.setSpacing(8)

        credits_row.addStretch()

        self._credits_label = QLabel()
        self._credits_label.setStyleSheet(
            "font-size: 11px; color: palette(text); background: transparent; border: none;"
        )
        self._credits_label.setVisible(False)
        credits_row.addWidget(self._credits_label)

        self._upgrade_cta = QPushButton(tr("Subscribe"))
        self._upgrade_cta.setCursor(QtC.PointingHandCursor)
        self._upgrade_cta.setStyleSheet(
            f"QPushButton {{ border: 1px solid {BRAND_BLUE}; color: {BRAND_BLUE};"
            f" border-radius: 8px; padding: 1px 8px; font-size: 11px;"
            f" background: transparent; font-weight: normal; }}"
            f"QPushButton:hover {{ background: rgba(25,118,210,0.12); }}"
        )
        self._upgrade_cta.clicked.connect(self._on_upgrade_clicked)
        self._upgrade_cta.setVisible(False)
        credits_row.addWidget(self._upgrade_cta)

        footer_vbox.addLayout(credits_row)

        # Row 2: links — [Settings] [Tutorial] [Contact us]
        links_row = QHBoxLayout()
        links_row.setContentsMargins(0, 0, 0, 0)
        links_row.setSpacing(16)

        links_row.addStretch()

        self._settings_btn = QLabel(f'<a href="#" style="color: {BRAND_BLUE};">{tr("Settings")}</a>')
        self._settings_btn.setStyleSheet("font-size: 13px;")
        self._settings_btn.setCursor(QtC.PointingHandCursor)
        self._settings_btn.linkActivated.connect(lambda _: self._on_settings_btn_clicked())
        self._settings_btn.setVisible(False)
        links_row.addWidget(self._settings_btn)

        for text, url, handler in [
            (tr("Contact us"), "#", self._on_contact_us),
            (tr("Tutorial"), get_tutorial_url(), None),
            (tr("Shortcuts"), "#", self._on_show_shortcuts),
        ]:
            link = QLabel(f'<a href="{url}" style="color: {BRAND_BLUE};">{text}</a>')
            link.setStyleSheet("font-size: 13px;")
            link.setCursor(QtC.PointingHandCursor)
            if handler:
                link.linkActivated.connect(handler)
            else:
                link.setOpenExternalLinks(True)
            links_row.addWidget(link)

        footer_vbox.addLayout(links_row)

        layout.addWidget(footer_widget)

        # Wrap in scroll area (matches AI Segmentation)
        scroll_area = QScrollArea()
        scroll_area.setWidget(main_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtC.FrameNoFrame)
        self.setWidget(scroll_area)

        # State
        self._active = False
        self._zone_selected = False
        self._activated = False
        self._checking_credits = False
        self._from_template = False
        self._is_free_tier = True  # default hidden until confirmed Pro
        self._selected_resolution = "1K"
        # Fallback costs used until server config is loaded
        self._resolution_credit_costs: dict[str, int] = {"1K": 20, "2K": 30, "4K": 40}

        # Keyboard shortcuts (G to start, Esc to stop)
        self._start_shortcut = QShortcut(QKeySequence("G"), self)
        self._start_shortcut.activated.connect(self._on_start_clicked)
        self._stop_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._stop_shortcut.activated.connect(self._on_stop_clicked)

        self._instruction_box.setText(tr("Click and drag to select your edit area"))

        # Layer monitoring
        QgsProject.instance().layersAdded.connect(self._update_layer_warning)
        QgsProject.instance().layersRemoved.connect(self._update_layer_warning)
        self._update_layer_warning()

    def _setup_title_bar(self):
        """Custom title bar matching AI Segmentation style with close button."""
        title_widget = QWidget()
        title_outer = QVBoxLayout(title_widget)
        title_outer.setContentsMargins(0, 0, 0, 0)
        title_outer.setSpacing(0)

        # Title row
        title_row = QHBoxLayout()
        title_row.setContentsMargins(4, 0, 0, 0)
        title_row.setSpacing(0)

        title_label = QLabel(
            "AI Edit by "
            f'<a href="{TERRALAB_URL}" '
            f'style="color: {BRAND_BLUE}; text-decoration: none;">TerraLab</a>'
        )
        title_label.setOpenExternalLinks(True)
        title_row.addWidget(title_label)
        title_row.addStretch()

        icon_size = self.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize)

        float_btn = QToolButton()
        float_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton)
        )
        float_btn.setFixedSize(icon_size + 4, icon_size + 4)
        float_btn.setAutoRaise(True)
        float_btn.clicked.connect(lambda: self.setFloating(not self.isFloating()))
        title_row.addWidget(float_btn)

        close_btn = QToolButton()
        close_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
        )
        close_btn.setFixedSize(icon_size + 4, icon_size + 4)
        close_btn.setAutoRaise(True)
        close_btn.clicked.connect(self.close)
        title_row.addWidget(close_btn)

        title_outer.addLayout(title_row)

        # Separator line (like AI Segmentation)
        separator = QFrame()
        separator.setFrameShape(QtC.FrameHLine)
        separator.setFrameShadow(QtC.FrameSunken)
        title_outer.addWidget(separator)

        self.setTitleBarWidget(title_widget)

    def _build_activation_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # --- How to set up AI Edit ---
        self._setup_header = QLabel(tr("Two steps to start using AI Edit"))
        self._setup_header.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: palette(text);"
        )
        layout.addWidget(self._setup_header)

        self._setup_desc = QLabel(
            tr("1. Sign up or sign in on terra-lab.ai to get your key")
            + "\n"
            + tr("2. Paste your key below to activate")
        )
        self._setup_desc.setWordWrap(True)
        self._setup_desc.setStyleSheet(_INSTRUCTION_BOX)
        layout.addWidget(self._setup_desc)

        layout.addSpacing(12)

        # --- Step 1: Create account (inside _signup_section) ---
        self._signup_section = QWidget()
        signup_layout = QVBoxLayout(self._signup_section)
        signup_layout.setContentsMargins(0, 0, 0, 0)
        signup_layout.setSpacing(8)

        step1_label = QLabel(tr("1. Sign up / Sign in"))
        step1_label.setStyleSheet("font-weight: bold; font-size: 12px; color: palette(text);")
        signup_layout.addWidget(step1_label)

        self._login_btn = QPushButton(tr("Get Your Key"))
        self._login_btn.setMinimumHeight(36)
        self._login_btn.setCursor(QtC.PointingHandCursor)
        self._login_btn.setStyleSheet(_BTN_GREEN_AUTH)
        self._login_btn.clicked.connect(self._on_login_clicked)
        signup_layout.addWidget(self._login_btn)

        login_hint = QLabel(tr("5 free generations — no credit card required"))
        login_hint.setAlignment(QtC.AlignCenter)
        login_hint.setWordWrap(True)
        login_hint.setStyleSheet("font-size: 11px; color: palette(text);")
        signup_layout.addWidget(login_hint)

        layout.addWidget(self._signup_section)

        layout.addSpacing(8)

        # --- Step 2: Paste key (outside _signup_section for change-key mode) ---
        self._step2_label = QLabel(tr("2. Paste your activation key"))
        self._step2_label.setStyleSheet("font-weight: bold; font-size: 12px; color: palette(text);")
        layout.addWidget(self._step2_label)

        self._key_input_widget = QWidget()
        key_input_layout = QHBoxLayout(self._key_input_widget)
        key_input_layout.setContentsMargins(0, 0, 0, 0)
        key_input_layout.setSpacing(6)
        self._code_input = QLineEdit()
        self._code_input.setPlaceholderText("tl_...")
        self._code_input.setMinimumHeight(28)
        self._code_input.returnPressed.connect(self._on_unlock_clicked)
        key_input_layout.addWidget(self._code_input)

        unlock_btn = QPushButton(tr("Activate"))
        unlock_btn.setMinimumHeight(28)
        unlock_btn.setMinimumWidth(70)
        unlock_btn.setStyleSheet(_BTN_BLUE_AUTH)
        unlock_btn.clicked.connect(self._on_unlock_clicked)
        key_input_layout.addWidget(unlock_btn)

        layout.addWidget(self._key_input_widget)

        # Cancel button (visible only in change-key mode)
        self._cancel_key_btn = QPushButton(tr("Cancel"))
        self._cancel_key_btn.setCursor(QtC.PointingHandCursor)
        self._cancel_key_btn.setStyleSheet(_BTN_GHOST)
        self._cancel_key_btn.clicked.connect(self._on_cancel_change_key)
        self._cancel_key_btn.setVisible(False)
        layout.addWidget(self._cancel_key_btn)

        # Activation message (errors / success)
        self._activation_message = QLabel("")
        self._activation_message.setAlignment(QtC.AlignCenter)
        self._activation_message.setWordWrap(True)
        self._activation_message.setStyleSheet("font-size: 11px;")
        self._activation_message.setVisible(False)
        layout.addWidget(self._activation_message)

        # CTA button displayed on activation flow when usage limit is reached
        self._activation_limit_cta_btn = QPushButton(tr("Subscribe"))
        self._activation_limit_cta_btn.setCursor(QtC.PointingHandCursor)
        self._activation_limit_cta_btn.setStyleSheet(_BTN_BLUE_AUTH)
        self._activation_limit_cta_btn.clicked.connect(self._on_activation_limit_cta_clicked)
        self._activation_limit_cta_btn.setVisible(False)
        layout.addWidget(self._activation_limit_cta_btn)
        self._activation_limit_cta_url = ""

        return widget

    def _build_warning_widget(self) -> QWidget:
        """Build yellow warning widget for when no layers are available."""
        widget = QWidget()
        widget.setStyleSheet(
            "QWidget { background-color: rgb(255, 230, 150); "
            "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; }"
            "QLabel { background: transparent; border: none; color: #333333; }"
        )
        warning_layout = QHBoxLayout(widget)
        warning_layout.setContentsMargins(8, 8, 8, 8)
        warning_layout.setSpacing(8)

        icon_label = QLabel()
        style = widget.style()
        warning_icon = style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        icon_label.setPixmap(warning_icon.pixmap(16, 16))
        icon_label.setFixedSize(16, 16)
        warning_layout.addWidget(icon_label, 0, QtC.AlignTop)

        self._warning_text = QLabel(tr("No visible layer. Add imagery to your project."))
        self._warning_text.setWordWrap(True)
        warning_layout.addWidget(self._warning_text, 1)

        return widget

    def _open_templates_dialog(self) -> dict | None:
        """Open the prompt templates dialog. Returns selected preset or None."""
        from .prompt_templates_dialog import PromptTemplatesDialog

        dlg = PromptTemplatesDialog(self)
        if dlg.exec():
            preset = dlg.get_selected_preset()
            if preset:
                self.template_selected.emit(
                    str(preset.get("id") or ""),
                    str(preset.get("label") or ""),
                )
            return preset
        return None

    # --- Public methods ---

    def set_activated(self, activated: bool):
        self._activated = activated
        self._activation_widget.setVisible(not activated)
        self._main_widget.setVisible(activated)
        self._settings_btn.setVisible(activated)
        if activated:
            self.hide_trial_info()
            self._update_layer_warning()
            self._cancel_key_btn.setVisible(False)
            self._upgrade_cta.setVisible(self._is_free_tier)
        else:
            self._setup_header.setVisible(True)
            self._setup_desc.setVisible(True)
            self._signup_section.setVisible(True)
            self._step2_label.setVisible(True)
            self._key_input_widget.setVisible(True)
            self._cancel_key_btn.setVisible(False)
            self._activation_message.setVisible(False)
            self.hide_activation_limit_cta()

    def show_change_key_mode(self):
        """Show only the key input, no signup flow. For users changing their key."""
        self._activated = False
        self._activation_widget.setVisible(True)
        self._main_widget.setVisible(False)
        self._settings_btn.setVisible(False)
        self._upgrade_cta.setVisible(False)
        self._setup_header.setVisible(False)
        self._setup_desc.setVisible(False)
        self._signup_section.setVisible(False)
        self._step2_label.setVisible(True)
        self._key_input_widget.setVisible(True)
        self._cancel_key_btn.setVisible(True)
        self._activation_message.setVisible(False)
        self.hide_activation_limit_cta()
        self._code_input.clear()
        self._code_input.setFocus()

    def hide_consent(self):
        """Hide the consent checkbox after first generation."""
        self._consent_widget.setVisible(False)

    def set_activation_message(self, text: str, is_error: bool = False):
        # Use brighter variants for dark theme readability
        self.hide_activation_limit_cta()
        color = ERROR_TEXT if is_error else SUCCESS_TEXT
        self._activation_message.setStyleSheet(f"font-size: 11px; color: {color};")
        self._activation_message.setText(text)
        self._activation_message.setVisible(True)

    def show_activation_limit_cta(self, subscribe_url: str):
        self._activation_limit_cta_url = subscribe_url
        self._activation_limit_cta_btn.setText(tr("Subscribe"))
        self._activation_limit_cta_btn.setVisible(True)

    def hide_activation_limit_cta(self):
        self._activation_limit_cta_btn.setVisible(False)
        self._activation_limit_cta_url = ""

    def set_credits(
        self,
        used: int | None = None,
        limit: int | None = None,
        is_free_tier: bool = False,
    ):
        """Update the credits indicator near the Generate button."""
        self._is_free_tier = is_free_tier
        if used is not None and limit is not None:
            remaining = max(0, limit - used)
            if is_free_tier:
                self._credits_label.setText(f"{remaining} / {limit} free trial credits remaining")
            else:
                self._credits_label.setText(f"{remaining} / {limit} credits remaining")
            self._credits_label.setVisible(True)
        else:
            self._credits_label.setVisible(False)
        self._upgrade_cta.setVisible(is_free_tier and self._activated)
        self._update_resolution_lock()
        self._update_generate_button_text()

    def set_active_mode(self):
        """Enter active mode: drawing rectangle."""
        self._active = True
        self._edit_header.setVisible(False)
        self._idle_section.setVisible(False)

        self._result_section.setVisible(False)
        self._warning_widget.setVisible(False)
        self._instruction_box.setVisible(True)
        self._hide_status_box()
        self._layer_saved_label.setVisible(False)

    def set_zone_selected(self):
        """Zone drawn: show prompt flow (no Start button)."""
        self._zone_selected = True
        self._edit_header.setVisible(False)
        self._idle_section.setVisible(False)

        self._result_section.setVisible(False)
        self._instruction_box.setVisible(False)
        self._hide_status_box()
        self._layer_saved_label.setVisible(False)
        self._prompt_section.setVisible(True)
        self._prompt_input.setReadOnly(False)
        self._prompt_input.setStyleSheet(_PROMPT_INPUT_NORMAL)
        self._templates_btn.setVisible(True)
        self._templates_btn.setEnabled(True)
        self._from_template = False
        self._consent_widget.setVisible(not has_consent())
        self._generate_btn.setVisible(True)
        self._stop_btn.setVisible(True)
        self._resolution_selector.setVisible(True)
        self._update_resolution_lock()
        self._start_shortcut.setEnabled(False)
        self._stop_shortcut.setEnabled(True)

        self._update_generate_enabled()
        QTimer.singleShot(0, self._prompt_input.setFocus)

    def _stop_progress_animation(self):
        """Stop the smooth progress animation timer if running."""
        if hasattr(self, "_progress_timer") and self._progress_timer is not None:
            self._progress_timer.stop()

    def set_idle(self):
        """Reset everything to initial state."""
        self._stop_progress_animation()
        self._hide_status_box()
        self._active = False
        self._zone_selected = False
        self._edit_header.setVisible(True)
        self._idle_section.setVisible(True)

        self._stop_btn.setVisible(False)
        self._instruction_box.setVisible(False)
        self._prompt_section.setVisible(False)
        self._consent_widget.setVisible(False)
        self._generate_btn.setVisible(False)
        self._progress_widget.setVisible(False)
        self._result_section.setVisible(False)
        self._layer_saved_label.setVisible(False)
        self._start_shortcut.setEnabled(True)
        self._stop_shortcut.setEnabled(True)
        self._prompt_input.clear()
        self._prompt_input.setFixedHeight(60)
        self._prompt_input.setReadOnly(False)
        self._prompt_input.setStyleSheet(_PROMPT_INPUT_NORMAL)
        self._result_prompt_input.clear()
        self._templates_btn.setEnabled(True)
        self._templates_btn.setVisible(True)
        self._from_template = False
        self._resolution_selector.setVisible(False)
        self._retry_resolution_selector.setVisible(False)
        self._selected_resolution = "1K"
        self._on_resolution_selected("1K")
        self._update_layer_warning()

    def set_generating(self, generating: bool):
        """Toggle generation state -- keep prompt visible but grayed out."""
        self._progress_widget.setVisible(generating)
        self._edit_header.setVisible(False)
        self._idle_section.setVisible(False)

        self._result_section.setVisible(False)
        self._warning_widget.setVisible(False)
        self._upgrade_cta.setVisible(False)

        if generating:
            # Reset progress bar to determinate 0%
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(0)
            self._hide_status_box()
            # Keep prompt section visible but disable interaction
            self._prompt_section.setVisible(True)
            self._prompt_input.setReadOnly(True)
            self._prompt_input.setStyleSheet(_PROMPT_INPUT_READONLY)
            self._templates_btn.setEnabled(False)
            self._consent_widget.setVisible(False)
            self._generate_btn.setVisible(False)
            self._stop_btn.setVisible(False)
            self._resolution_selector.setVisible(False)
            self._stop_shortcut.setEnabled(False)
            self._progress_label.setText(tr("Preparing..."))
        else:
            # Restore prompt interaction
            self._prompt_input.setReadOnly(False)
            self._prompt_input.setStyleSheet(_PROMPT_INPUT_NORMAL)
            self._templates_btn.setEnabled(True)
            self._consent_widget.setVisible(not has_consent() and self._zone_selected)
            self._generate_btn.setVisible(self._zone_selected)
            self._resolution_selector.setVisible(self._zone_selected)
            self._update_resolution_lock()
            self._stop_btn.setVisible(self._zone_selected)
            self._stop_shortcut.setEnabled(True)
            self._prompt_section.setVisible(self._zone_selected)

        self._start_shortcut.setEnabled(not generating)

    def set_generate_loading(self, loading: bool):
        """Toggle loading state on the Generate button during canvas export."""
        if loading:
            self._generate_btn_original_text = self._generate_btn.text()
            self._generate_btn.setText(tr("Preparing..."))
            self._generate_btn.setEnabled(False)
            self._generate_btn.setStyleSheet(_BTN_DISABLED)
        else:
            text = getattr(self, "_generate_btn_original_text", tr("Generate"))
            self._generate_btn.setText(text)
            self._update_generate_style()

    def set_progress_message(self, message: str, percentage: int = -1):
        """Update the progress label and bar during generation with smooth animation."""
        self._progress_label.setText(message)
        if percentage >= 0:
            self._progress_bar.setRange(0, 100)
            self._progress_target = percentage
            if not hasattr(self, "_progress_timer") or self._progress_timer is None:
                self._progress_timer = QTimer(self)
                self._progress_timer.setInterval(30)
                self._progress_timer.timeout.connect(self._animate_progress)
            if not self._progress_timer.isActive():
                self._progress_timer.start()

    def _animate_progress(self):
        """Smoothly animate progress bar toward target value."""
        current = self._progress_bar.value()
        target = getattr(self, "_progress_target", current)
        if current < target:
            self._progress_bar.setValue(current + 1)
        else:
            if hasattr(self, "_progress_timer") and self._progress_timer is not None:
                self._progress_timer.stop()

    def _show_status_box(self, message: str, box_type: str = "info"):
        """Show a styled status message box (AI Segmentation style)."""
        styles = {
            "error": (
                "QWidget { background-color: rgba(211, 47, 47, 0.25); "
                "border: 1px solid rgba(211, 47, 47, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #ef5350; }",
                QStyle.StandardPixmap.SP_MessageBoxCritical,
            ),
            "success": (
                "QWidget { background-color: rgba(46, 125, 50, 0.25); "
                "border: 1px solid rgba(46, 125, 50, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #66bb6a; }",
                QStyle.StandardPixmap.SP_DialogApplyButton,
            ),
            "warning": (
                "QWidget { background-color: rgb(255, 230, 150); "
                "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #333333; }",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            ),
            "info": (
                "QWidget { background-color: rgba(25, 118, 210, 0.08); "
                "border: 1px solid rgba(25, 118, 210, 0.2); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; }",
                QStyle.StandardPixmap.SP_MessageBoxInformation,
            ),
        }
        style_str, icon_enum = styles.get(box_type, styles["error"])
        self._status_widget.setStyleSheet(style_str)
        icon = self._status_widget.style().standardIcon(icon_enum)
        self._status_icon.setPixmap(icon.pixmap(self._status_icon_size, self._status_icon_size))
        self._status_label.setText(message)
        self._status_widget.setVisible(True)

    def _hide_status_box(self):
        self._status_widget.setVisible(False)
        self._status_label.setText("")
        self._hide_limit_cta()

    def set_status(self, message: str, is_error: bool = False):
        self._hide_limit_cta()
        if not message:
            self._hide_status_box()
        else:
            self._show_status_box(message, "error")
        self._trial_info_box.setVisible(False)

    def set_generation_complete(self, layer_name: str):
        """Show RESULT state with iteration options (retry / new zone / done)."""
        self._stop_progress_animation()
        self._progress_bar.setValue(100)
        self._progress_widget.setVisible(False)
        self._hide_status_box()
        self._active = False

        # Hide all non-result UI
        self._edit_header.setVisible(False)
        self._idle_section.setVisible(False)

        self._instruction_box.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        self._stop_btn.setVisible(False)
        self._consent_widget.setVisible(False)

        # Show result section with prompt pre-filled for editing
        last_prompt = self._prompt_input.toPlainText().strip()
        self._result_prompt_input.setPlainText(last_prompt)
        self._result_prompt_input.moveCursor(QtC.CursorEnd)
        self._result_prompt_input.setReadOnly(False)
        self._result_section.setVisible(True)
        self._retry_resolution_selector.setVisible(True)
        self._update_resolution_lock()

        # Persistent layer saved info (stays until Done or New Area)
        self._layer_saved_label.setText(
            tr('Layer saved as "{name}" — visible in your Layers panel').format(name=layer_name))
        self._layer_saved_label.setVisible(True)

        # Restore upgrade CTA visibility for free tier
        self._upgrade_cta.setVisible(self._is_free_tier and self._activated)

        self._start_shortcut.setEnabled(False)
        self._stop_shortcut.setEnabled(True)

    def show_trial_exhausted_info(self, message: str, subscribe_url: str):
        self._hide_limit_cta()
        self._trial_info_text.setText(message)
        self._trial_info_link.setText(
            f'<a href="{subscribe_url}" style="color: {BRAND_BLUE}; '
            f'font-weight: bold;">{tr("Subscribe")}</a>'
        )
        self._trial_info_box.setVisible(True)
        self._idle_section.setVisible(False)
        self._edit_header.setVisible(False)
        self._hide_status_box()

    def show_usage_limit_info(self, message: str, subscribe_url: str):
        self._show_status_box(message, "error")
        self._trial_info_box.setVisible(False)
        self._limit_cta_url = subscribe_url
        self._limit_cta_btn.setVisible(True)
        self._idle_section.setVisible(False)
        self._edit_header.setVisible(False)

    def set_checking_credits(self, checking: bool):
        """Show/hide a loading state while credits are being fetched.

        Prevents interaction with the idle section during the async gap
        between key activation and credit response.
        """
        self._checking_credits = checking
        if checking:
            self._idle_section.setVisible(False)
            self._edit_header.setVisible(False)
            self._trial_info_box.setVisible(False)
            self._show_status_box(tr("Checking credits..."), "info")
        else:
            self._hide_status_box()

    def hide_trial_info(self):
        self._trial_info_box.setVisible(False)
        self._hide_status_box()
        self._hide_limit_cta()
        # Only restore idle UI if we're actually in idle state
        # (not during result, generating, checking credits, etc.)
        if (
            not self._zone_selected
            and not self._active  # noqa: W503
            and not self._checking_credits  # noqa: W503
            and not self._result_section.isVisible()  # noqa: W503
        ):
            self._idle_section.setVisible(True)
            self._edit_header.setVisible(True)

    def get_activation_key(self) -> str:
        return self._code_input.text().strip()

    def set_activation_key(self, key: str):
        self._code_input.setText(key)

    def get_prompt(self) -> str:
        return self._prompt_input.toPlainText().strip()

    # --- Private methods ---

    def _update_layer_warning(self, *_args):
        """Show/hide warning based on layer availability."""
        if self._active or self._zone_selected:
            self._warning_widget.setVisible(False)
            return
        has_layers = bool(QgsProject.instance().mapLayers())
        self._warning_widget.setVisible(not has_layers)
        self._browse_btn.setEnabled(has_layers)
        self._start_btn.setEnabled(has_layers)

    def _on_start_clicked(self):
        """Start selection -- enter active mode (free edit path)."""
        if self._active:
            return
        if not self._start_btn.isEnabled():
            return
        self._from_template = False
        self.set_active_mode()
        self.select_zone_clicked.emit()

    def _on_settings_btn_clicked(self):
        self.settings_clicked.emit()

    def _on_upgrade_clicked(self):
        from ..core import telemetry
        telemetry.track("subscribe_link_clicked", {"source": "upgrade_cta"})
        QDesktopServices.openUrl(QUrl(get_subscribe_url()))

    def _on_stop_clicked(self):
        """Stop -- reset to idle."""
        if not self._active:
            return
        self.set_idle()
        self.stop_clicked.emit()

    def _on_key_toggle(self, checked: bool):
        pass

    def _on_login_clicked(self):
        """Open terra-lab.ai login page in system browser."""
        import webbrowser
        webbrowser.open("https://terra-lab.ai/login?product=ai-edit")

    def _on_cancel_change_key(self):
        """Cancel key change and restore the activated state."""
        from ..core.activation_manager import get_activation_key
        saved_key = get_activation_key()
        if saved_key:
            self._code_input.setText(saved_key)
            self.set_activated(True)
        else:
            self._signup_section.setVisible(True)
            self._cancel_key_btn.setVisible(False)

    def _on_unlock_clicked(self):
        code = self._code_input.text().strip()
        if not code:
            self.set_activation_message(tr("Enter your code"), is_error=True)
            return
        self.activation_attempted.emit(code)

    # ------------------------------------------------------------------
    # Resolution selector helpers
    # ------------------------------------------------------------------

    def _build_resolution_selector(self) -> tuple[QWidget, dict[str, QPushButton]]:
        """Build a labeled row of 1K / 2K / 4K toggle buttons."""
        widget = QWidget()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(2)

        label = QLabel(tr("Output resolution"))
        label.setStyleSheet("font-size: 11px; color: palette(text);")
        outer.addWidget(label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        btns: dict[str, QPushButton] = {}
        for res in ("1K", "2K", "4K"):
            btn = QPushButton(res)
            btn.setCursor(QtC.PointingHandCursor)
            btn.setStyleSheet(_RES_BTN_SELECTED if res == "1K" else _RES_BTN_NEUTRAL)
            btn.clicked.connect(lambda _, r=res: self._on_resolution_selected(r))
            row.addWidget(btn)
            btns[res] = btn
        outer.addLayout(row)
        return widget, btns

    def _update_resolution_lock(self):
        """Lock 2K/4K buttons for free tier users (teaser for paid)."""
        for btns in (self._res_btns, self._retry_res_btns):
            for res, btn in btns.items():
                if self._is_free_tier and res != "1K":
                    btn.setEnabled(True)  # Keep enabled to catch clicks and show tooltip
                    btn.setText(res)
                    btn.setToolTip(tr("Subscribe for higher resolution"))
                    btn.setStyleSheet(_RES_BTN_LOCKED)
                else:
                    btn.setEnabled(True)
                    btn.setText(res)
                    btn.setToolTip("")
                    if res == self._selected_resolution:
                        btn.setStyleSheet(_RES_BTN_SELECTED)
                    else:
                        btn.setStyleSheet(_RES_BTN_NEUTRAL)

    def _on_resolution_selected(self, label: str):
        """Handle resolution toggle click — sync both selectors."""
        if self._is_free_tier and label != "1K":
            subscribe_url = get_subscribe_url()
            self._show_status_box(
                tr("The {} resolution is an advanced feature reserved for subscribed users.").format(label)
                + f' <a href="{subscribe_url}" style="color: {BRAND_BLUE}; font-weight: bold;">'
                + tr("Subscribe") + "</a>",
                "warning"
            )
            QTimer.singleShot(5000, self._hide_status_box)
            return

        # Clear any existing status message if switching resolutions
        self._hide_status_box()

        self._selected_resolution = label
        self._update_resolution_lock()
        self._update_generate_button_text()

    def _update_generate_button_text(self):
        """Update Generate / Retry button text with credit cost."""
        cost = self._resolution_credit_costs.get(self._selected_resolution)
        if cost is not None:
            self._generate_btn.setText(tr("Generate") + f" ({cost} credits)")
            self._retry_btn.setText(tr("Retry on Same Area") + f" ({cost} credits)")
        else:
            self._generate_btn.setText(tr("Generate"))
            self._retry_btn.setText(tr("Retry on Same Area"))

    def set_resolution_credit_costs(self, costs: dict[str, int]):
        """Set credit costs per resolution (from server config)."""
        if costs:
            self._resolution_credit_costs = costs
        self._update_generate_button_text()

    def get_selected_resolution(self) -> str:
        """Return the user-selected resolution label."""
        return self._selected_resolution

    def _on_browse_templates_clicked(self):
        """Open templates dialog. Pick template -> start zone drawing directly."""
        preset = self._open_templates_dialog()
        if not preset:
            return

        if self._result_section.isVisible():
            # In result state — fill the result prompt
            self._result_prompt_input.blockSignals(True)
            self._result_prompt_input.setPlainText(preset["prompt"])
            self._result_prompt_input.blockSignals(False)
            self._result_prompt_input.moveCursor(QtC.CursorEnd)
            self._result_prompt_input.setFocus()
            self._update_generate_enabled()
            self._adjust_result_prompt_height()
        elif self._zone_selected:
            # Already have a zone — fill the active prompt input
            self._prompt_input.blockSignals(True)
            self._prompt_input.setPlainText(preset["prompt"])
            self._prompt_input.blockSignals(False)
            self._prompt_input.moveCursor(QtC.CursorEnd)
            self._prompt_input.setFocus()
            self._update_generate_enabled()
            self._adjust_prompt_height()
        else:
            # IDLE state — store prompt and go directly to zone drawing.
            # processEvents() lets Qt finish the modal dialog cleanup
            # (focus restoration, cursor state) before we activate the
            # selection tool, so the crosshair cursor applies correctly.
            self._from_template = True
            self._prompt_input.blockSignals(True)
            self._prompt_input.setPlainText(preset["prompt"])
            self._prompt_input.blockSignals(False)
            self._prompt_input.moveCursor(QtC.CursorEnd)
            self._adjust_prompt_height()
            self.set_active_mode()
            QApplication.processEvents()
            self.select_zone_clicked.emit()

    def _on_prompt_changed(self):
        self._enforce_prompt_max_length(self._prompt_input)
        self._update_generate_enabled()

    def _on_result_prompt_changed(self):
        self._enforce_prompt_max_length(self._result_prompt_input)

    @staticmethod
    def _enforce_prompt_max_length(text_edit: QTextEdit) -> None:
        """Truncate the prompt to MAX_PROMPT_CHARS."""
        plain = text_edit.toPlainText()
        if len(plain) <= MAX_PROMPT_CHARS:
            return
        cursor_pos = text_edit.textCursor().position()
        text_edit.blockSignals(True)
        try:
            text_edit.setPlainText(plain[:MAX_PROMPT_CHARS])
            cursor = text_edit.textCursor()
            cursor.setPosition(min(cursor_pos, MAX_PROMPT_CHARS))
            text_edit.setTextCursor(cursor)
        finally:
            text_edit.blockSignals(False)

    def _adjust_prompt_height(self):
        """Auto-expand prompt input to fit content (60px min, 140px max)."""
        doc_height = int(self._prompt_input.document().size().height())
        padding = 12  # 6px top + 6px bottom from QSS padding
        frame = 2 * self._prompt_input.frameWidth()
        target = doc_height + padding + frame
        clamped = max(60, min(140, target))
        self._prompt_input.setFixedHeight(clamped)

    def _adjust_result_prompt_height(self):
        """Auto-expand result prompt input (50px min, 140px max)."""
        doc_height = int(self._result_prompt_input.document().size().height())
        padding = 12  # 6px top + 6px bottom from QSS padding
        frame = 2 * self._result_prompt_input.frameWidth()
        target = doc_height + padding + frame
        clamped = max(50, min(140, target))
        self._result_prompt_input.setFixedHeight(clamped)

    def _on_retry_clicked(self):
        """Retry on same zone with the (possibly edited) prompt from result section."""
        prompt = self._result_prompt_input.toPlainText().strip()
        if not prompt:
            return
        if len(prompt) < 10 or len(prompt.split()) < 2:
            self._show_status_box(
                tr("Please describe what you want to change (at least 10 characters, 2 words)."),
                "warning",
            )
            return
        # Transfer prompt to main input for the generation flow
        self._prompt_input.setPlainText(prompt)
        self._result_section.setVisible(False)
        self._hide_status_box()
        self.retry_clicked.emit(prompt)

    def _on_new_zone_clicked(self):
        """Keep prompt, start new zone selection."""
        prompt = self._result_prompt_input.toPlainText().strip()
        if prompt:
            self._prompt_input.setPlainText(prompt)
        self._result_section.setVisible(False)
        self._layer_saved_label.setVisible(False)
        self._hide_status_box()
        self.new_zone_clicked.emit(prompt)

    def _on_done_clicked(self):
        """Done — back to idle."""
        self.set_idle()
        self.stop_clicked.emit()

    def _on_generate_clicked(self):
        prompt = self.get_prompt()
        if not prompt:
            return
        if len(prompt) < 10 or len(prompt.split()) < 2:
            msg = tr("Please describe what you want to change (at least 10 characters, 2 words).")
            self._show_status_box(msg, "warning")
            return
        self._hide_status_box()
        self.generate_clicked.emit(prompt)

    def _on_contact_us(self, _link=None):
        """Show a dialog with email + Calendly options."""
        from qgis.PyQt.QtWidgets import QApplication, QDialog
        from qgis.PyQt.QtWidgets import QVBoxLayout as _VBox

        calendly_url = "https://calendly.com/barbot-yvann/30min"

        dlg = QDialog(self)
        dlg.setWindowTitle("Contact us")
        dlg.setMinimumWidth(350)
        dlg.setMaximumWidth(450)
        lay = _VBox(dlg)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        msg = QLabel(
            "Bug, question, feature request?\n"
            "We'd love to hear from you!"
        )
        msg.setWordWrap(True)
        lay.addWidget(msg)

        email_label = QLabel(f"<b>{SUPPORT_EMAIL}</b>")
        email_label.setTextInteractionFlags(QtC.TextSelectableByMouse)
        lay.addWidget(email_label)

        copy_btn = QPushButton("Copy email address")
        copy_btn.clicked.connect(
            lambda: (
                QApplication.clipboard().setText(SUPPORT_EMAIL),
                copy_btn.setText("Copied!"),
            )
        )
        lay.addWidget(copy_btn)

        or_label = QLabel("or")
        or_label.setAlignment(QtC.AlignCenter)
        or_label.setStyleSheet("color: palette(text);")
        lay.addWidget(or_label)

        call_btn = QPushButton("Book a video call")
        call_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(calendly_url))
        )
        lay.addWidget(call_btn)

        dlg.exec()

    def _on_show_shortcuts(self, _link=None):
        import sys

        from qgis.PyQt.QtWidgets import QDialog
        from qgis.PyQt.QtWidgets import QVBoxLayout as _VBox

        undo_key = "Cmd+Z" if sys.platform == "darwin" else "Ctrl+Z"
        key_style = (
            "background-color: rgba(128,128,128,0.18);"
            "border: 1px solid rgba(128,128,128,0.35);"
            "border-radius: 3px; padding: 1px 5px; font-family: monospace;"
        )
        k = f"<span style='{key_style}'>{{}}</span>"

        shortcuts_html = (
            "<table cellspacing='4' cellpadding='2'>"
            f"<tr><td colspan='2' style='padding-bottom:2px;'><b>{tr('Editing')}</b></td></tr>"
            f"<tr><td>{k.format('G')}</td><td>{tr('Select a zone')}</td></tr>"
            f"<tr><td>{k.format('Esc')}</td><td>{tr('Cancel selection')}</td></tr>"
            f"<tr><td>{k.format(undo_key)}</td><td>{tr('Undo')}</td></tr>"
            "</table>"
        )

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Shortcuts"))
        lay = _VBox(dlg)
        lay.setContentsMargins(16, 16, 16, 12)
        label = QLabel(shortcuts_html)
        label.setTextFormat(QtC.RichText)
        lay.addWidget(label)
        ok_btn = QPushButton("OK")
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(dlg.accept)
        lay.addWidget(ok_btn, alignment=QtC.AlignCenter)
        dlg.exec()

    def _on_limit_cta_clicked(self):
        if self._limit_cta_url:
            from ..core import telemetry
            telemetry.track("subscribe_link_clicked", {"source": "limit_cta"})
            QDesktopServices.openUrl(QUrl(self._limit_cta_url))

    def _on_activation_limit_cta_clicked(self):
        if self._activation_limit_cta_url:
            from ..core import telemetry
            telemetry.track("subscribe_link_clicked", {"source": "activation_limit_cta"})
            QDesktopServices.openUrl(QUrl(self._activation_limit_cta_url))

    def _on_consent_changed(self):
        """Re-evaluate Generate button when consent checkbox changes."""
        self._update_generate_enabled()

    def _update_generate_enabled(self):
        has_prompt = bool(self.get_prompt())
        consent_ok = has_consent() or self._consent_check.isChecked()
        enabled = self._zone_selected and has_prompt and consent_ok
        self._generate_btn.setEnabled(enabled)
        self._update_generate_style()

    def _hide_limit_cta(self):
        self._limit_cta_btn.setVisible(False)
        self._limit_cta_url = ""

    def _update_generate_style(self):
        if self._generate_btn.isEnabled():
            self._generate_btn.setStyleSheet(_BTN_GREEN)
        else:
            self._generate_btn.setStyleSheet(_BTN_DISABLED)

    def closeEvent(self, event):
        """Cancel generation and disconnect signals on close."""
        self._stop_progress_animation()
        if self._progress_widget.isVisible():
            self.stop_clicked.emit()
        try:
            QgsProject.instance().layersAdded.disconnect(self._update_layer_warning)
            QgsProject.instance().layersRemoved.disconnect(self._update_layer_warning)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)
