from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices, QKeySequence
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QShortcut,
    QStyle,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ..core.activation_manager import (
    get_shared_email,
    get_subscribe_url,
    get_tutorial_url,
    has_consent,
)
from ..core.i18n import tr
from ..core.prompt_presets import get_translated_categories

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

TERRALAB_URL = "https://terra-lab.ai/ai-edit?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dock_branding"
SUPPORT_EMAIL = "yvann.barbot@terra-lab.ai"

# ---------------------------------------------------------------------------
# Reusable QSS style constants (design system)
# ---------------------------------------------------------------------------
_BTN_GREEN = (
    f"QPushButton {{ background-color: {BRAND_GREEN}; padding: 8px 16px; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_GREEN_DISABLED}; }}"
)

_BTN_GREEN_COMPACT = (
    f"QPushButton {{ background-color: {BRAND_GREEN}; padding: 6px 12px; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_GREEN_DISABLED}; }}"
)

_BTN_BLUE = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; padding: 8px 16px; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; }}"
)

_BTN_GRAY = (
    f"QPushButton {{ background-color: {BRAND_GRAY}; padding: 4px 8px; }}"
)

_BTN_DISABLED = (
    f"QPushButton {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT};"
    f" padding: 8px 16px; }}"
)

_PROMPT_INPUT_NORMAL = (
    "QTextEdit { border: 1px solid rgba(128,128,128,0.3);"
    " border-radius: 4px; padding: 6px; color: palette(text); }"
)

_PROMPT_INPUT_READONLY = (
    "QTextEdit { border: 1px solid rgba(128,128,128,0.3);"
    " border-radius: 4px; padding: 6px;"
    " background-color: rgba(128,128,128,0.1); color: #999; }"
)

_INSTRUCTION_BOX = (
    "QLabel {"
    "  background-color: rgba(128, 128, 128, 0.12);"
    "  border: 1px solid rgba(128, 128, 128, 0.25);"
    "  border-radius: 4px;"
    "  padding: 8px;"
    "  font-size: 12px;"
    "  color: palette(text);"
    "}"
)

_SECTION_HEADER = (
    "font-weight: bold; color: palette(text);"
)

_SECTION_HEADER_EXTRA_TOP = (
    "font-weight: bold; color: palette(text); padding-top: 6px;"
)

# QGroupBox style for collapsible sections (matches AI Segmentation refine_group)
_GROUPBOX_COLLAPSIBLE = """
    QGroupBox {
        background-color: transparent;
        border: none;
        border-radius: 0px;
        margin: 0px;
        padding: 0px;
        padding-top: 20px;
    }
    QGroupBox::title {
        subcontrol-origin: padding;
        subcontrol-position: top left;
        padding: 2px 4px;
        background-color: transparent;
        border: none;
    }
"""

# Collapsed height for QGroupBox (just title + arrow)
_KEY_GROUP_COLLAPSED_HEIGHT = 25


def _make_section_header(text: str, extra_top: bool = False) -> QLabel:
    """Create a section header label."""
    label = QLabel(text)
    label.setStyleSheet(_SECTION_HEADER_EXTRA_TOP if extra_top else _SECTION_HEADER)
    return label


