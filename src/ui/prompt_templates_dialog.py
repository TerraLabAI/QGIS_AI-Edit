"""Prompt Templates Dialog, sidebar + scrollable list with search.

Opens as a modal dialog. User picks a template, dialog closes and returns
the selected preset (id + prompt text) to the caller.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import tr
from ..core.prompt_presets import get_all_categories

# ---------------------------------------------------------------------------
# QSS
# ---------------------------------------------------------------------------
_SIDEBAR_ITEM = (
    "QPushButton {{ text-align: left; border: none; border-radius: 4px; "
    "padding: 6px 10px; font-size: 13px; color: palette(text); "
    "background: transparent; }}"
    "QPushButton:hover {{ background: rgba(128,128,128,0.12); }}"
)

_SIDEBAR_ITEM_ACTIVE = (
    "QPushButton {{ text-align: left; border: none; border-radius: 4px; "
    "padding: 6px 10px; font-size: 13px; font-weight: bold; "
    "color: {color}; background: rgba(128,128,128,0.15); }}"
)

_SEARCH_BOX = (
    "QLineEdit { border: 1px solid rgba(128,128,128,0.3); "
    "border-radius: 4px; padding: 6px 10px; font-size: 13px; "
    "color: palette(text); background: palette(base); }"
)

_CARD_NORMAL = (
    "QFrame { border: 1px solid rgba(128,128,128,0.15); "
    "border-radius: 4px; background: rgba(128,128,128,0.03); }"
)

_CARD_HOVER = (
    "QFrame { border: 1px solid rgba(128,128,128,0.35); "
    "border-radius: 4px; background: rgba(128,128,128,0.08); }"
)

_SECTION_HEADER = (
    "QLabel {{ font-size: 13px; font-weight: bold; "
    "color: {color}; padding: 4px 0px; }}"
)

# Muted color per category — sidebar icons only
_SIDEBAR_COLORS = {
    "favorites": "#b89868",
    "clean": "#b07878",
    "add": "#68a868",
    "style": "#9880b0",
    "detect": "#b08858",
    "simulate": "#a0a058",
}

# Colored HTML icons for sidebar (rendered as rich text for color support)
_SIDEBAR_LABELS = {
    "favorites": ('<span style="color:#b89868; font-size:15px;">\u2605</span>', "Top Picks"),
    "clean": ('<span style="color:#b07878; font-size:15px;">\u232b</span>', "Clean"),
    "add": ('<span style="color:#68a868; font-size:15px;">+</span>', "Add"),
    "style": ('<span style="color:#9880b0; font-size:15px;">\u2726</span>', "Style"),
    "detect": ('<span style="color:#b08858; font-size:15px;">\u25c9</span>', "Detect"),
    "simulate": ('<span style="color:#a0a058; font-size:15px;">\u21bb</span>', "Simulate"),
}

# Plain icons for section headers
_SECTION_ICONS = {
    "favorites": "\u2605",
    "clean": "\u232b",
    "add": "+",
    "style": "\u2726",
    "detect": "\u25c9",
    "simulate": "\u21bb",
}


class _ClickableCard(QFrame):
    """A QFrame that acts as a clickable card."""

    def __init__(self, preset: dict, on_click, parent=None):
        super().__init__(parent)
        self._preset = preset
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(_CARD_NORMAL)

    def enterEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_HOVER)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_NORMAL)
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._on_click(self._preset)
        super().mousePressEvent(event)


class _SidebarButton(QPushButton):
    """Sidebar button with colored HTML icon via rich text label overlay."""

    def __init__(self, icon_html: str, text: str, parent=None):
        super().__init__(parent)
        self.setText("")
        self._label = QLabel(
            f'{icon_html}  <span style="font-size:13px; color:palette(text);">{text}</span>'
        )
        self._label.setTextFormat(Qt.RichText)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._label.setStyleSheet("background: transparent; border: none; padding: 0px;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(self._label)


class PromptTemplatesDialog(QDialog):
    """Modal dialog for browsing and selecting prompt templates."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Prompt Templates"))
        self.setMinimumSize(580, 440)
        self.resize(660, 560)
        self.setSizeGripEnabled(True)

        self._selected_preset: dict | None = None
        self._categories = get_all_categories()
        self._sidebar_buttons: list[_SidebarButton] = []
        self._section_widgets: dict[str, QWidget] = {}
        self._card_widgets: list[tuple[QWidget, dict, str]] = []
        self._active_cat_key: str = "favorites"

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Search bar
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(tr("Search templates..."))
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setStyleSheet(_SEARCH_BOX)
        self._search_input.textChanged.connect(self._on_search_changed)
        root.addWidget(self._search_input)

        # Main area: sidebar + scroll
        body = QHBoxLayout()
        body.setSpacing(8)

        # Sidebar
        sidebar_widget = QWidget()
        sidebar_widget.setFixedWidth(120)
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(2)

        for cat in self._categories:
            sidebar_info = _SIDEBAR_LABELS.get(cat["key"], ("", cat["label"]))
            icon_html, label_text = sidebar_info
            btn = _SidebarButton(icon_html, label_text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setProperty("cat_key", cat["key"])
            btn.clicked.connect(
                lambda checked, k=cat["key"]: self._scroll_to_category(k)
            )
            sidebar_layout.addWidget(btn)
            self._sidebar_buttons.append(btn)

        sidebar_layout.addStretch()
        body.addWidget(sidebar_widget)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        body.addWidget(sep)

        # Scrollable content
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )

        content_widget = QWidget()
        self._content_layout = QVBoxLayout(content_widget)
        self._content_layout.setContentsMargins(4, 0, 4, 0)
        self._content_layout.setSpacing(12)

        for cat in self._categories:
            section = self._build_section(cat)
            self._content_layout.addWidget(section)
            self._section_widgets[cat["key"]] = section

        self._content_layout.addStretch()
        self._scroll_area.setWidget(content_widget)
        self._scroll_area.verticalScrollBar().valueChanged.connect(
            self._on_scroll_changed
        )

        body.addWidget(self._scroll_area, 1)
        root.addLayout(body, 1)

        self._update_sidebar_highlight("favorites")

    def _build_section(self, category: dict) -> QWidget:
        """Build one category section with header + preset cards."""
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        cat_key = category["key"]
        icon = _SECTION_ICONS.get(cat_key, "")
        color = _SIDEBAR_COLORS.get(cat_key, "palette(text)")

        header = QLabel(f"{icon}  {category['label']}")
        header.setStyleSheet(_SECTION_HEADER.format(color=color))
        layout.addWidget(header)

        for preset in category["presets"]:
            card = self._build_preset_card(preset, cat_key, color)
            layout.addWidget(card)
            self._card_widgets.append((card, preset, cat_key))

        return section

    def _build_preset_card(
        self, preset: dict, cat_key: str, color: str = "palette(text)"
    ) -> _ClickableCard:
        """Build a clickable preset card."""
        card = _ClickableCard(preset, self._on_use_clicked)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 7, 10, 7)
        card_layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        # For favorites, resolve color from source category
        if cat_key == "favorites":
            src = preset.get("source_category", "")
            if src:
                color = _SIDEBAR_COLORS.get(src, color)

        label = QLabel(preset["label"])
        label.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {color}; "
            "background: transparent; border: none;"
        )
        top_row.addWidget(label)

        top_row.addStretch()
        card_layout.addLayout(top_row)

        # Prompt text
        prompt_label = QLabel(preset["prompt"])
        prompt_label.setWordWrap(True)
        prompt_label.setStyleSheet(
            "font-size: 11px; color: palette(text); padding: 0px; "
            "background: transparent; border: none;"
        )
        card_layout.addWidget(prompt_label)

        return card

    def _on_use_clicked(self, preset: dict):
        self._selected_preset = preset
        self.accept()

    def get_selected_preset(self) -> dict | None:
        return self._selected_preset

    def _scroll_to_category(self, cat_key: str):
        """Scroll so the category header is at the top of the viewport."""
        widget = self._section_widgets.get(cat_key)
        if not widget:
            return
        content_widget = self._scroll_area.widget()
        pos_in_content = widget.mapTo(content_widget, widget.rect().topLeft())
        self._scroll_area.verticalScrollBar().setValue(pos_in_content.y())
        self._update_sidebar_highlight(cat_key)

    def _on_scroll_changed(self):
        sb = self._scroll_area.verticalScrollBar()
        viewport_top = sb.value()

        # When scrolled to the bottom, highlight the last section
        if sb.value() >= sb.maximum() - 5:
            cat_keys = list(self._section_widgets.keys())
            if cat_keys:
                last_key = cat_keys[-1]
                if last_key != self._active_cat_key:
                    self._update_sidebar_highlight(last_key)
                return

        closest_key = "favorites"
        closest_dist = float("inf")

        for cat_key, widget in self._section_widgets.items():
            widget_top = widget.mapTo(
                self._scroll_area.widget(), widget.rect().topLeft()
            ).y()
            dist = abs(widget_top - viewport_top)
            if widget_top <= viewport_top + 40 and dist < closest_dist:
                closest_dist = dist
                closest_key = cat_key

        if closest_key != self._active_cat_key:
            self._update_sidebar_highlight(closest_key)

    def _update_sidebar_highlight(self, active_key: str):
        self._active_cat_key = active_key
        for btn in self._sidebar_buttons:
            cat_key = btn.property("cat_key")
            if cat_key == active_key:
                color = _SIDEBAR_COLORS.get(cat_key, "palette(text)")
                btn.setStyleSheet(_SIDEBAR_ITEM_ACTIVE.format(color=color))
            else:
                btn.setStyleSheet(_SIDEBAR_ITEM.format())

    def _on_search_changed(self, text: str):
        query = text.strip().lower()
        visible_cats: set[str] = set()

        for card_widget, preset, cat_key in self._card_widgets:
            if not query:
                card_widget.setVisible(True)
                visible_cats.add(cat_key)
            else:
                match = (
                    query in preset["label"].lower()
                    or query in preset["prompt"].lower()
                    or query in preset.get("source_category", "").lower()
                )
                card_widget.setVisible(match)
                if match:
                    visible_cats.add(cat_key)

        for cat_key, section_widget in self._section_widgets.items():
            section_widget.setVisible(cat_key in visible_cats)

        if query:
            for btn in self._sidebar_buttons:
                btn.setStyleSheet(_SIDEBAR_ITEM.format())
        else:
            self._update_sidebar_highlight(self._active_cat_key)
