"""AI Edit's design tokens: the AI Agent line, in AI Edit's colours.

AI Agent (``QGIS_AI-Agent-Team/src/ui/style.py``, ``docs/DESIGN.md``) is the
reference for every TerraLab panel since 2026-09-17. Its line is ChatGPT's,
read through Beautiful UI: four steps of one neutral (page, canvas, surface,
inset), three inks, three hairlines, a field, corners of 6, 8, 10 and 14.
The neutrals, sizes and radii below are AI Agent's values.

The colours are AI Edit's and AI Segmentation's own (Yvann, 2026-09-17, the
same decision in both plugins, mirrored from Segmentation's
``src/ui/dock/styles.py``): the Material green fills the one control a screen
asks the user to press (Generate, Upgrade, Sign in) with black text; the
brand blue answers the pointer and the keyboard (hover wash, selected row,
focus, a link); the TerraLab leaf green is the brand (logo, a green word).
Buttons are pills, their radius half their height, as in Segmentation.
Green, orange and red keep their meaning: done, needs review, failed.

The palette is resolved once at import for the QGIS theme in force, so every
constant below is a plain string a stylesheet can take. A painted surface (a
delegate, a ``paintEvent``) reads the same token through ``qcolor()``: Qt's
``QColor`` cannot parse ``rgba(...)`` and paints it opaque black.
"""
from __future__ import annotations


def is_dark_theme() -> bool:
    """Whether QGIS runs a dark palette. Resolved once, at import."""
    try:
        from qgis.PyQt.QtGui import QPalette
        from qgis.PyQt.QtWidgets import QApplication

        app = QApplication.instance()
        palette = app.palette() if app is not None else QPalette()
        return palette.color(QPalette.ColorRole.Window).lightness() < 128
    except Exception:  # noqa: BLE001 - no application reads as a light theme
        return False


# The two palettes, AI Agent's own values (light steps widened so a filled
# shape still reads on a white QGIS).
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

# Surfaces: four steps of one neutral.
PAGE = _PALETTE["page"]
CANVAS = _PALETTE["canvas"]
SURFACE = _PALETTE["surface"]
INSET = _PALETTE["inset"]
HOVER = _PALETTE["hover"]
HOVER_ON = _PALETTE["hover_2"]
FIELD = _PALETTE["field"]
# Ink: text, secondary text, numbers and glyphs at rest.
INK = _PALETTE["ink"]
INK_2 = _PALETTE["ink_2"]
INK_3 = _PALETTE["ink_3"]
INK_HOVER = _PALETTE["ink_hover"]
# Hairlines: a card's, a sheet's, a divider.
LINE = _PALETTE["line"]
LINE_STRONG = _PALETTE["line_strong"]
LINE_SOFT = _PALETTE["line_soft"]
# Colours with a meaning.
GREEN = _PALETTE["green"]
GREEN_TINT = _PALETTE["green_tint"]
ORANGE = _PALETTE["orange"]
ORANGE_TINT = _PALETTE["orange_tint"]
RED = _PALETTE["red"]
RED_TINT = _PALETTE["red_tint"]
# The same meanings as text: 4.5:1 or better on the panel (AI Segmentation's
# values, d7fda634). The fills and glyphs above keep their brighter shades.
GREEN_TEXT = _PALETTE["green_text"]
ORANGE_TEXT = _PALETTE["orange_text"]
RED_TEXT = _PALETTE["red_text"]
# The outline of an input, an outline button and a slider knob: 3:1 on the
# panel. LINE and LINE_STRONG stay decorative.
LINE_INPUT = _PALETTE["line_input"]

# The primary fill: the Material green, black text on it (white fails).
ACCENT = "#43a047"
# Hover: lighter, so the black words stay at 8:1 (a darker green fell to 4.1).
ACCENT_DARK = "#57b35b"
# Disabled primary: the fill at 40 %, as in AI Agent, never a grey or a pale wash.
ACCENT_DISABLED = "rgba(67, 160, 71, 0.40)"
# A primary that cannot run yet reads as grey, never as a dim green that
# looks pressable (ChatGPT's send button, AI Agent's composer).
PRIMARY_DISABLED_FILL = "#45474c" if DARK else "#e2e5ea"
PRIMARY_DISABLED_INK = _PALETTE["ink_3"]
ON_ACCENT = "#000000"
# The interaction colour: the brand blue at four strengths.
BRAND_BLUE = "#1e88e5"
BRAND_BLUE_HOVER = "#1976d2"
ACCENT_TINT = "rgba(30, 136, 229, 0.10)"
ACCENT_TINT_ON = "rgba(30, 136, 229, 0.22)"
ACCENT_BORDER_SOFT = "rgba(30, 136, 229, 0.35)"
ACCENT_BORDER = BRAND_BLUE
# A link or a blue word, text only: 5.7:1 or better on the panel. The
# #1e88e5 fill is unchanged.
LINK_INK = "#1565c0" if not DARK else "#42a5f5"
# The brand: the TerraLab leaf green. A green word or glyph uses the ink.
BRAND_GREEN = "#8bac27"
ACCENT_INK = _PALETTE["accent_ink"]
# Ink on a blue fill (a checked box): black, 5.6:1. White is 3.7:1 and never
# goes on #1e88e5.
ON_BLUE = "#000000"
# The one red fill: a confirm window's destructive primary. White on it is
# 5.6:1; the hover goes darker.
DANGER_FILL = "#c62828"
DANGER_FILL_HOVER = "#b71c1c"
ON_DANGER = "#ffffff"
# Grounds that stay light on both themes: the ink of a handle drawn over
# imagery, and the tile behind a plugin logo.
FIXED_INK = _LIGHT_PALETTE["ink"]
LOGO_TILE_GROUND = _LIGHT_PALETTE["inset"]