class AIEditDockWidget(QDockWidget):
    """Dock widget with dynamic flow matching AI Segmentation pattern."""

    select_zone_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    generate_clicked = pyqtSignal(str)
    activation_attempted = pyqtSignal(str)
    free_signup_requested = pyqtSignal(str)
    change_key_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(tr("AI Edit by TerraLab"), parent)
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setMinimumWidth(260)

        self._setup_title_bar()

        # Main content
        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Cooldown timer for free signup button (prevents spam clicks)
        self._signup_cooldown_remaining = 0
        self._signup_cooldown_timer = QTimer(self)
        self._signup_cooldown_timer.setInterval(1000)
        self._signup_cooldown_timer.timeout.connect(self._on_cooldown_tick)

        # --- Activation section ---
        self._activation_widget = self._build_activation_section()
        layout.addWidget(self._activation_widget)

        # --- Main content section ---
        self._main_widget = QWidget()
        main_layout = QVBoxLayout(self._main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)

        # Section header
        self._edit_header = _make_section_header(tr("Select an Area to Edit with AI:"))
        main_layout.addWidget(self._edit_header)

        # Warning widget (no visible layer) — above start button
        self._warning_widget = self._build_warning_widget()
        self._warning_widget.setVisible(False)
        main_layout.addWidget(self._warning_widget)

        # Start button
        self._start_btn = QPushButton(tr("Start AI Edit"))
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_btn.setStyleSheet(_BTN_GREEN)
        self._start_btn.clicked.connect(self._on_start_clicked)
        main_layout.addWidget(self._start_btn)

        # Instruction info box (shown during drawing)
        self._instruction_box = QLabel()
        self._instruction_box.setWordWrap(True)
        self._instruction_box.setStyleSheet(_INSTRUCTION_BOX)
        self._instruction_box.setVisible(False)
        main_layout.addWidget(self._instruction_box)

        # --- Prompt section (hidden until zone selected) ---
        self._prompt_section = QWidget()
        prompt_layout = QVBoxLayout(self._prompt_section)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        prompt_layout.setSpacing(4)

        # Section header: PROMPT
        self._prompt_header = _make_section_header(tr("What should AI change?"), extra_top=True)
        prompt_layout.addWidget(self._prompt_header)

        self._prompt_input = QTextEdit()
        self._prompt_input.setPlaceholderText(tr("Type your prompt or use a template below..."))
        self._prompt_input.setMinimumHeight(60)
        self._prompt_input.setMaximumHeight(60)
        self._prompt_input.setStyleSheet(_PROMPT_INPUT_NORMAL)
        self._prompt_input.textChanged.connect(self._on_prompt_changed)
        self._prompt_input.document().documentLayout().documentSizeChanged.connect(
            self._adjust_prompt_height
        )
        prompt_layout.addWidget(self._prompt_input)

        # Templates button (flat dropdown trigger, below prompt input)
        self._templates_btn = QPushButton(tr("\u25bc Prompt Templates"))
        self._templates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._templates_btn.setStyleSheet(
            "QPushButton { text-align: left; color: palette(text); "
            "font-size: 11px; border: 1px solid rgba(128,128,128,0.25); "
            "border-radius: 4px; padding: 4px 8px; "
            "background-color: rgba(128,128,128,0.08); }"
            "QPushButton:hover { background-color: rgba(128,128,128,0.15); }"
            "QPushButton::menu-indicator { image: none; }"
        )
        self._build_templates_menu()
        prompt_layout.addWidget(self._templates_btn)

        self._prompt_section.setVisible(False)
        main_layout.addWidget(self._prompt_section)

        # Consent checkbox (shown only until first generation)
        self._consent_check = QCheckBox()
        self._consent_check.setStyleSheet("font-size: 11px; color: palette(text);")
        self._consent_check.setText("")  # text set via label below
        consent_layout = QHBoxLayout()
        consent_layout.setContentsMargins(0, 0, 0, 0)
        consent_layout.setSpacing(4)
        consent_layout.addWidget(self._consent_check, 0)
        consent_text = QLabel(
            'I agree to the <a href="https://terra-lab.ai/terms-of-sale?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=consent_terms" '
            f'style="color: {BRAND_BLUE};">Terms</a> and '
            '<a href="https://terra-lab.ai/privacy-policy?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=consent_privacy" '
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
        self._generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._generate_btn.setEnabled(False)
        self._update_generate_style()
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        self._generate_btn.setVisible(False)
        main_layout.addWidget(self._generate_btn)

        # Stop button
        self._stop_btn = QPushButton(tr("Stop"))
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
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
            self._status_icon, 0, Qt.AlignmentFlag.AlignTop
        )
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setOpenExternalLinks(True)
        self._status_label.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        status_box_layout.addWidget(self._status_label, 1)
        main_layout.addWidget(self._status_widget)

        # CTA button displayed for quota exhaustion
        self._limit_cta_btn = QPushButton(tr("Go to AI Edit page"))
        self._limit_cta_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._limit_cta_btn.setStyleSheet(_BTN_BLUE)
        self._limit_cta_btn.clicked.connect(self._on_limit_cta_clicked)
        self._limit_cta_btn.setVisible(False)
        main_layout.addWidget(self._limit_cta_btn)
        self._limit_cta_url = ""

        # Trial exhausted info box
        self._trial_info_box = QFrame()
        self._trial_info_box.setStyleSheet(
            "QFrame { background: rgba(25,118,210,0.08); "
            "border: 1px solid rgba(25,118,210,0.2); "
            "border-radius: 4px; padding: 10px; }"
        )
        trial_layout = QVBoxLayout(self._trial_info_box)
        trial_layout.setContentsMargins(10, 8, 10, 8)
        trial_layout.setSpacing(4)
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

        # Footer section
        footer_container = QVBoxLayout()
        footer_container.setContentsMargins(0, 0, 0, 0)
        footer_container.setSpacing(2)

        # Credits line (right-aligned, above links)
        self._credits_label = QLabel()
        self._credits_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._credits_label.setStyleSheet(
            "font-size: 10px; color: palette(text); background: transparent; border: none;"
        )
        self._credits_label.setVisible(False)
        footer_container.addWidget(self._credits_label)

        # Links line (right-aligned)
        footer_links = QWidget()
        footer_layout = QHBoxLayout(footer_links)
        footer_layout.setContentsMargins(0, 0, 0, 4)
        footer_layout.setSpacing(16)
        footer_layout.addStretch()

        self._change_key_link = QLabel(
            f'<a href="#" style="color: {BRAND_BLUE};">{tr("Change key")}</a>'
        )
        self._change_key_link.setStyleSheet("font-size: 13px;")
        self._change_key_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._change_key_link.linkActivated.connect(self._on_change_key)
        self._change_key_link.setVisible(False)
        footer_layout.addWidget(self._change_key_link)

        for text, url, handler in [
            (tr("Tutorial"), get_tutorial_url(), None),
            (tr("Contact us"), "#", self._on_contact_us),
        ]:
            link = QLabel(f'<a href="{url}" style="color: {BRAND_BLUE};">{text}</a>')
            link.setStyleSheet("font-size: 13px;")
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            if handler:
                link.linkActivated.connect(handler)
            else:
                link.setOpenExternalLinks(True)
            footer_layout.addWidget(link)

        footer_container.addWidget(footer_links)
        layout.addLayout(footer_container)

        # Wrap in scroll area (matches AI Segmentation)
        scroll_area = QScrollArea()
        scroll_area.setWidget(main_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        self.setWidget(scroll_area)

        # State
        self._active = False
        self._zone_selected = False
        self._activated = False

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
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        title_outer.addWidget(separator)

        self.setTitleBarWidget(title_widget)

    def _build_activation_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # --- Email signup section (hidden after successful signup) ---
        self._signup_section = QWidget()
        signup_layout = QVBoxLayout(self._signup_section)
        signup_layout.setContentsMargins(0, 0, 0, 0)
        signup_layout.setSpacing(8)

        title = QLabel(tr("Try AI Edit for free"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-weight: bold; font-size: 13px; color: palette(text);")
        signup_layout.addWidget(title)

        subtitle = QLabel(tr("No credit card required."))
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 11px; color: palette(text);")
        signup_layout.addWidget(subtitle)

        self._email_input = QLineEdit()
        self._email_input.setPlaceholderText(tr("your@email.com"))
        self._email_input.setMinimumHeight(32)
        self._email_input.setStyleSheet("color: palette(text);")
        shared_email = get_shared_email()
        if shared_email:
            self._email_input.setText(shared_email)
        signup_layout.addWidget(self._email_input)

        self._free_submit_btn = QPushButton(tr("Get {credits} free AI edits").replace("{credits}", "5"))
        self._free_submit_btn.setMinimumHeight(36)
        self._free_submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._free_submit_btn.setStyleSheet(_BTN_GREEN)
        self._free_submit_btn.clicked.connect(self._on_free_signup_clicked)
        self._email_input.returnPressed.connect(self._on_free_signup_clicked)
        signup_layout.addWidget(self._free_submit_btn)

        # Flow info (small text below button)
        flow_info = QLabel(
            tr("Enter your email, check your inbox, get your free key from the dashboard, paste it here.")
        )
        flow_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        flow_info.setWordWrap(True)
        flow_info.setStyleSheet("font-size: 10px; color: palette(text); margin-top: 2px;")
        signup_layout.addWidget(flow_info)

        layout.addWidget(self._signup_section)

        # Activation message (errors / success) - right below signup section
        self._activation_message = QLabel("")
        self._activation_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._activation_message.setWordWrap(True)
        self._activation_message.setStyleSheet("font-size: 11px;")
        self._activation_message.setVisible(False)
        layout.addWidget(self._activation_message)

        # CTA button displayed on activation flow when usage limit is reached
        self._activation_limit_cta_btn = QPushButton(tr("Go to AI Edit page"))
        self._activation_limit_cta_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._activation_limit_cta_btn.setStyleSheet(_BTN_BLUE)
        self._activation_limit_cta_btn.clicked.connect(self._on_activation_limit_cta_clicked)
        self._activation_limit_cta_btn.setVisible(False)
        layout.addWidget(self._activation_limit_cta_btn)
        self._activation_limit_cta_url = ""

        # --- Collapsible key section (QGroupBox pattern from AI Segmentation) ---
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        self._key_group = QGroupBox("\u25b6 " + tr("Already have a key? Paste it here"))
        self._key_group.setCheckable(False)
        self._key_group.setCursor(Qt.CursorShape.PointingHandCursor)
        self._key_group.setStyleSheet(_GROUPBOX_COLLAPSIBLE)
        self._key_group.mousePressEvent = self._on_key_group_clicked

        key_group_layout = QVBoxLayout(self._key_group)
        key_group_layout.setContentsMargins(0, 4, 0, 0)
        key_group_layout.setSpacing(6)

        self._key_content = QWidget()
        self._key_content.setVisible(False)
        key_layout = QVBoxLayout(self._key_content)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)

        code_row = QHBoxLayout()
        code_row.setSpacing(6)
        self._code_input = QLineEdit()
        self._code_input.setPlaceholderText("tl_...")
        self._code_input.setMinimumHeight(28)
        self._code_input.returnPressed.connect(self._on_unlock_clicked)
        code_row.addWidget(self._code_input)

        unlock_btn = QPushButton(tr("Activate"))
        unlock_btn.setMinimumHeight(28)
        unlock_btn.setMinimumWidth(70)
        unlock_btn.setStyleSheet(_BTN_BLUE)
        unlock_btn.clicked.connect(self._on_unlock_clicked)
        code_row.addWidget(unlock_btn)
        key_layout.addLayout(code_row)

        key_group_layout.addWidget(self._key_content)
        layout.addWidget(self._key_group)

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
        warning_layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        self._warning_text = QLabel(tr("No visible layer. Add imagery to your project."))
        self._warning_text.setWordWrap(True)
        warning_layout.addWidget(self._warning_text, 1)

        return widget

    def _build_templates_menu(self, categories=None):
        """Build flat popup menu with category headers and indented items."""
        if categories is None:
            categories = get_translated_categories()
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: palette(window); }"
            "QMenu::item { padding: 3px 12px 3px 20px; color: palette(text); }"
            "QMenu::item:selected { background: rgba(128,128,128,0.15); color: palette(text); }"
            "QMenu::separator { margin: 4px 8px; }"
        )
        header_html = {
            "remove": '<span style="color:#e06060;">\u2715</span> Remove',
            "add": '<span style="color:#60c060;">+</span> Add',
        }
        for i, category in enumerate(categories):
            if i > 0:
                menu.addSeparator()
            html = header_html.get(category["key"], category["label"])
            header_label = QLabel(html)
            header_label.setTextFormat(Qt.TextFormat.RichText)
            header_label.setStyleSheet(
                "padding: 6px 12px 2px 8px; font-size: 10px;"
                " color: palette(text); font-weight: bold;"
            )
            header_action = QWidgetAction(menu)
            header_action.setDefaultWidget(header_label)
            header_action.setEnabled(False)
            menu.addAction(header_action)
            for preset in category["presets"]:
                action = menu.addAction("  \u2022 " + preset["label"])
                action.triggered.connect(
                    lambda checked, p=preset: self._on_preset_clicked(p)
                )
        self._templates_btn.setMenu(menu)

    def update_presets(self, categories):
        """Rebuild the templates menu with new categories (e.g. from server)."""
        self._build_templates_menu(categories)

    # --- Public methods ---

    def set_activated(self, activated: bool):
        self._activated = activated
        self._activation_widget.setVisible(not activated)
        self._main_widget.setVisible(activated)
        self._change_key_link.setVisible(activated)
        if activated:
            self.hide_trial_info()
            self._update_layer_warning()
        else:
            # Reset to full signup view (for new users)
            self._signup_section.setVisible(True)
            self._key_group.setTitle("\u25b6 " + tr("Already have a key? Paste it here"))
            self._key_group.setCursor(Qt.CursorShape.PointingHandCursor)
            self._key_group.setStyleSheet(_GROUPBOX_COLLAPSIBLE)
            self._key_group.mousePressEvent = self._on_key_group_clicked
            self._key_content.setVisible(False)
            self._activation_message.setVisible(False)
            self.hide_activation_limit_cta()

    def show_change_key_mode(self):
        """Show only the key input, no signup flow. For users changing their key."""
        self._activated = False
        self._activation_widget.setVisible(True)
        self._main_widget.setVisible(False)
        self._change_key_link.setVisible(False)
        # Hide signup, show only key input
        self._signup_section.setVisible(False)
        self._key_group.setTitle(tr("Enter your new activation key:"))
        self._key_group.setCursor(Qt.CursorShape.ArrowCursor)
        self._key_group.mousePressEvent = lambda e: None  # Disable toggle
        self._key_content.setVisible(True)
        self._activation_message.setVisible(False)
        self.hide_activation_limit_cta()
        self._code_input.clear()
        self._code_input.setFocus()

    def reset_free_signup_button(self, start_cooldown: bool = True):
        """Start a 60s cooldown before re-enabling the free signup button.

        Prevents users from spamming the magic link button, which would
        trigger the per-user email interval (60s) or our IP rate limit.
        """
        if start_cooldown:
            self._signup_cooldown_remaining = 60
            self._free_submit_btn.setEnabled(False)
            self._free_submit_btn.setText(tr("Resend in {seconds}s").format(seconds=60))
            self._signup_cooldown_timer.start()
        else:
            self._free_submit_btn.setEnabled(True)
            self._free_submit_btn.setText(tr("Get {credits} free AI edits").replace("{credits}", "5"))

    def _on_cooldown_tick(self):
        """Tick the cooldown timer, re-enable button when done."""
        self._signup_cooldown_remaining -= 1
        if self._signup_cooldown_remaining <= 0:
            self._signup_cooldown_timer.stop()
            self._free_submit_btn.setEnabled(True)
            self._free_submit_btn.setText(tr("Get {credits} free AI edits").replace("{credits}", "5"))
        else:
            self._free_submit_btn.setText(
                tr("Resend in {seconds}s").format(seconds=self._signup_cooldown_remaining)
            )

    def show_post_signup_state(self):
        """After successful email send, hide signup section and show key input directly."""
        self._signup_section.setVisible(False)
        # Replace the dropdown toggle with a plain label
        self._key_group.setTitle(tr("Paste your key from terra-lab.ai/dashboard:"))
        self._key_group.setCursor(Qt.CursorShape.ArrowCursor)
        self._key_group.mousePressEvent = lambda e: None  # Disable toggle
        # Show key input directly (not as dropdown)
        self._key_content.setVisible(True)
        self._code_input.setFocus()

    def _on_key_group_clicked(self, _event):
        """Toggle the collapsible key input section."""
        visible = not self._key_content.isVisible()
        self._key_content.setVisible(visible)
        prefix = "\u25bc " if visible else "\u25b6 "
        self._key_group.setTitle(prefix + tr("Already have a key? Paste it here"))

    def hide_consent(self):
        """Hide the consent checkbox after first generation."""
        self._consent_widget.setVisible(False)

    def set_activation_message(self, text: str, is_error: bool = False):
        # Use brighter variants for dark theme readability
        self.hide_activation_limit_cta()
        color = "#ef5350" if is_error else "#66bb6a"
        self._activation_message.setStyleSheet(f"font-size: 11px; color: {color};")
        self._activation_message.setText(text)
        self._activation_message.setVisible(True)

    def show_activation_limit_cta(self, subscribe_url: str):
        self._activation_limit_cta_url = subscribe_url
        self._activation_limit_cta_btn.setText(tr("Go to AI Edit page"))
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
        if used is not None and limit is not None:
            remaining = max(0, limit - used)
            if is_free_tier:
                self._credits_label.setText(f"{remaining} / {limit} free credits remaining")
            else:
                self._credits_label.setText(f"{remaining} / {limit} credits remaining")
            self._credits_label.setVisible(True)
        else:
            self._credits_label.setVisible(False)

    def set_active_mode(self):
        """Enter active mode: drawing rectangle."""
        self._active = True
        self._edit_header.setVisible(False)
        self._start_btn.setVisible(False)
        self._warning_widget.setVisible(False)
        self._instruction_box.setVisible(True)
        self._hide_status_box()

    def set_zone_selected(self):
        """Zone drawn: show prompt flow (no Start button)."""
        self._zone_selected = True
        self._edit_header.setVisible(False)
        self._start_btn.setVisible(False)
        self._instruction_box.setVisible(False)
        self._hide_status_box()
        self._prompt_section.setVisible(True)
        self._consent_widget.setVisible(not has_consent())
        self._generate_btn.setVisible(True)
        self._stop_btn.setVisible(True)
        self._start_shortcut.setEnabled(False)
        self._stop_shortcut.setEnabled(True)
        self._update_generate_enabled()
        # Deferred focus: survives Qt layout recalculations from visibility changes
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
        self._start_btn.setVisible(True)
        self._stop_btn.setVisible(False)
        self._instruction_box.setVisible(False)
        self._prompt_section.setVisible(False)
        self._consent_widget.setVisible(False)
        self._generate_btn.setVisible(False)
        self._progress_widget.setVisible(False)
        self._start_shortcut.setEnabled(True)
        self._stop_shortcut.setEnabled(True)
        self._prompt_input.clear()
        self._prompt_input.setMaximumHeight(60)
        self._prompt_input.setReadOnly(False)
        self._prompt_input.setStyleSheet(_PROMPT_INPUT_NORMAL)
        self._templates_btn.setEnabled(True)
        self._update_layer_warning()

    def set_generating(self, generating: bool):
        """Toggle generation state -- keep prompt visible but grayed out."""
        self._progress_widget.setVisible(generating)
        self._start_btn.setVisible(False)
        self._warning_widget.setVisible(False)

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
            self._stop_shortcut.setEnabled(False)
            self._progress_label.setText(tr("Preparing..."))
        else:
            # Restore prompt interaction
            self._prompt_input.setReadOnly(False)
            self._prompt_input.setStyleSheet(_PROMPT_INPUT_NORMAL)
            self._templates_btn.setEnabled(True)
            self._consent_widget.setVisible(not has_consent() and self._zone_selected)
            self._generate_btn.setVisible(self._zone_selected)
            self._stop_btn.setVisible(self._zone_selected)
            self._stop_shortcut.setEnabled(True)
            self._prompt_section.setVisible(self._zone_selected)

        self._start_shortcut.setEnabled(not generating)

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
                "QWidget { background-color: rgba(255, 152, 0, 0.2); "
                "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #ffa726; }",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
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
        """Show success message and reset to idle state."""
        self._stop_progress_animation()
        self._progress_bar.setValue(100)
        self._progress_widget.setVisible(False)
        self._show_status_box(
            f'Generation complete! Layer "{layer_name}" added.', "success"
        )
        self._active = False
        self._zone_selected = False
        self._edit_header.setVisible(True)
        self._start_btn.setVisible(True)
        self._start_btn.setEnabled(True)
        self._stop_btn.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        self._start_shortcut.setEnabled(True)
        self._stop_shortcut.setEnabled(True)
        self._prompt_input.clear()
        self._prompt_input.setMaximumHeight(60)
        self._prompt_input.setReadOnly(False)
        self._prompt_input.setStyleSheet(_PROMPT_INPUT_NORMAL)
        self._templates_btn.setEnabled(True)
        self._update_layer_warning()

    def show_trial_exhausted_info(self, message: str, subscribe_url: str, promo_text: str = ""):
        self._hide_limit_cta()
        parts = [message]
        if promo_text:
            parts.append(promo_text)
        self._trial_info_text.setText("\n\n".join(parts))
        self._trial_info_link.setText(
            f'<a href="{subscribe_url}" style="color: {BRAND_BLUE}; '
            f'font-weight: bold;">{tr("Subscribe at terra-lab.ai")}</a>'
        )
        self._trial_info_box.setVisible(True)
        self._start_btn.setVisible(False)
        self._edit_header.setVisible(False)
        self._hide_status_box()

    def show_usage_limit_info(self, message: str, subscribe_url: str):
        self._show_status_box(message, "error")
        self._trial_info_box.setVisible(False)
        self._limit_cta_url = subscribe_url
        self._limit_cta_btn.setVisible(True)
        self._start_btn.setVisible(False)
        self._edit_header.setVisible(False)

    def hide_trial_info(self):
        self._trial_info_box.setVisible(False)
        self._hide_status_box()
        self._hide_limit_cta()
        self._start_btn.setVisible(True)
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
        self._start_btn.setEnabled(has_layers)

    def _on_start_clicked(self):
        """Start selection -- enter active mode."""
        if self._active or not self._start_btn.isVisible():
            return
        if not self._start_btn.isEnabled():
            return
        self.set_active_mode()
        self.select_zone_clicked.emit()

    def _on_stop_clicked(self):
        """Stop -- reset to idle."""
        if not self._active:
            return
        self.set_idle()
        self.stop_clicked.emit()

    def _on_get_key_clicked(self):
        QDesktopServices.openUrl(QUrl(get_subscribe_url()))

    def _on_free_signup_clicked(self):
        """Handle free tier email signup."""
        email = self._email_input.text().strip()
        if not email or "@" not in email:
            self.set_activation_message(tr("Please enter a valid email address."), is_error=True)
            return

        # Disable button during send
        self._free_submit_btn.setEnabled(False)
        self._free_submit_btn.setText(tr("Sending..."))

        self.free_signup_requested.emit(email)

    def _on_unlock_clicked(self):
        code = self._code_input.text().strip()
        if not code:
            self.set_activation_message(tr("Enter your code"), is_error=True)
            return
        self.activation_attempted.emit(code)

    def _on_preset_clicked(self, preset: dict):
        self._prompt_input.setPlainText(preset["prompt"])
        self._prompt_input.setFocus()

    def _on_prompt_changed(self):
        self._update_generate_enabled()

    def _adjust_prompt_height(self):
        """Auto-expand prompt input to fit content (60px min, 140px max)."""
        doc_height = int(self._prompt_input.document().size().height())
        # Add padding (top + bottom from QSS: 6px each) + frame margins
        margins = self._prompt_input.contentsMargins()
        target = doc_height + margins.top() + margins.bottom() + 2 * self._prompt_input.frameWidth()
        clamped = max(60, min(140, target))
        self._prompt_input.setMaximumHeight(clamped)

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

    def _on_change_key(self, _link=None):
        self.change_key_clicked.emit()

    def _on_contact_us(self, _link=None):
        """Show a dialog with email + Calendly options."""
        from qgis.PyQt.QtWidgets import QApplication, QDialog, QVBoxLayout as _VBox

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

        email_label = QLabel(f'<b>{SUPPORT_EMAIL}</b>')
        email_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
        or_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        or_label.setStyleSheet("color: palette(text);")
        lay.addWidget(or_label)

        call_btn = QPushButton("Book a video call")
        call_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(calendly_url))
        )
        lay.addWidget(call_btn)

        dlg.exec()

    def _on_limit_cta_clicked(self):
        if self._limit_cta_url:
            QDesktopServices.openUrl(QUrl(self._limit_cta_url))

    def _on_activation_limit_cta_clicked(self):
        if self._activation_limit_cta_url:
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
        self._signup_cooldown_timer.stop()
        self._stop_progress_animation()
        if self._progress_widget.isVisible():
            self.stop_clicked.emit()
        try:
            QgsProject.instance().layersAdded.disconnect(self._update_layer_warning)
            QgsProject.instance().layersRemoved.disconnect(self._update_layer_warning)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)
