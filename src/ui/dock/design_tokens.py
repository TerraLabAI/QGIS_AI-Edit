





















from __future__ import annotations


def is_dark_theme() -> bool:

    try:
        from qgis.PyQt.QtGui import QPalette
        from qgis.PyQt.QtWidgets import QApplication

        app = QApplication.instance()
        palette = app.palette() if app is not None else QPalette()
        return palette.color(QPalette.ColorRole.Window).lightness() < 128
    except Exception:  # noqa: BLE001
        return False




_LIGHT_PALETTE = {
    "page": "#f7f8fa", "canvas": "#eef0f3", "surface": "#ffffff", "inset": "#f4f6f8",
    "hover": "#eff1f4", "hover_2": "#e2e5ea", "field": "#eef0f3",
    "ink": "#1f2124", "ink_2": "#5b5f66", "ink_3": "#6b7079", "ink_hover": "#33363b",
    "line": "#e2e5ea", "line_strong": "#cdd2d9", "line_soft": "#eef0f3",
    "green": "#199a4d", "green_tint": "#e8f5ed",
    "orange": "#ef720d", "orange_tint": "#fdf1e5",
    "red": "#e3474c", "red_tint": "#fcecec",
    "accent_ink": "#437010",
    "line_input": "#80868f",
    "green_text": "#2e7d32", "orange_text": "#b45309", "red_text": "#c62828",
    "accent_tint": "rgba(139, 172, 39, 0.10)",
    "accent_tint_on": "rgba(139, 172, 39, 0.20)",
}
_DARK_PALETTE = {
    "page": "#17181a", "canvas": "#1c1d1f", "surface": "#232427", "inset": "#1f2022",
    "hover": "#2a2b2e", "hover_2": "#313236", "field": "#2b2c2f",
    "ink": "#f2f3f4", "ink_2": "#a5a8ad", "ink_3": "#92959b", "ink_hover": "#d7dade",
    "line": "#2e3033", "line_strong": "#3a3c40", "line_soft": "#27282b",
    "green": "#3cbb72", "green_tint": "rgba(60, 187, 114, 0.14)",
    "orange": "#f68f3c", "orange_tint": "rgba(246, 143, 60, 0.14)",
    "red": "#ee5c61", "red_tint": "rgba(238, 92, 97, 0.14)",
    "accent_ink": "#a3c644",
    "line_input": "#7a7d83",
    "green_text": "#66bb6a", "orange_text": "#f5a623", "red_text": "#f26b69",
    "accent_tint": "rgba(139, 172, 39, 0.16)",
    "accent_tint_on": "rgba(139, 172, 39, 0.26)",
}

DARK = is_dark_theme()
_PALETTE = _DARK_PALETTE if DARK else _LIGHT_PALETTE


PAGE = _PALETTE["page"]
CANVAS = _PALETTE["canvas"]
SURFACE = _PALETTE["surface"]
INSET = _PALETTE["inset"]
HOVER = _PALETTE["hover"]
HOVER_ON = _PALETTE["hover_2"]
FIELD = _PALETTE["field"]

INK = _PALETTE["ink"]
INK_2 = _PALETTE["ink_2"]
INK_3 = _PALETTE["ink_3"]
INK_HOVER = _PALETTE["ink_hover"]

LINE = _PALETTE["line"]
LINE_STRONG = _PALETTE["line_strong"]
LINE_SOFT = _PALETTE["line_soft"]

GREEN = _PALETTE["green"]
GREEN_TINT = _PALETTE["green_tint"]
ORANGE = _PALETTE["orange"]
ORANGE_TINT = _PALETTE["orange_tint"]
RED = _PALETTE["red"]
RED_TINT = _PALETTE["red_tint"]


GREEN_TEXT = _PALETTE["green_text"]
ORANGE_TEXT = _PALETTE["orange_text"]
RED_TEXT = _PALETTE["red_text"]


LINE_INPUT = _PALETTE["line_input"]