# Type scale, px.
FONT_PROSE = 14
FONT_BASE = 13
FONT_BODY = 12
FONT_HINT = 11
# 11 px is the smallest text the line draws (accessibility floor).
FONT_MICRO = 11

# Spacing rhythm: 8 between sections, 6 inside a card, 4 on a tight row.
SPACE_OUTER = 8
SPACE_CARD = 6
SPACE_TIGHT = 4
SPACE_STAGE = 12
CARD_MARGINS = (12, 10, 12, 10)

# Radii: a chip, a control (button, row, input), a card, a sheet.
RADIUS_CHIP = 6
RADIUS_CONTROL = 8
RADIUS_CARD = 10
RADIUS_BOX = 12
RADIUS_PANEL = 14
RADIUS_COMPOSER = 14

# Heights: a button, a card's small button, the wide primary, a chip.
BTN_PX = 32
BTN_SMALL_PX = 28
BTN_PRIMARY_WIDE_PX = 36
# Buttons are pills: Qt squares a corner past half the height.
RADIUS_PILL = BTN_PX // 2
RADIUS_PILL_WIDE = BTN_PRIMARY_WIDE_PX // 2
RADIUS_PILL_SMALL = BTN_SMALL_PX // 2
ROW_PX = 28
CHIP_PX = 22

MOTION_HOVER_MS = 120
MOTION_FOLD_MS = 300

MONO_FAMILY = "Consolas, 'DejaVu Sans Mono', Menlo, monospace"


# ---------------------------------------------------------------------------
# Buttons: four kinds and no more. Primary is the green filled, one per
# screen; ghost is a pill on the strong hairline; danger ghost is red text;
# quiet is text on nothing. 32 px pills, 12 px semibold.
# ---------------------------------------------------------------------------

# Keyboard focus (buttons take focus by Tab only, ``src/ui/keyboard_focus.py``):
# a 2 px ring in the interaction blue. Padding and height give the ring's
# width back so a focused button never grows.
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
# The wide primary of a card or a screen (Generate, Upgrade, Sign in).
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
# The wide outline twin: a screen's offer that is not its one filled action.
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
# A text link in the brand blue (Manage account, a TerraLab link).
BTN_LINK_QSS = (
    "QPushButton { background: transparent; border: 1px solid transparent;"
    f" color: {LINK_INK}; font-size: {FONT_BODY}px; font-weight: 500; padding: 1px 3px;"
    f" border-radius: {RADIUS_CHIP}px; text-align: left; }}"
    f"QPushButton:hover {{ background: {ACCENT_TINT}; color: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:focus {{ border-color: {ACCENT_BORDER}; }}"
)

# Flat icon button (header, footer): transparent, a hover step under the
# pointer, the accent tint while its tool or menu is open.
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

# Menus: surface on the strong hairline, 10 px corners, rows with 6 px
# corners that take the hover step.
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

# A card: surface on a hairline, 10 px corners. A sheet: the strong hairline,
# 14 px corners. A well: the inset step. Keyed on object names so a widget
# only has to name itself (and set WA_StyledBackground).
CARD_QSS = (
    f"QFrame#card, QWidget#card {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#sheet, QWidget#sheet {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {RADIUS_PANEL}px; }}"
    f"QFrame#inset, QWidget#inset {{ background: {INSET}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CONTROL}px; }}"
)

# An input: surface on a hairline, 8 px corners, the ink named explicitly so a
# parent sheet cannot drop it off the theme; focus turns the border green.
INPUT_QSS = (
    f"QLineEdit, QPlainTextEdit, QTextEdit {{ background: {SURFACE}; color: {INK};"
    f" border: 1px solid {LINE_INPUT}; border-radius: {RADIUS_CONTROL}px;"
    f" padding: 6px 10px; font-size: {FONT_BASE}px;"
    f" selection-background-color: {ACCENT_BORDER}; }}"
    f"QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover {{ border-color: {ACCENT_BORDER_SOFT}; }}"
    f"QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {ACCENT_BORDER}; }}"
    f"QLineEdit:disabled {{ color: {INK_3}; background: {FIELD}; }}"
)

# Text roles.
TITLE_QSS = f"font-size: {FONT_BASE}px; font-weight: 600; color: {INK}; background: transparent; border: none;"
HEADLINE_QSS = f"font-size: {FONT_BASE + 5}px; font-weight: 600; color: {INK}; background: transparent; border: none;"
BODY_QSS = f"font-size: {FONT_BODY}px; color: {INK}; background: transparent; border: none;"
HINT_QSS = f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none;"
MICRO_QSS = (
    f"font-size: {FONT_MICRO}px; font-weight: 600; color: {INK_3};"
    " background: transparent; border: none;"
)

