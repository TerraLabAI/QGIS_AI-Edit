from qgis.PyQt.QtCore import pyqtSignal, Qt, QUrl, QTimer
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTextEdit,
    QLineEdit,
    QProgressBar,
    QFrame,
    QScrollArea,
    QShortcut,
    QMenu,
    QStyle,
    QToolButton,
    QWidgetAction,
    QCheckBox,
)
from qgis.PyQt.QtGui import QDesktopServices, QKeySequence
from qgis.core import QgsProject

from ..core.activation_manager import get_subscribe_url, get_dashboard_url, get_shared_email, get_tutorial_url, has_consent, tr
from ..core.prompt_presets import get_translated_categories

# Brand colors (matching AI Segmentation)
BRAND_GREEN = "#2e7d32"
BRAND_GREEN_DISABLED = "#c8e6c9"
BRAND_BLUE = "#1976d2"
BRAND_BLUE_HOVER = "#1565c0"
BRAND_GREEN_HOVER = "#1b5e20"
BRAND_RED = "#d32f2f"
BRAND_DISABLED = "#b0bec5"
BRAND_GRAY = "#757575"

TERRALAB_URL = "https://terra-lab.ai"


class AIEditDockWidget(QDockWidget):
    """Dock widget with dynamic flow matching AI Segmentation pattern."""

    select_zone_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    generate_clicked = pyqtSignal(str)
    activation_attempted = pyqtSignal(str)
    free_signup_requested = pyqtSignal(str)
    change_key_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(tr("dock_title"), parent)
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        self._setup_title_bar()

        # Main content
        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(6)

        # --- Activation section ---
        self._activation_widget = self._build_activation_section()
        layout.addWidget(self._activation_widget)

        # --- Main content section ---
        self._main_widget = QWidget()
        main_layout = QVBoxLayout(self._main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)

        # Bold label (always visible)
        self._bold_label = QLabel(tr("select_area"))
        self._bold_label.setStyleSheet("font-weight: bold; color: palette(text);")
        main_layout.addWidget(self._bold_label)

        # Warning widget (no visible layer)
        self._warning_widget = self._build_warning_widget()
        self._warning_widget.setVisible(False)
        main_layout.addWidget(self._warning_widget)

        # Start button (green, matches AI Segmentation)
        self._start_btn = QPushButton(tr("start_ai_edit"))
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BRAND_GREEN}; padding: 8px 16px; }}"
            f"QPushButton:disabled {{ background-color: {BRAND_GREEN_DISABLED}; }}"
        )
        self._start_btn.clicked.connect(self._on_start_clicked)
        main_layout.addWidget(self._start_btn)

        # Instruction info box (shown during drawing)
        self._instruction_box = QLabel(tr("click_drag"))
        self._instruction_box.setWordWrap(True)
        self._instruction_box.setStyleSheet(
            "QLabel {"
            "  background-color: rgba(128, 128, 128, 0.12);"
            "  border: 1px solid rgba(128, 128, 128, 0.25);"
            "  border-radius: 4px;"
            "  padding: 8px;"
            "  font-size: 12px;"
            "  color: palette(text);"
            "}"
        )
        self._instruction_box.setVisible(False)
        main_layout.addWidget(self._instruction_box)

        # --- Prompt section (hidden until zone selected) ---
        self._prompt_section = QWidget()
        prompt_layout = QVBoxLayout(self._prompt_section)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        prompt_layout.setSpacing(4)

        self._prompt_label = QLabel(tr("what_change"))
        self._prompt_label.setStyleSheet("font-weight: bold; color: palette(text);")
        prompt_layout.addWidget(self._prompt_label)

        self._prompt_input = QTextEdit()
        self._prompt_input.setPlaceholderText(tr("prompt_placeholder"))
        self._prompt_input.setFixedHeight(60)
        self._prompt_input.setStyleSheet(
            "border: 1px solid rgba(128,128,128,0.3); border-radius: 4px; padding: 6px;"
        )
        self._prompt_input.textChanged.connect(self._on_prompt_changed)
        prompt_layout.addWidget(self._prompt_input)

        # Templates button (flat dropdown trigger, below prompt input)
        self._templates_btn = QPushButton(tr("prompt_templates"))
        self._templates_btn.setCursor(Qt.PointingHandCursor)
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
            'I agree to the <a href="https://terra-lab.ai/terms-of-sale" '
            f'style="color: {BRAND_BLUE};">Terms</a> and '
            '<a href="https://terra-lab.ai/privacy-policy" '
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
        self._generate_btn = QPushButton(tr("generate"))
        self._generate_btn.setCursor(Qt.PointingHandCursor)
        self._generate_btn.setEnabled(False)
        self._update_generate_style()
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        self._generate_btn.setVisible(False)
        main_layout.addWidget(self._generate_btn)

        # Stop button (small gray, below Generate)
        self._stop_btn = QPushButton(tr("stop"))
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BRAND_GRAY}; padding: 4px 8px; }}"
        )
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.setVisible(False)
        main_layout.addWidget(self._stop_btn)

        # Progress section
        self._progress_widget = QWidget()
        progress_layout = QVBoxLayout(self._progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(4)
        self._progress_label = QLabel(tr("preparing"))
        self._progress_label.setStyleSheet("font-size: 11px; color: palette(text);")
        progress_layout.addWidget(self._progress_label)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        progress_layout.addWidget(self._progress_bar)
        self._progress_widget.setVisible(False)
        main_layout.addWidget(self._progress_widget)

        # Status message
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setOpenExternalLinks(True)
        self._status_label.setStyleSheet("font-size: 11px;")
        main_layout.addWidget(self._status_label)

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

        # Footer (pinned to bottom, right-aligned)
        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 4, 0, 4)
        footer_layout.setSpacing(16)
        footer_layout.addStretch()

        self._change_key_link = QLabel(
            f'<a href="#" style="color: {BRAND_BLUE};">{tr("change_key")}</a>'
        )
        self._change_key_link.setStyleSheet("font-size: 13px;")
        self._change_key_link.setCursor(Qt.PointingHandCursor)
        self._change_key_link.linkActivated.connect(self._on_change_key)
        self._change_key_link.setVisible(False)
        footer_layout.addWidget(self._change_key_link)

        for text, url, handler in [
            (tr("report_bug"), "#", self._on_report_bug),
            (tr("tutorial"), get_tutorial_url(), None),
            ("AI Edit demo", "https://terra-lab.ai/ai-edit", None),
        ]:
            link = QLabel(f'<a href="{url}" style="color: {BRAND_BLUE};">{text}</a>')
            link.setStyleSheet("font-size: 13px;")
            link.setCursor(Qt.PointingHandCursor)
            if handler:
                link.linkActivated.connect(handler)
            else:
                link.setOpenExternalLinks(True)
            footer_layout.addWidget(link)

        layout.addWidget(footer)

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

        # Keyboard shortcuts (G to start, Esc to stop — no UI display)
        self._start_shortcut = QShortcut(QKeySequence("G"), self)
        self._start_shortcut.activated.connect(self._on_start_clicked)
        self._stop_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._stop_shortcut.activated.connect(self._on_stop_clicked)

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

        title = QLabel(tr("free_title"))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-weight: bold; font-size: 13px; color: palette(text);")
        signup_layout.addWidget(title)

        subtitle = QLabel(tr("free_subtitle"))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 11px; color: palette(text);")
        signup_layout.addWidget(subtitle)

        self._email_input = QLineEdit()
        self._email_input.setPlaceholderText(tr("free_email_placeholder"))
        self._email_input.setMinimumHeight(32)
        self._email_input.setStyleSheet("color: palette(text);")
        shared_email = get_shared_email()
        if shared_email:
            self._email_input.setText(shared_email)
        signup_layout.addWidget(self._email_input)

        self._free_submit_btn = QPushButton(tr("free_submit").replace("{credits}", "10"))
        self._free_submit_btn.setMinimumHeight(36)
        self._free_submit_btn.setCursor(Qt.PointingHandCursor)
        self._free_submit_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BRAND_GREEN}; color: white; "
            f"font-weight: bold; border-radius: 4px; padding: 8px 16px; }}"
            f"QPushButton:hover {{ background-color: {BRAND_GREEN_HOVER}; }}"
        )
        self._free_submit_btn.clicked.connect(self._on_free_signup_clicked)
        self._email_input.returnPressed.connect(self._on_free_signup_clicked)
        signup_layout.addWidget(self._free_submit_btn)

        # Flow info (small text below button)
        flow_info = QLabel(tr("free_flow_info"))
        flow_info.setAlignment(Qt.AlignCenter)
        flow_info.setWordWrap(True)
        flow_info.setStyleSheet("font-size: 10px; color: palette(text); margin-top: 2px;")
        signup_layout.addWidget(flow_info)

        layout.addWidget(self._signup_section)

        # Activation message (errors / success) - right below signup section
        self._activation_message = QLabel("")
        self._activation_message.setAlignment(Qt.AlignCenter)
        self._activation_message.setWordWrap(True)
        self._activation_message.setStyleSheet("font-size: 11px;")
        self._activation_message.setVisible(False)
        layout.addWidget(self._activation_message)

        # --- Collapsible key section (dropdown) ---
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        self._key_toggle = QToolButton()
        self._key_toggle.setText(tr("free_or_paste_key"))
        self._key_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._key_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._key_toggle.setStyleSheet(
            "QToolButton { border: none; font-size: 11px; color: palette(text); }"
        )
        self._key_toggle.setCursor(Qt.PointingHandCursor)
        self._key_toggle.clicked.connect(self._toggle_key_section)
        layout.addWidget(self._key_toggle)

        self._key_content = QWidget()
        self._key_content.setVisible(False)
        key_layout = QVBoxLayout(self._key_content)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)

        code_row = QHBoxLayout()
        code_row.setSpacing(6)
        self._code_input = QLineEdit()
        self._code_input.setPlaceholderText("tl_pro_... / tl_free_...")
        self._code_input.setMinimumHeight(28)
        self._code_input.returnPressed.connect(self._on_unlock_clicked)
        code_row.addWidget(self._code_input)

        unlock_btn = QPushButton(tr("activate"))
        unlock_btn.setMinimumHeight(28)
        unlock_btn.setMinimumWidth(70)
        unlock_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BRAND_BLUE}; color: white; "
            f"font-weight: bold; border-radius: 4px; }}"
            f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; }}"
        )
        unlock_btn.clicked.connect(self._on_unlock_clicked)
        code_row.addWidget(unlock_btn)
        key_layout.addLayout(code_row)

        layout.addWidget(self._key_content)

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
        warning_layout.addWidget(icon_label, 0, Qt.AlignTop)

        self._warning_text = QLabel(tr("no_visible_layer"))
        self._warning_text.setWordWrap(True)
        warning_layout.addWidget(self._warning_text, 1)

        return widget

    def _build_templates_menu(self, categories=None):
        """Build flat popup menu with category headers and indented items."""
        if categories is None:
            categories = get_translated_categories()
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu::item { padding: 3px 12px 3px 20px;"
            "  color: rgba(255,255,255,0.72); }"
            "QMenu::item:selected { background: rgba(255,255,255,0.08); }"
            "QMenu::separator { margin: 4px 8px; }"
        )
        header_html = {
            "remove": (
                '<span style="color:#e06060;">\u2715</span>'
                ' <span style="color:rgba(255,255,255,0.50);">Remove</span>'
            ),
            "add": (
                '<span style="color:#60c060;">+</span>'
                ' <span style="color:rgba(255,255,255,0.50);">Add</span>'
            ),
        }
        for i, category in enumerate(categories):
            if i > 0:
                menu.addSeparator()
            html = header_html.get(category["key"], category["label"])
            header_label = QLabel(html)
            header_label.setTextFormat(Qt.RichText)
            header_label.setStyleSheet("padding: 6px 12px 2px 8px;")
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
        if not activated:
            # Reset to full signup view (for new users)
            self._signup_section.setVisible(True)
            self._key_toggle.setEnabled(True)
            self._key_toggle.setText(tr("free_or_paste_key"))
            self._key_toggle.setArrowType(Qt.ArrowType.RightArrow)
            self._key_toggle.setStyleSheet(
                "QToolButton { border: none; font-size: 11px; color: palette(text); }"
            )
            self._key_content.setVisible(False)
            self._activation_message.setVisible(False)

    def show_change_key_mode(self):
        """Show only the key input, no signup flow. For users changing their key."""
        self._activated = False
        self._activation_widget.setVisible(True)
        self._main_widget.setVisible(False)
        self._change_key_link.setVisible(False)
        # Hide signup, show only key input
        self._signup_section.setVisible(False)
        self._key_toggle.setArrowType(Qt.ArrowType.NoArrow)
        self._key_toggle.setText(tr("change_key_paste"))
        self._key_toggle.setEnabled(False)
        self._key_toggle.setStyleSheet(
            "QToolButton { border: none; font-size: 11px; color: palette(text); font-weight: bold; }"
        )
        self._key_content.setVisible(True)
        self._activation_message.setVisible(False)
        self._code_input.clear()
        self._code_input.setFocus()

    def reset_free_signup_button(self):
        """Re-enable the free signup button after result."""
        self._free_submit_btn.setEnabled(True)
        self._free_submit_btn.setText(tr("free_submit").replace("{credits}", "10"))

    def show_post_signup_state(self):
        """After successful email send, hide signup section and show key input directly."""
        self._signup_section.setVisible(False)
        # Replace the dropdown toggle with a plain label
        self._key_toggle.setArrowType(Qt.ArrowType.NoArrow)
        self._key_toggle.setText(tr("free_post_signup_hint"))
        self._key_toggle.setEnabled(False)
        self._key_toggle.setStyleSheet(
            "QToolButton { border: none; font-size: 11px; color: palette(text); font-weight: bold; }"
        )
        # Show key input directly (not as dropdown)
        self._key_content.setVisible(True)
        self._code_input.setFocus()

    def _toggle_key_section(self):
        """Toggle the collapsible key input section."""
        visible = not self._key_content.isVisible()
        self._key_content.setVisible(visible)
        self._key_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )

    def hide_consent(self):
        """Hide the consent checkbox after first generation."""
        self._consent_widget.setVisible(False)

    def set_activation_message(self, text: str, is_error: bool = False):
        # Use brighter variants for dark theme readability
        color = "#ef5350" if is_error else "#66bb6a"
        self._activation_message.setStyleSheet(f"font-size: 11px; color: {color};")
        self._activation_message.setText(text)
        self._activation_message.setVisible(True)

    def set_active_mode(self):
        """Enter active mode: drawing rectangle."""
        self._active = True
        self._bold_label.setVisible(False)
        self._start_btn.setVisible(False)
        self._warning_widget.setVisible(False)
        self._instruction_box.setVisible(True)
        self._status_label.setText("")

    def set_zone_selected(self):
        """Zone drawn: show prompt flow (no Start button)."""
        self._zone_selected = True
        self._bold_label.setVisible(False)
        self._instruction_box.setVisible(False)
        self._prompt_section.setVisible(True)
        self._consent_widget.setVisible(not has_consent())
        self._generate_btn.setVisible(True)
        self._stop_btn.setVisible(True)
        self._update_generate_enabled()
        # Deferred focus: survives Qt layout recalculations from visibility changes
        QTimer.singleShot(0, self._prompt_input.setFocus)

    def set_idle(self):
        """Reset everything to initial state."""
        self._active = False
        self._zone_selected = False
        self._bold_label.setVisible(True)
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
        self._prompt_input.setReadOnly(False)
        self._prompt_input.setStyleSheet(
            "border: 1px solid rgba(128,128,128,0.3); border-radius: 4px; padding: 6px;"
        )
        self._templates_btn.setEnabled(True)
        self._update_layer_warning()

    def set_generating(self, generating: bool):
        """Toggle generation state — keep prompt visible but grayed out."""
        self._progress_widget.setVisible(generating)
        self._start_btn.setVisible(False)
        self._warning_widget.setVisible(False)

        if generating:
            # Reset progress bar to determinate 0%
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(0)
            # Keep prompt section visible but disable interaction
            self._prompt_section.setVisible(True)
            self._prompt_input.setReadOnly(True)
            self._prompt_input.setStyleSheet(
                "border: 1px solid rgba(128,128,128,0.3); "
                "border-radius: 4px; padding: 6px; "
                "background-color: rgba(128,128,128,0.1); color: #999;"
            )
            self._templates_btn.setEnabled(False)
            self._consent_widget.setVisible(False)
            self._generate_btn.setVisible(False)
            self._stop_btn.setVisible(True)
            self._progress_label.setText(tr("preparing"))
        else:
            # Restore prompt interaction
            self._prompt_input.setReadOnly(False)
            self._prompt_input.setStyleSheet(
                "border: 1px solid rgba(128,128,128,0.3); "
                "border-radius: 4px; padding: 6px;"
            )
            self._templates_btn.setEnabled(True)
            self._consent_widget.setVisible(not has_consent() and self._zone_selected)
            self._generate_btn.setVisible(self._zone_selected)
            self._stop_btn.setVisible(self._zone_selected)
            self._prompt_section.setVisible(self._zone_selected)

        self._start_shortcut.setEnabled(not generating)
        self._stop_shortcut.setEnabled(not generating)

    def set_progress_message(self, message: str, percentage: int = -1):
        """Update the progress label and bar during generation."""
        self._progress_label.setText(message)
        if percentage >= 0:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(percentage)

    def set_status(self, message: str, is_error: bool = False):
        color = BRAND_RED if is_error else "#888"
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._status_label.setText(message)
        self._trial_info_box.setVisible(False)

    def set_generation_complete(self, layer_name: str):
        """Show success message and reset to idle state."""
        self._progress_bar.setValue(100)
        self._progress_widget.setVisible(False)
        self._status_label.setText("")
        self._active = False
        self._zone_selected = False
        self._bold_label.setVisible(True)
        self._start_btn.setVisible(True)
        self._start_btn.setEnabled(True)
        self._stop_btn.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        self._start_shortcut.setEnabled(True)
        self._stop_shortcut.setEnabled(True)
        self._prompt_input.clear()
        self._prompt_input.setReadOnly(False)
        self._prompt_input.setStyleSheet(
            "border: 1px solid rgba(128,128,128,0.3); border-radius: 4px; padding: 6px;"
        )
        self._templates_btn.setEnabled(True)
        self._update_layer_warning()

    def show_trial_exhausted_info(self, message: str, subscribe_url: str):
        self._trial_info_text.setText(
            f"{message}\n\n{tr('trial_exhausted_info')}"
        )
        self._trial_info_link.setText(
            f'<a href="{subscribe_url}" style="color: {BRAND_BLUE}; '
            f'font-weight: bold;">{tr("subscribe_link")}</a>'
        )
        self._trial_info_box.setVisible(True)
        self._status_label.setText("")

    def hide_trial_info(self):
        self._trial_info_box.setVisible(False)

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
            self.set_activation_message(tr("free_error_invalid_email"), is_error=True)
            return

        # Disable button during send
        self._free_submit_btn.setEnabled(False)
        self._free_submit_btn.setText(tr("free_sending"))

        self.free_signup_requested.emit(email)

    def _on_unlock_clicked(self):
        code = self._code_input.text().strip()
        if not code:
            self.set_activation_message(tr("enter_code"), is_error=True)
            return
        self.activation_attempted.emit(code)

    def _on_preset_clicked(self, preset: dict):
        self._prompt_input.setPlainText(preset["prompt"])
        self._prompt_input.setFocus()

    def _on_prompt_changed(self):
        self._update_generate_enabled()

    def _on_generate_clicked(self):
        prompt = self.get_prompt()
        if prompt:
            self.generate_clicked.emit(prompt)

    def _on_change_key(self, _link=None):
        self.change_key_clicked.emit()

    def _on_report_bug(self, _link=None):
        from .error_report_dialog import show_bug_report

        show_bug_report(self)

    def _on_consent_changed(self):
        """Re-evaluate Generate button when consent checkbox changes."""
        self._update_generate_enabled()

    def _update_generate_enabled(self):
        has_prompt = bool(self.get_prompt())
        consent_ok = has_consent() or self._consent_check.isChecked()
        enabled = self._zone_selected and has_prompt and consent_ok
        self._generate_btn.setEnabled(enabled)
        self._update_generate_style()

    def _update_generate_style(self):
        if self._generate_btn.isEnabled():
            self._generate_btn.setStyleSheet(
                f"QPushButton {{ background-color: {BRAND_GREEN}; padding: 8px 16px; }}"
                f"QPushButton:disabled {{ background-color: {BRAND_GREEN_DISABLED}; }}"
            )
        else:
            self._generate_btn.setStyleSheet(
                f"QPushButton {{ background-color: {BRAND_DISABLED}; "
                f"padding: 8px 16px; }}"
            )

    def closeEvent(self, event):
        """Cancel generation and disconnect signals on close."""
        if self._progress_widget.isVisible():
            self.stop_clicked.emit()
        try:
            QgsProject.instance().layersAdded.disconnect(self._update_layer_warning)
            QgsProject.instance().layersRemoved.disconnect(self._update_layer_warning)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)