ACCENT = "#43a047"

ACCENT_DARK = "#57b35b"

ACCENT_DISABLED = "rgba(67, 160, 71, 0.40)"


PRIMARY_DISABLED_FILL = "#45474c" if DARK else "#e2e5ea"
PRIMARY_DISABLED_INK = _PALETTE["ink_3"]
ON_ACCENT = "#000000"

BRAND_BLUE = "#1e88e5"
BRAND_BLUE_HOVER = "#1976d2"
ACCENT_TINT = "rgba(30, 136, 229, 0.10)"
ACCENT_TINT_ON = "rgba(30, 136, 229, 0.22)"
ACCENT_BORDER_SOFT = "rgba(30, 136, 229, 0.35)"
ACCENT_BORDER = BRAND_BLUE


LINK_INK = "#1565c0" if not DARK else "#42a5f5"

BRAND_GREEN = "#8bac27"
ACCENT_INK = _PALETTE["accent_ink"]


ON_BLUE = "#000000"


DANGER_FILL = "#c62828"
DANGER_FILL_HOVER = "#b71c1c"
ON_DANGER = "#ffffff"


FIXED_INK = _LIGHT_PALETTE["ink"]
LOGO_TILE_GROUND = _LIGHT_PALETTE["inset"]


FONT_PROSE = 14
FONT_BASE = 13
FONT_BODY = 12
FONT_HINT = 11

FONT_MICRO = 11


SPACE_OUTER = 8
SPACE_CARD = 6
SPACE_TIGHT = 4
SPACE_STAGE = 12
CARD_MARGINS = (12, 10, 12, 10)


RADIUS_CHIP = 6
RADIUS_CONTROL = 8
RADIUS_CARD = 10
RADIUS_BOX = 12
RADIUS_PANEL = 14
RADIUS_COMPOSER = 14


BTN_PX = 32
BTN_SMALL_PX = 28
BTN_PRIMARY_WIDE_PX = 36

RADIUS_PILL = BTN_PX // 2
RADIUS_PILL_WIDE = BTN_PRIMARY_WIDE_PX // 2
RADIUS_PILL_SMALL = BTN_SMALL_PX // 2
ROW_PX = 28
CHIP_PX = 22

MOTION_HOVER_MS = 120
MOTION_FOLD_MS = 300

MONO_FAMILY = "Consolas, 'DejaVu Sans Mono', Menlo, monospace"











_FOCUS_ON_FILL = f"border: 2px solid {ACCENT_BORDER};"

BTN_PRIMARY_QSS = (
    f"QPushButton {{ background: {ACCENT}; color: {ON_ACCENT};"
    f" border: none; border-radius: {RADIUS_PILL}px; padding: 0 16px;"
    f" min-height: {BTN_PX}px; font-size: {FONT_BODY}px; font-weight: 600; }}"
    f"QPushButton:hover {{ background: {ACCENT_DARK}; }}"
    f"QPushButton:pressed {{ background: {ACCENT_DARK}; }}"
    f"QPushButton:focus {{ {_FOCUS_ON_FILL} padding: 0 14px; min-height: {BTN_PX - 4}px; }}"
    f"QPushButton:disabled {{ background: {PRIMARY_DISABLED_FILL}; color: {PRIMARY_DISABLED_INK}; }}"
)