# A slim quiet scrollbar with no arrows. Set on the scroll area itself.
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

# Tooltips follow the panel instead of the OS yellow.
TOOLTIP_QSS = (
    f"QToolTip {{ background: {'#111214' if DARK else '#25272b'}; color: #f2f3f4;"
    f" border: 1px solid {'#2e3033' if DARK else '#3a3c40'}; border-radius: {RADIUS_CHIP}px;"
    f" padding: 4px 8px; font-size: {FONT_HINT}px; }}"
)


# Category hues (colour pass, 2026-09-17, AI Segmentation's values and helper
# names from its ``src/ui/dock/styles.py``). The panel was almost all grey, so
# a surface may carry ONE of these to say what kind of thing it is: an icon
# tile, a tip card, a gauge, an avatar. Each hue has a fill for dots and bars,
# an ink for words and glyphs (4.5:1 on the surface, the window and its own
# tint, both themes) and a tint for a ground. Meaning never rests on the
# colour alone, and the filled primary stays the only solid green block.
_CATEGORY_HUES = {
    #          fill       ink light  ink dark   rgb of the fill
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
    """The hue itself: a dot, a bar, a ring. Never a word."""
    return _CATEGORY_HUES.get(name, _CATEGORY_HUES["green"])[0]


def category_ink(name: str) -> str:
    """A word or a glyph in the hue, readable on the surface and on its tint."""
    _fill, light, dark, _rgb = _CATEGORY_HUES.get(name, _CATEGORY_HUES["green"])
    return dark if DARK else light


def category_tint(name: str, strong: bool = False) -> str:
    """A ground in the hue: a tile, a tip card. ``strong`` for hover or selected."""
    rgb = _CATEGORY_HUES.get(name, _CATEGORY_HUES["green"])[3]
    alpha = (0.26 if strong else 0.18) if DARK else (0.20 if strong else 0.12)
    return f"rgba({rgb}, {alpha})"


def category_line(name: str) -> str:
    """A soft border in the hue for a tinted card."""
    rgb = _CATEGORY_HUES.get(name, _CATEGORY_HUES["green"])[3]
    return f"rgba({rgb}, {0.40 if DARK else 0.35})"


def gauge_category(fraction_left: float) -> str:
    """The hue of a remaining-allowance bar: leaf, amber under 20 %, coral at 0."""
    if fraction_left <= 0:
        return "coral"
    return "amber" if fraction_left < 0.2 else "leaf"


# The generation bar (Yvann, 2026-09-18): 8 px instead of the 4 px thread,
# its fill sliding from the run's sky to the done green as it advances, the
# colours of AI Segmentation's run bar (auto_run_status.py).
PROGRESS_BAR_PX = 8
PROGRESS_HUE_START = "sky"
PROGRESS_HUE_DONE = "green"


def blend_hex_colors(start_hex: str, end_hex: str, t: float) -> str:
    """``start_hex`` at ``t=0`` to ``end_hex`` at ``t=1``, both ``#rrggbb``."""
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
    """The bar's fill at ``ratio`` done: sky, eased toward green over the run."""
    t = max(0.0, min(1.0, float(ratio)))
    t = t * t * (3.0 - 2.0 * t)
    return blend_hex_colors(
        category_ink(PROGRESS_HUE_START), category_ink(PROGRESS_HUE_DONE), t)


def progress_bar_qss(ratio: float = 0.0) -> str:
    """The generation bar's stylesheet at ``ratio`` done: a pill groove."""
    radius = PROGRESS_BAR_PX // 2
    return (
        f"QProgressBar {{ background: {FIELD}; border: none; border-radius: {radius}px;"
        f" max-height: {PROGRESS_BAR_PX}px; min-height: {PROGRESS_BAR_PX}px; text-align: center; }}"
        f"QProgressBar::chunk {{ background: {progress_fill_color(ratio)};"
        f" border-radius: {radius}px; }}"
    )


PROGRESS_QSS = progress_bar_qss(0.0)


# A picked option (shared with AI Segmentation, 2026-09-17): a soft tint of its
# hue, a thin border in the hue ink and a check (panel_helpers.add_picked_check).
# Never a solid fill.
PICKED_HUE = "green"


def picked_qss(selector: str, hue: str = PICKED_HUE) -> str:
    """The ``:checked`` rule of a picked option tile or chip."""
    ink = category_ink(hue)
    return (
        f"{selector}:checked {{ background: {category_tint(hue)};"
        f" border: 1px solid {ink}; color: {ink}; font-weight: 600; }}"
        f"{selector}:checked:hover {{ background: {category_tint(hue, strong=True)}; }}"
    )


def qcolor(token: str):
    """A token (``#rrggbb`` or ``rgba(r, g, b, a)``) as a QColor."""
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
    """Re-run the stylesheet after a dynamic property changed."""
    try:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
    except (RuntimeError, AttributeError):
        pass  # the widget was deleted before the repolish