BTN_PRIMARY_WIDE_QSS = (
    f"QPushButton {{ background: {ACCENT}; color: {ON_ACCENT};"
    f" border: none; border-radius: {RADIUS_PILL_WIDE}px; padding: 0 18px;"
    f" min-height: {BTN_PRIMARY_WIDE_PX}px; font-size: {FONT_BASE}px; font-weight: 600; }}"
    f"QPushButton:hover {{ background: {ACCENT_DARK}; }}"
    f"QPushButton:pressed {{ background: {ACCENT_DARK}; }}"
    f"QPushButton:focus {{ {_FOCUS_ON_FILL} padding: 0 16px; min-height: {BTN_PRIMARY_WIDE_PX - 4}px; }}"
    f"QPushButton:disabled {{ background: {PRIMARY_DISABLED_FILL}; color: {PRIMARY_DISABLED_INK}; }}"
)
BTN_GHOST_QSS = (
    f"QPushButton {{ background: {SURFACE}; color: {INK};"
    f" border: 1px solid {LINE_INPUT}; border-radius: {RADIUS_PILL}px;"
    f" padding: 0 14px; min-height: {BTN_PX - 2}px; font-size: {FONT_BODY}px; font-weight: 500; }}"
    f"QPushButton:hover {{ background: {HOVER}; border-color: {INK_3}; }}"
    f"QPushButton:pressed {{ background: {HOVER_ON}; }}"
    f"QPushButton:checked {{ background: {ACCENT_TINT_ON}; border-color: {ACCENT_BORDER}; }}"
    f"QPushButton:focus {{ {_FOCUS_ON_FILL} padding: 0 13px; min-height: {BTN_PX - 4}px; }}"
    f"QPushButton:disabled {{ color: {INK_3}; border-color: {LINE}; background: transparent; }}"
)

BTN_GHOST_WIDE_QSS = BTN_GHOST_QSS + (
    f"QPushButton {{ border-radius: {RADIUS_PILL_WIDE}px; padding: 0 18px;"
    f" min-height: {BTN_PRIMARY_WIDE_PX - 2}px; font-size: {FONT_BASE}px; font-weight: 600; }}"
    f"QPushButton:focus {{ padding: 0 17px; min-height: {BTN_PRIMARY_WIDE_PX - 4}px; }}"
)
BTN_DANGER_GHOST_QSS = (
    f"QPushButton {{ background: {SURFACE}; color: {RED_TEXT};"
    f" border: 1px solid {LINE_INPUT}; border-radius: {RADIUS_PILL}px;"
    f" padding: 0 14px; min-height: {BTN_PX - 2}px; font-size: {FONT_BODY}px; font-weight: 500; }}"
    f"QPushButton:hover {{ background: {RED_TINT}; border-color: {RED}; }}"
    f"QPushButton:pressed {{ background: {RED_TINT}; border-color: {RED}; }}"
    f"QPushButton:focus {{ {_FOCUS_ON_FILL} padding: 0 13px; min-height: {BTN_PX - 4}px; }}"
    f"QPushButton:disabled {{ color: {INK_3}; border-color: {LINE}; background: transparent; }}"
)
BTN_QUIET_QSS = (
    "QPushButton { background: transparent; border: 1px solid transparent;"
    f" color: {INK_2}; font-size: {FONT_BODY}px; font-weight: 500; padding: 3px 7px;"
    f" border-radius: {RADIUS_PILL_SMALL}px; }}"
    f"QPushButton:hover {{ color: {INK}; background: {HOVER}; }}"
    f"QPushButton:pressed {{ background: {HOVER_ON}; }}"
    f"QPushButton:focus {{ color: {INK}; border-color: {ACCENT_BORDER}; }}"
    f"QPushButton:disabled {{ color: {INK_3}; }}"
)

BTN_LINK_QSS = (
    "QPushButton { background: transparent; border: 1px solid transparent;"
    f" color: {LINK_INK}; font-size: {FONT_BODY}px; font-weight: 500; padding: 1px 3px;"
    f" border-radius: {RADIUS_CHIP}px; text-align: left; }}"
    f"QPushButton:hover {{ background: {ACCENT_TINT}; color: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:focus {{ border-color: {ACCENT_BORDER}; }}"
)



BTN_ICON_QSS = (
    "QToolButton { background: transparent; border: 1px solid transparent; padding: 3px;"
    f" border-radius: {RADIUS_CONTROL}px; color: {INK_2}; }}"
    f"QToolButton:hover {{ background: {HOVER}; color: {INK}; }}"
    f"QToolButton:pressed {{ background: {HOVER_ON}; }}"
    f'QToolButton[active="true"], QToolButton:checked {{ background: {ACCENT_TINT_ON}; }}'
    f"QToolButton:focus {{ border-color: {ACCENT_BORDER}; }}"
    "QToolButton:disabled { background: transparent; }"
    "QToolButton::menu-indicator { image: none; width: 0; }"
)



MENU_QSS = (
    f"QMenu {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {RADIUS_CARD}px; padding: 6px; }}"
    "QMenu::item { background: transparent; padding: 7px 12px 7px 10px;"
    f" border-radius: {RADIUS_CHIP}px; color: {INK}; font-size: {FONT_BODY}px; }}"
    f"QMenu::item:selected {{ background: {HOVER}; color: {INK}; }}"
    f"QMenu::item:disabled {{ color: {INK_3}; }}"
    f"QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}"
    "QMenu::icon { padding-left: 6px; }"
)




CARD_QSS = (
    f"QFrame#card, QWidget#card {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#sheet, QWidget#sheet {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {RADIUS_PANEL}px; }}"
    f"QFrame#inset, QWidget#inset {{ background: {INSET}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CONTROL}px; }}"
)



INPUT_QSS = (
    f"QLineEdit, QPlainTextEdit, QTextEdit {{ background: {SURFACE}; color: {INK};"
    f" border: 1px solid {LINE_INPUT}; border-radius: {RADIUS_CONTROL}px;"
    f" padding: 6px 10px; font-size: {FONT_BASE}px;"
    f" selection-background-color: {ACCENT_BORDER}; }}"
    f"QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover {{ border-color: {ACCENT_BORDER_SOFT}; }}"
    f"QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {ACCENT_BORDER}; }}"
    f"QLineEdit:disabled {{ color: {INK_3}; background: {FIELD}; }}"
)


TITLE_QSS = f"font-size: {FONT_BASE}px; font-weight: 600; color: {INK}; background: transparent; border: none;"
HEADLINE_QSS = f"font-size: {FONT_BASE + 5}px; font-weight: 600; color: {INK}; background: transparent; border: none;"
BODY_QSS = f"font-size: {FONT_BODY}px; color: {INK}; background: transparent; border: none;"
HINT_QSS = f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none;"
MICRO_QSS = (
    f"font-size: {FONT_MICRO}px; font-weight: 600; color: {INK_3};"
    " background: transparent; border: none;"
)


SCROLL_AREA_QSS = (
    "QScrollArea { background: transparent; border: none; }"
    "QScrollBar:vertical { background: transparent; width: 8px;"
    " margin: 2px 2px 2px 0; border: none; }"
    f"QScrollBar::handle:vertical {{ background: {LINE_STRONG};"
    " border-radius: 3px; min-height: 24px; }"
    f"QScrollBar::handle:vertical:hover {{ background: {INK_3}; }}"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
    " height: 0px; width: 0px; border: none; background: none; }"
    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
    "QScrollBar:horizontal { background: transparent; height: 8px; border: none; }"
    f"QScrollBar::handle:horizontal {{ background: {LINE_STRONG}; border-radius: 3px; min-width: 24px; }}"
    "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; border: none; }"
    "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }"
)


TOOLTIP_QSS = (
    f"QToolTip {{ background: {'#111214' if DARK else '#25272b'}; color: #f2f3f4;"
    f" border: 1px solid {'#2e3033' if DARK else '#3a3c40'}; border-radius: {RADIUS_CHIP}px;"
    f" padding: 4px 8px; font-size: {FONT_HINT}px; }}"
)









_CATEGORY_HUES = {

    "green": ("#43a047", "#2e7d32", "#66bb6a", "67, 160, 71"),
    "leaf": ("#8bac27", "#437010", "#a3c644", "139, 172, 39"),
    "amber": ("#e0952b", "#96590c", "#f0b252", "224, 149, 43"),
    "teal": ("#1f9e96", "#0b6e68", "#3cc3ba", "31, 158, 150"),
    "coral": ("#e0603f", "#b23c20", "#f28a6c", "224, 96, 63"),
    "violet": ("#7c6cd0", "#5a48b8", "#a597ee", "124, 108, 208"),
    "sky": ("#3e86d6", "#1f5fa8", "#6ea8ec", "62, 134, 214"),
}
CATEGORY_NAMES = tuple(_CATEGORY_HUES)


def category_fill(name: str) -> str:

    return _CATEGORY_HUES.get(name, _CATEGORY_HUES["green"])[0]


def category_ink(name: str) -> str:

    _fill, light, dark, _rgb = _CATEGORY_HUES.get(name, _CATEGORY_HUES["green"])
    return dark if DARK else light


def category_tint(name: str, strong: bool = False) -> str:

    rgb = _CATEGORY_HUES.get(name, _CATEGORY_HUES["green"])[3]
    alpha = (0.26 if strong else 0.18) if DARK else (0.20 if strong else 0.12)
    return f"rgba({rgb}, {alpha})"


def category_line(name: str) -> str:

    rgb = _CATEGORY_HUES.get(name, _CATEGORY_HUES["green"])[3]
    return f"rgba({rgb}, {0.40 if DARK else 0.35})"


def gauge_category(fraction_left: float) -> str:

    if fraction_left <= 0:
        return "coral"
    return "amber" if fraction_left < 0.2 else "leaf"





PROGRESS_BAR_PX = 8
PROGRESS_HUE_START = "sky"
PROGRESS_HUE_DONE = "green"


def blend_hex_colors(start_hex: str, end_hex: str, t: float) -> str:

    t = max(0.0, min(1.0, float(t)))
    start = start_hex.lstrip("#")
    end = end_hex.lstrip("#")
    out = []
    for i in (0, 2, 4):
        a = int(start[i:i + 2], 16)
        b = int(end[i:i + 2], 16)
        out.append(f"{round(a + (b - a) * t):02x}")
    return "#" + "".join(out)


def progress_fill_color(ratio: float) -> str:

    t = max(0.0, min(1.0, float(ratio)))
    t = t * t * (3.0 - 2.0 * t)
    return blend_hex_colors(
        category_ink(PROGRESS_HUE_START), category_ink(PROGRESS_HUE_DONE), t)


def progress_bar_qss(ratio: float = 0.0) -> str:

    radius = PROGRESS_BAR_PX // 2
    return (
        f"QProgressBar {{ background: {FIELD}; border: none; border-radius: {radius}px;"
        f" max-height: {PROGRESS_BAR_PX}px; min-height: {PROGRESS_BAR_PX}px; text-align: center; }}"
        f"QProgressBar::chunk {{ background: {progress_fill_color(ratio)};"
        f" border-radius: {radius}px; }}"
    )


PROGRESS_QSS = progress_bar_qss(0.0)





PICKED_HUE = "green"


def picked_qss(selector: str, hue: str = PICKED_HUE) -> str:

    ink = category_ink(hue)
    return (
        f"{selector}:checked {{ background: {category_tint(hue)};"
        f" border: 1px solid {ink}; color: {ink}; font-weight: 600; }}"
        f"{selector}:checked:hover {{ background: {category_tint(hue, strong=True)}; }}"
    )


def qcolor(token: str):

    from qgis.PyQt.QtGui import QColor

    text = str(token or "").strip()
    if text.startswith(("rgba(", "rgb(")):
        parts = [p.strip() for p in text[text.index("(") + 1:text.rindex(")")].split(",")]
        try:
            r, g, b = (int(float(p)) for p in parts[:3])
            alpha = float(parts[3]) if len(parts) > 3 else 1.0
        except (ValueError, IndexError):
            return QColor()
        color = QColor(r, g, b)
        color.setAlphaF(max(0.0, min(1.0, alpha)))
        return color
    return QColor(text)


def repolish_widget(widget) -> None:

    try:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
    except (RuntimeError, AttributeError):
        pass
