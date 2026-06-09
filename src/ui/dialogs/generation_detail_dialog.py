"""Detail popup for a prompt-library card.

Opened when the user clicks an inline card (a curated template OR one of their
past generations). Mirrors the dashboard detail view: a large before/after
slider on the left (expandable to fullscreen), and an info panel on the right
with the full prompt, the reference image(s), generation metadata, and the
actions that fit the card type.

Two modes, chosen by what the caller passes:
  - ``preset`` (curated template): demo slider + prompt + "Use this prompt".
  - ``job`` (past generation): real before/after + reference thumbnails +
    resolution/ratio/duration/date + Use (full restore) / Add to map /
    Download input+output / favorite.

The dialog never applies anything itself. It records an outcome the parent
library dialog reads after ``exec()`` (so nested modal event loops stay sane),
and forwards add-to-map / download / favorite through callbacks. Confidential
details (provider names, system preprompt, internal URLs) are never shown.
"""
from __future__ import annotations

import os

try:  # SIP ships with both PyQt5 and PyQt6 - used to detect dead C++ objects.
    from qgis.PyQt import sip as _sip
except ImportError:  # pragma: no cover - defensive only
    _sip = None

from qgis.PyQt.QtCore import QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core.date_format import format_smart_date
from ...core.i18n import tr
from ...core.logger import log_warning
from ...core.prompts import prompt_history
from ...core.prompts.hex_highlight import prompt_to_hex_html
from ...core.prompts.prompt_presets import format_template_prompt, lookup_template_by_prompt
from ..before_after_slider import BeforeAfterSlider

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
_ICONS_DIR = os.path.join(_PLUGIN_DIR, "resources", "icons")
_STAR_OUTLINE_SVG = os.path.join(_ICONS_DIR, "star.svg")
_STAR_FILLED_SVG = os.path.join(_ICONS_DIR, "star-filled.svg")
_DOWNLOAD_SVG = os.path.join(_ICONS_DIR, "download.svg")
_COPY_SVG = os.path.join(_ICONS_DIR, "copy.svg")


def _with_preview_size(url: str) -> str:
    """Append the size=preview query so the demo route serves the 2048px variant
    (falls back server-side to the base demo when no preview is seeded)."""
    if not url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}size=preview"


_TITLE_STYLE = (
    "color: palette(text); font-size: 18px; font-weight: 800; "
    "letter-spacing: -0.2px; background: transparent; border: none;"
)
_SECTION_STYLE = (
    "color: rgba(128,128,128,0.95); font-size: 10px; font-weight: 700; "
    "letter-spacing: 1.2px; background: transparent; border: none;"
)
# Type/category tag above the title. Brand-green tint, hugs its content.
_BADGE_STYLE = (
    "QLabel { color: #6f8c1e; background: rgba(139,172,39,0.13); "
    "border: 1px solid rgba(139,172,39,0.40); border-radius: 9px; "
    "font-size: 10px; font-weight: 800; letter-spacing: 1.0px; "
    "padding: 2px 9px; }"
)
_SEPARATOR = "background: rgba(128,128,128,0.20); border: none;"
_PROMPT_STYLE = (
    "QLabel { color: palette(text); font-size: 12px; "
    "background: rgba(128,128,128,0.05); border: 1px solid rgba(128,128,128,0.15); "
    "border-radius: 4px; padding: 8px 10px; }"
)
# Tiny flat "Copy" affordance sitting on the PROMPT section header.
_COPY_BTN = (
    "QPushButton { background: transparent; border: none; "
    "color: rgba(128,128,128,0.95); font-size: 11px; font-weight: 600; "
    "padding: 1px 6px; border-radius: 4px; }"
    "QPushButton:hover { background: rgba(128,128,128,0.14); color: palette(text); }"
)
_CHIP_STYLE = (
    "QFrame { background: rgba(128,128,128,0.06); "
    "border: 1px solid rgba(128,128,128,0.15); border-radius: 4px; }"
)
_CHIP_CAPTION = (
    "color: rgba(128,128,128,0.95); font-size: 9px; font-weight: 600; "
    "letter-spacing: 0.5px; background: transparent; border: none;"
)
_CHIP_VALUE = (
    "color: palette(text); font-size: 12px; font-weight: 600; "
    "background: transparent; border: none;"
)
_ACTION_BTN = (
    "QPushButton { background: transparent; border: 1px solid rgba(128,128,128,0.35); "
    "border-radius: 4px; padding: 7px 12px; font-size: 12px; color: palette(text); }"
    "QPushButton:hover { background: rgba(128,128,128,0.12); "
    "border-color: rgba(128,128,128,0.55); }"
    "QPushButton:disabled { color: rgba(128,128,128,0.5); "
    "border-color: rgba(128,128,128,0.15); }"
)
_PRIMARY_BTN = (
    "QPushButton { background: #8bac27; border: none; border-radius: 4px; "
    "padding: 8px 14px; font-size: 12px; font-weight: 600; color: #14210A; }"
    "QPushButton:hover { background: #76a32a; }"
    "QPushButton:disabled { background: rgba(128,128,128,0.25); color: rgba(128,128,128,0.6); }"
)
_FS_BTN = (
    "QToolButton { background: rgba(0,0,0,0.55); color: white; border: none; "
    "border-radius: 15px; font-size: 15px; }"
    "QToolButton:hover { background: rgba(0,0,0,0.8); }"
)
_REF_THUMB = (
    "QLabel { border: 1px solid rgba(128,128,128,0.3); border-radius: 4px; "
    "background: rgba(128,128,128,0.06); }"
)


class _AspectBox(QWidget):
    """Keeps its single child at a fixed width:height ratio, centered. The
    before/after slider draws cover-fit, so matching the box ratio to the
    image ratio shows the whole image with no crop (portrait or landscape)."""

    def __init__(self, child: QWidget, ratio: float, parent=None):
        super().__init__(parent)
        self._child = child
        child.setParent(self)
        self._ratio = ratio if ratio and ratio > 0 else 1.0
        self._overlay: QWidget | None = None
        self._overlay_margin = 10

    def set_ratio(self, ratio: float) -> None:
        self._ratio = ratio if ratio and ratio > 0 else 1.0
        self._relayout()

    def set_overlay(self, widget: QWidget) -> None:
        """Float ``widget`` over the bottom-right corner of the image rect."""
        self._overlay = widget
        widget.setParent(self)
        widget.raise_()
        self._relayout()

    def resizeEvent(self, event):  # noqa: N802 - Qt signature
        self._relayout()
        super().resizeEvent(event)

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if w / h > self._ratio:
            ch = h
            cw = int(round(h * self._ratio))
        else:
            cw = w
            ch = int(round(w / self._ratio))
        cx, cy = (w - cw) // 2, (h - ch) // 2
        self._child.setGeometry(cx, cy, cw, ch)
        if self._overlay is not None:
            ow = self._overlay.width()
            oh = self._overlay.height()
            m = self._overlay_margin
            self._overlay.move(cx + cw - ow - m, cy + ch - oh - m)
            self._overlay.raise_()


_REF_OVERLAY_BTN = (
    "QToolButton { background: rgba(255,255,255,0.92); border: none; "
    "border-radius: 5px; font-size: 13px; color: #14210A; }"
    "QToolButton:hover { background: #ffffff; }"
)


class _RefThumb(QWidget):
    """Reference-image thumbnail. Hover shows open / download controls; the
    whole tile opens the image large on click."""

    def __init__(self, index: int, on_open, on_download, parent=None):
        super().__init__(parent)
        self._index = index
        self._on_open = on_open
        self._full_pm: QPixmap | None = None
        self.setFixedSize(72, 72)
        self.setCursor(QtC.PointingHandCursor)
        self.setToolTip(tr("Click to open. Hover to download."))

        self._img = QLabel(self)
        self._img.setGeometry(0, 0, 72, 72)
        self._img.setAlignment(QtC.AlignCenter)
        self._img.setStyleSheet(_REF_THUMB)

        self._overlay = QWidget(self)
        self._overlay.setGeometry(0, 0, 72, 72)
        self._overlay.setStyleSheet("background: rgba(20,24,15,0.45); border-radius: 4px;")
        ov = QHBoxLayout(self._overlay)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(6)
        ov.addStretch(1)
        open_b = QToolButton(self._overlay)
        open_b.setText("⤢")
        open_b.setFixedSize(26, 26)
        open_b.setCursor(QtC.PointingHandCursor)
        open_b.setToolTip(tr("Open large"))
        open_b.setStyleSheet(_REF_OVERLAY_BTN)
        open_b.clicked.connect(lambda: self._on_open(self._index))
        ov.addWidget(open_b)
        dl_b = QToolButton(self._overlay)
        dl_b.setIcon(QIcon(_DOWNLOAD_SVG))
        dl_b.setFixedSize(26, 26)
        dl_b.setCursor(QtC.PointingHandCursor)
        dl_b.setToolTip(tr("Download original"))
        dl_b.setStyleSheet(_REF_OVERLAY_BTN)
        dl_b.clicked.connect(lambda: on_download(self._index))
        ov.addWidget(dl_b)
        ov.addStretch(1)
        self._overlay.hide()

    def set_pixmap(self, pm: QPixmap) -> None:
        self._full_pm = pm
        self._img.setPixmap(
            pm.scaled(70, 70, QtC.KeepAspectRatio, QtC.SmoothTransformation)
        )

    def full_pixmap(self) -> QPixmap | None:
        return self._full_pm

    def enterEvent(self, event):  # noqa: N802 - Qt signature
        if self._full_pm is not None:
            self._overlay.show()
            self._overlay.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt signature
        self._overlay.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802 - Qt signature
        if event.button() == QtC.LeftButton and self._full_pm is not None:
            self._on_open(self._index)
        super().mousePressEvent(event)


class _ImageLightbox(QDialog):
    """Simple large-image viewer with a Download button, used for references."""

    def __init__(self, pixmap: QPixmap, title: str, on_download=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title or tr("Reference image"))
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        img = QLabel(self)
        img.setAlignment(QtC.AlignCenter)
        shown = pixmap
        if pixmap.width() > 1100 or pixmap.height() > 800:
            shown = pixmap.scaled(
                1100, 800, QtC.KeepAspectRatio, QtC.SmoothTransformation
            )
        img.setPixmap(shown)
        v.addWidget(img, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        if on_download is not None:
            dl = QPushButton(tr("Download original"))
            dl.setIcon(QIcon(_DOWNLOAD_SVG))
            dl.setStyleSheet(_ACTION_BTN)
            dl.setCursor(QtC.PointingHandCursor)
            dl.clicked.connect(on_download)
            row.addWidget(dl)
        close = QPushButton(tr("Close"))
        close.setStyleSheet(_ACTION_BTN)
        close.setCursor(QtC.PointingHandCursor)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)


class GenerationDetailDialog(QDialog):
    """Larger detail view for a template or a past generation."""

    # request_id, now_favorited - relayed from the inline star so the library
    # keeps its Favorites list and the server in sync.
    favorite_toggled = pyqtSignal(str, bool)
    # prompt, now_favorited, label, source_category - emitted when a template's
    # favorite is toggled from this popup (favoriting moved out of the grid).
    prompt_favorite_toggled = pyqtSignal(str, bool, str, str)

    def __init__(
        self,
        parent=None,
        *,
        job: dict | None = None,
        preset: dict | None = None,
        client=None,
        demo_loader=None,
        absolute_url=None,
        on_action=None,
        on_favorite=None,
        browse_only: bool = False,
    ):
        super().__init__(parent)
        self._job = job
        self._preset = preset
        self._is_generation = job is not None
        self._client = client
        self._demo_loader = demo_loader
        self._absolute_url = absolute_url
        self._on_action = on_action
        self._on_favorite = on_favorite
        self._browse_only = browse_only
        self._outcome: str | None = None  # None | "use" | "close"
        self._fullscreen = False
        self._is_favorite = bool((job or {}).get("is_favorite"))

        src = job or preset or {}
        self._title_text = self._resolve_title(src)
        self.setWindowTitle(self._title_text or tr("Details"))
        self.setMinimumSize(560, 420)
        self.setSizeGripEnabled(True)

        # Image aspect ratio (w/h) drives the slider shape + the window size, so
        # a portrait generation opens portrait and a wide one opens wide. For a
        # template the dimensions are unknown until the demo loads, so we adopt
        # the loaded pixmap's ratio then (``_aspect_locked`` guards that).
        self._aspect_locked = False
        self._aspect = self._compute_aspect()
        self._aspect_box: _AspectBox | None = None

        # Image cache keys. The card grid caches the small thumbnail under the
        # request_id; the popup reuses that for an instant first paint, then
        # upgrades to the full image under a distinct "_full" key so the two
        # sizes never collide in the shared on-disk cache.
        if self._is_generation:
            self._thumb_key = str(job.get("request_id") or "")
            self._full_key = self._thumb_key + "_full"
        else:
            self._thumb_key = str((preset or {}).get("id") or "")
            self._full_key = self._thumb_key + "_preview"
        self._full_done: set[str] = set()

        self._loader_hooked = False
        self._has_images = self._build_ui()
        if self._has_images:
            self._apply_image_size()
        else:
            self.resize(520, 560)
        self._start_image_loads()
        # Drop the shared loader connections when the dialog closes so a late
        # network reply never paints into a destroyed dialog (Qt6 crash guard).
        self.finished.connect(self._cleanup_loader)

    def _compute_aspect(self) -> float:
        if self._is_generation:
            w, h = self._job.get("output_w"), self._job.get("output_h")
            if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
                self._aspect_locked = True
                return w / h
            ar = str(self._job.get("aspect_ratio") or "")
            if ":" in ar:
                try:
                    a, b = ar.split(":")
                    fa, fb = float(a), float(b)
                    if fa > 0 and fb > 0:
                        self._aspect_locked = True
                        return fa / fb
                except ValueError:
                    pass
        return 1.0

    def _apply_image_size(self) -> None:
        """Size the window so the slider area matches the image aspect (so the
        whole generation is visible without cropping or letterboxing)."""
        ar = self._aspect if self._aspect > 0 else 1.0
        # A touch wider than the pane's 330 minimum so a typical prompt opens
        # with breathing room; the user can still widen further (up to 560).
        info_w = 380
        disp_h = 600.0
        disp_w = disp_h * ar
        max_w = 900.0
        if disp_w > max_w:
            disp_w = max_w
            disp_h = disp_w / ar
        if disp_h < 380.0:
            disp_h = 380.0
            disp_w = min(disp_h * ar, max_w)
        # 12px spacing between panes + 12px margins on each side. The image now
        # spans the full pane height (the toolbar row moved onto the image).
        width = int(disp_w) + info_w + 12 + 24
        height = int(disp_h) + 24
        self.resize(width, height)

    # -- public --------------------------------------------------------------

    def outcome(self) -> str | None:
        """``"use"`` (apply the payload), ``"close"`` (just close the library),
        or None (do nothing). Read by the library dialog after exec()."""
        return self._outcome

    def payload(self) -> dict | None:
        return self._job if self._is_generation else self._preset

    # -- build ---------------------------------------------------------------

    def _resolve_title(self, src: dict) -> str:
        if not self._is_generation:
            return str(src.get("label") or "").strip() or tr("Template")
        # A generation is "a template" only when its prompt still matches one
        # verbatim; an edited template reads as a custom prompt and titles off
        # the prompt text instead of the (stale) stored template name.
        match = lookup_template_by_prompt(str(src.get("prompt") or ""))
        if match and match[1]:
            return match[1]
        prompt = " ".join(str(src.get("prompt") or "").split())
        if not prompt:
            return tr("Generation")
        return prompt[:59].rstrip() + "…" if len(prompt) > 60 else prompt

    def _build_ui(self) -> bool:
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        has_images = self._image_sources_present()

        # --- left: slider --------------------------------------------------
        if has_images:
            # No auto-loop: the divider sits parked at the middle and the user
            # drags it themselves (calmer, and the user asked for this). The
            # TEMPLATE tag already tells the user this is a sample, so no extra
            # "Example" badge over the image.
            self._slider = BeforeAfterSlider(
                None, auto_loop=False, show_badges=True, example_badge=None
            )
            # The aspect box keeps the slider at the image ratio (portrait stays
            # portrait), centered, growing with the window.
            self._aspect_box = _AspectBox(self._slider, self._aspect, self)
            self._aspect_box.setMinimumSize(260, 240)
            self._aspect_box.setSizePolicy(
                QtC.SizePolicyExpanding, QtC.SizePolicyExpanding
            )
            # Fullscreen toggle floats over the image's bottom-right corner,
            # clear of the BEFORE / Example / AFTER badges along the top edge.
            self._fs_btn = QToolButton(self._aspect_box)
            self._fs_btn.setText("⤢")  # diagonal expand glyph
            self._fs_btn.setToolTip(tr("Fullscreen"))
            self._fs_btn.setCursor(QtC.PointingHandCursor)
            self._fs_btn.setStyleSheet(_FS_BTN)
            self._fs_btn.setFixedSize(30, 30)
            self._fs_btn.clicked.connect(self._toggle_fullscreen)
            self._aspect_box.set_overlay(self._fs_btn)
            root.addWidget(self._aspect_box, 1)
        else:
            self._slider = None
            self._fs_btn = None

        # --- right: info panel ---------------------------------------------
        # The scrollable content (badge, title, prompt, references, metadata)
        # can run tall for a long prompt; the action footer (download + favorite
        # + reuse) is pinned below it OUTSIDE the scroll, so those buttons stay
        # visible no matter how long the prompt is. The pane is flexible-width:
        # widening the window gives the prompt more room (fewer wrapped lines,
        # less scrolling) up to a cap that keeps the image useful.
        right = QWidget(self)
        right_col = QVBoxLayout(right)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(10)
        if has_images:
            right.setMinimumWidth(330)
            right.setMaximumWidth(560)
        self._info_panel = right

        info_scroll = QScrollArea(right)
        info_scroll.setWidgetResizable(True)
        info_scroll.setFrameShape(QtC.FrameNoFrame)
        info_scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)

        info = QWidget()
        info.setMinimumWidth(300)
        col = QVBoxLayout(info)
        col.setContentsMargins(4, 2, 10, 2)
        col.setSpacing(12)

        # Header: a small type tag, then the title. The tag adds context and
        # anchors the top of the panel so the title doesn't float alone.
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge = QLabel(self._badge_text().upper())
        badge.setStyleSheet(_BADGE_STYLE)
        badge_row.addWidget(badge)
        badge_row.addStretch(1)
        col.addLayout(badge_row)

        title = QLabel(self._title_text)
        title.setWordWrap(True)
        title.setTextFormat(QtC.PlainText)
        title.setStyleSheet(_TITLE_STYLE)
        col.addWidget(title)

        col.addWidget(self._build_prompt_block())

        if self._is_generation:
            refs = self._job.get("reference_image_urls") or []
            if refs:
                col.addWidget(self._section_label(tr("Reference images")))
                col.addWidget(self._build_reference_row(refs))
            meta = self._build_meta_block()
            if meta is not None:
                col.addWidget(meta)

        col.addStretch(1)
        info_scroll.setWidget(info)
        right_col.addWidget(info_scroll, 1)

        # Pinned footer: a separator, the downloads (generations only), then the
        # favorite + primary action. Always on screen, never scrolled away.
        footer = QWidget(right)
        footer_col = QVBoxLayout(footer)
        footer_col.setContentsMargins(4, 0, 10, 2)
        footer_col.setSpacing(10)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(_SEPARATOR)
        footer_col.addWidget(sep)
        if self._is_generation:
            dl = self._build_download_group()
            if dl is not None:
                footer_col.addWidget(dl)
        footer_col.addLayout(self._build_actions())
        right_col.addWidget(footer, 0)

        root.addWidget(right, 1)
        return has_images

    def _badge_text(self) -> str:
        if self._is_generation:
            return tr("Your result")
        src = self._preset or {}
        return (
            str(src.get("category_label") or src.get("category") or "").strip() or
            tr("Template")
        )

    def _image_sources_present(self) -> bool:
        if self._is_generation:
            return bool(self._job.get("input_url") or self._job.get("output_url"))
        has_url = bool(
            (self._preset or {}).get("demo_url_before")
            or (self._preset or {}).get("demo_url_after")  # noqa: W503
        )
        return bool(has_url and self._demo_loader is not None)

    def _section_label(self, text: str) -> QLabel:
        # Uppercase here, not in tr(), so translation keys stay natural-case
        # (QSS has no text-transform).
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(_SECTION_STYLE)
        return lbl

    def _build_prompt_block(self) -> QWidget:
        src = self._job or self._preset or {}
        prompt = str(src.get("prompt") or "")
        self._prompt_text = prompt
        self._copy_btn = None

        wrap = QWidget(self)
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        # Header: section label on the left, a tiny one-click "Copy" on the
        # right so the user can reuse a past prompt without selecting it by hand.
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        header.addWidget(self._section_label(tr("Prompt")))
        header.addStretch(1)
        if prompt.strip():
            btn = QPushButton(tr("Copy"))
            btn.setIcon(QIcon(_COPY_SVG))
            btn.setIconSize(QSize(13, 13))
            btn.setCursor(QtC.PointingHandCursor)
            btn.setFlat(True)
            btn.setToolTip(tr("Copy prompt"))
            btn.setStyleSheet(_COPY_BTN)
            btn.clicked.connect(self._on_copy_prompt)
            self._copy_btn = btn
            header.addWidget(btn)
        v.addLayout(header)

        body = QLabel(prompt_to_hex_html(format_template_prompt(prompt)))
        body.setWordWrap(True)
        body.setTextFormat(QtC.RichText)
        body.setTextInteractionFlags(QtC.TextSelectableByMouse)
        body.setStyleSheet(_PROMPT_STYLE)
        v.addWidget(body)
        return wrap

    def _on_copy_prompt(self) -> None:
        """Copy the prompt to the clipboard and flash a brief 'Copied' state."""
        text = getattr(self, "_prompt_text", "") or ""
        if not text:
            return
        from qgis.PyQt.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        if self._copy_btn is not None:
            self._copy_btn.setText(tr("Copied"))
            QTimer.singleShot(1400, self._reset_copy_btn)

    def _reset_copy_btn(self) -> None:
        btn = getattr(self, "_copy_btn", None)
        if btn is None:
            return
        if _sip is not None and _sip.isdeleted(btn):
            return
        btn.setText(tr("Copy"))

    def _build_reference_row(self, urls: list) -> QWidget:
        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._ref_labels: dict[str, _RefThumb] = {}
        for i, _url in enumerate(urls):
            thumb = _RefThumb(i, self._open_reference, self._download_reference, host)
            row.addWidget(thumb)
            self._ref_labels[f"ref{i}"] = thumb
        row.addStretch(1)
        return host

    def _chip(self, caption: str, value: str) -> QFrame:
        chip = QFrame(self)
        chip.setStyleSheet(_CHIP_STYLE)
        v = QVBoxLayout(chip)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        cap = QLabel(caption)
        cap.setStyleSheet(_CHIP_CAPTION)
        val = QLabel(value)
        val.setWordWrap(True)
        val.setTextFormat(QtC.PlainText)
        val.setStyleSheet(_CHIP_VALUE)
        v.addWidget(cap)
        v.addWidget(val)
        return chip

    def _build_meta_block(self) -> QWidget | None:
        job = self._job
        chips: list[tuple[str, str]] = []

        res = str(job.get("resolution") or "").strip()
        w, h = job.get("output_w"), job.get("output_h")
        if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
            dims = f"{w}×{h}"
            chips.append((tr("RESOLUTION"), f"{dims} · {res}" if res else dims))
        elif res:
            chips.append((tr("RESOLUTION"), res))

        dur = job.get("duration_ms")
        if isinstance(dur, (int, float)) and dur > 0:
            chips.append((tr("DURATION"), f"{dur / 1000.0:.1f}s"))

        date_text = format_smart_date(job.get("created_at") or "")
        if date_text:
            chips.append((tr("DATE"), date_text))

        if not chips:
            return None

        host = QWidget(self)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for idx, (cap, val) in enumerate(chips):
            r, c = divmod(idx, 3)
            grid.addWidget(self._chip(cap, val), r, c)
        return host

    def _build_actions(self):
        if not self._is_generation:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            # Favoriting a template lives here now (removed from the grid cards).
            self._prompt_star_btn = QToolButton(self)
            self._prompt_star_btn.setIconSize(QSize(18, 18))
            self._prompt_star_btn.setFixedSize(38, 38)
            self._prompt_star_btn.setCursor(QtC.PointingHandCursor)
            self._prompt_star_btn.setStyleSheet(
                "QToolButton { background: transparent; border: 1px solid "
                "rgba(128,128,128,0.35); border-radius: 4px; }"
                "QToolButton:hover { background: rgba(128,128,128,0.15); }"
            )
            self._prompt_is_favorite = prompt_history.is_favorite(
                str((self._preset or {}).get("prompt") or "")
            )
            self._refresh_prompt_star()
            self._prompt_star_btn.clicked.connect(self._on_prompt_star)
            row.addWidget(self._prompt_star_btn)

            use_btn = QPushButton(tr("Use this prompt"))
            use_btn.setStyleSheet(_PRIMARY_BTN)
            use_btn.setMinimumHeight(38)
            use_btn.setCursor(QtC.PointingHandCursor)
            use_btn.setEnabled(not self._browse_only)
            use_btn.clicked.connect(self._on_use)
            row.addWidget(use_btn, 1)  # full-width footer button
            return row

        # Footer = favorite + reuse. (Downloads live in their own group above;
        # there is no "add to map" because downloading already lets the user
        # bring the result into QGIS themselves.)
        can_apply = not self._browse_only and _job_has_location(self._job)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._star_btn = QToolButton(self)
        self._star_btn.setIconSize(QSize(18, 18))
        self._star_btn.setFixedSize(38, 38)
        self._star_btn.setCursor(QtC.PointingHandCursor)
        self._star_btn.setStyleSheet(
            "QToolButton { background: transparent; border: 1px solid "
            "rgba(128,128,128,0.35); border-radius: 4px; }"
            "QToolButton:hover { background: rgba(128,128,128,0.15); }"
        )
        self._refresh_star()
        self._star_btn.clicked.connect(self._on_star)
        row.addWidget(self._star_btn)

        use_btn = QPushButton(tr("Reuse this setup"))
        use_btn.setStyleSheet(_PRIMARY_BTN)
        use_btn.setMinimumHeight(38)
        use_btn.setCursor(QtC.PointingHandCursor)
        use_btn.setToolTip(
            tr("Load this prompt, its reference images, and the same map zone "
               "back into AI Edit, replacing what you have now.")
        )
        use_btn.setEnabled(can_apply)
        use_btn.clicked.connect(self._on_use)
        row.addWidget(use_btn, 1)
        return row

    def _build_download_group(self) -> QWidget | None:
        """Download the input and the AI result as georeferenced GeoTIFFs, with
        a clear heading and download icons so the purpose is obvious."""
        has_in = bool(self._job.get("input_url"))
        has_out = bool(self._job.get("output_url"))
        if not has_in and not has_out:
            return None
        host = QWidget(self)
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(self._section_label(tr("Download")))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        icon = QIcon(_DOWNLOAD_SVG)

        dl_in = QPushButton(tr("Input image"))
        dl_in.setIcon(icon)
        dl_in.setStyleSheet(_ACTION_BTN)
        dl_in.setCursor(QtC.PointingHandCursor)
        dl_in.setToolTip(tr("Download the original input as a georeferenced GeoTIFF (.tif)"))
        dl_in.setEnabled(has_in)
        dl_in.clicked.connect(lambda: self._on_download("input"))
        row.addWidget(dl_in, 1)

        dl_out = QPushButton(tr("AI result"))
        dl_out.setIcon(icon)
        dl_out.setStyleSheet(_ACTION_BTN)
        dl_out.setCursor(QtC.PointingHandCursor)
        dl_out.setToolTip(tr("Download the AI result as a georeferenced GeoTIFF (.tif)"))
        dl_out.setEnabled(has_out)
        dl_out.clicked.connect(lambda: self._on_download("output"))
        row.addWidget(dl_out, 1)
        v.addLayout(row)
        return host

    # -- image loading -------------------------------------------------------

    def _start_image_loads(self) -> None:
        if self._slider is not None:
            if self._is_generation:
                self._load_generation_images()
            else:
                self._load_template_images()
        if self._is_generation and getattr(self, "_ref_labels", None):
            self._load_reference_thumbs()

    def _load_generation_images(self) -> None:
        if self._demo_loader is None or not self._thumb_key:
            return
        self._demo_loader.loaded.connect(self._on_image_loaded)
        # Thumb first (instant - the card already cached it), then the 2048px
        # preview upgrades it (sharp on any screen, fast on slow links). Each
        # tier falls back to the full URL on rows that predate it. The full
        # original stays reserved for the GeoTIFF download.
        thumb_before = self._job.get("input_thumb_url") or self._job.get("input_url")
        thumb_after = self._job.get("output_thumb_url") or self._job.get("output_url")
        full_before = self._job.get("input_preview_url") or self._job.get("input_url")
        full_after = self._job.get("output_preview_url") or self._job.get("output_url")
        if thumb_before:
            self._demo_loader.request(self._thumb_key, "before", thumb_before)
        if thumb_after:
            self._demo_loader.request(self._thumb_key, "after", thumb_after)
        if full_before:
            self._demo_loader.request(self._full_key, "before", full_before)
        if full_after:
            self._demo_loader.request(self._full_key, "after", full_after)

    def _load_template_images(self) -> None:
        if self._demo_loader is None or self._absolute_url is None:
            return
        self._demo_loader.loaded.connect(self._on_image_loaded)
        ub = self._preset.get("demo_url_before")
        ua = self._preset.get("demo_url_after")
        # Base demo (640px, often already grid-cached) for an instant first
        # paint, then the 2048px preview upgrades it under the _preview key.
        if ub:
            self._demo_loader.request(self._thumb_key, "before", self._absolute_url(ub))
            self._demo_loader.request(
                self._full_key, "before", self._absolute_url(_with_preview_size(ub))
            )
        if ua:
            self._demo_loader.request(self._thumb_key, "after", self._absolute_url(ua))
            self._demo_loader.request(
                self._full_key, "after", self._absolute_url(_with_preview_size(ua))
            )

    def _load_reference_thumbs(self) -> None:
        if self._demo_loader is None:
            return
        rid = str(self._job.get("request_id") or "")
        urls = self._job.get("reference_image_urls") or []
        if not rid or not urls:
            return
        self._demo_loader.loaded.connect(self._on_ref_loaded)
        for i, url in enumerate(urls):
            if url:
                self._demo_loader.request(rid, f"ref{i}", url)

    def _on_image_loaded(self, key: str, which: str, pixmap) -> None:
        if self._slider is None or which not in ("before", "after"):
            return
        is_full = key == self._full_key
        is_thumb = key == self._thumb_key
        if not (is_full or is_thumb):
            return
        # A thumb must never overwrite the full image once it has arrived.
        if is_thumb and which in self._full_done:
            return
        if is_full:
            self._full_done.add(which)
        if which == "before":
            self._slider.set_before(pixmap)
        else:
            self._slider.set_after(pixmap)
        # Prefer the full image's true dimensions for the window aspect.
        if is_full or not self._aspect_locked:
            self._adopt_aspect(pixmap)

    def _adopt_aspect(self, pixmap) -> None:
        """Match the window + slider to the image aspect (used for templates and
        any generation lacking stored dimensions)."""
        if self._aspect_locked or pixmap is None or pixmap.isNull() or pixmap.height() <= 0:
            return
        self._aspect_locked = True
        self._aspect = pixmap.width() / pixmap.height()
        if self._aspect_box is not None:
            self._aspect_box.set_ratio(self._aspect)
        if not self._fullscreen:
            self._apply_image_size()

    def _on_ref_loaded(self, key: str, which: str, pixmap) -> None:
        if key != str(self._job.get("request_id") or ""):
            return
        thumb = getattr(self, "_ref_labels", {}).get(which)
        if thumb is None or pixmap is None or pixmap.isNull():
            return
        thumb.set_pixmap(pixmap)

    def _open_reference(self, index: int) -> None:
        thumb = getattr(self, "_ref_labels", {}).get(f"ref{index}")
        pm = thumb.full_pixmap() if thumb is not None else None
        if pm is None or pm.isNull():
            return
        box = _ImageLightbox(
            pm,
            tr("Reference image {n}").format(n=index + 1),
            on_download=lambda: self._download_reference(index),
            parent=self,
        )
        box.exec()
        box.deleteLater()

    def _download_reference(self, index: int) -> None:
        urls = self._job.get("reference_image_urls") or []
        if index >= len(urls) or not urls[index]:
            return
        from urllib.parse import urlparse

        url = urls[index]
        ext = os.path.splitext(urlparse(url).path)[1] or ".png"
        suggested = f"reference_{index + 1}{ext}"
        dest, _sel = QFileDialog.getSaveFileName(
            self, tr("Save reference image"), suggested
        )
        if not dest:
            return
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QApplication

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from qgis.core import QgsNetworkAccessManager
            from qgis.PyQt.QtCore import QUrl
            from qgis.PyQt.QtNetwork import QNetworkRequest

            request = QNetworkRequest(QUrl(url))
            # Bound the blocking call so a stalled link can't hang QGIS forever.
            request.setTransferTimeout(60_000)
            reply = QgsNetworkAccessManager.instance().blockingGet(request)
            data = bytes(reply.content())
            if not data:
                raise OSError("empty response")
            with open(dest, "wb") as f:
                f.write(data)
        except Exception as err:  # noqa: BLE001
            log_warning(f"Reference download failed: {err}")
            from qgis.PyQt.QtWidgets import QMessageBox

            QMessageBox.warning(
                self, tr("Download failed"),
                tr("Could not download the reference image."),
            )
        finally:
            QApplication.restoreOverrideCursor()

    # -- actions -------------------------------------------------------------

    def _on_use(self) -> None:
        self._outcome = "use"
        self.accept()

    def _on_download(self, side: str) -> None:
        if self._on_action and self._job:
            self._on_action(f"download_{side}", self._job)

    def _refresh_star(self) -> None:
        icon = _STAR_FILLED_SVG if self._is_favorite else _STAR_OUTLINE_SVG
        self._star_btn.setIcon(QIcon(icon))
        self._star_btn.setToolTip(
            tr("Remove from favorites") if self._is_favorite
            else tr("Add to favorites")
        )

    def _on_star(self) -> None:
        self._is_favorite = not self._is_favorite
        self._job["is_favorite"] = self._is_favorite
        self._refresh_star()
        rid = str(self._job.get("request_id") or "")
        if self._on_favorite and rid:
            self._on_favorite(rid, self._is_favorite)
        self.favorite_toggled.emit(rid, self._is_favorite)

    def _refresh_prompt_star(self) -> None:
        icon = _STAR_FILLED_SVG if self._prompt_is_favorite else _STAR_OUTLINE_SVG
        self._prompt_star_btn.setIcon(QIcon(icon))
        self._prompt_star_btn.setToolTip(
            tr("Remove from favorites") if self._prompt_is_favorite
            else tr("Add to favorites")
        )

    def _on_prompt_star(self) -> None:
        src = self._preset or {}
        prompt = str(src.get("prompt") or "")
        label = src.get("label")
        source_cat = src.get("source_category")
        # Mutate + record here (mirrors the old inline star); the parent picks up
        # the signal to sync the favorite to the server.
        self._prompt_is_favorite = prompt_history.toggle_favorite(
            prompt, label, source_cat
        )
        telemetry.track("favorite_toggled", {"now_favorited": self._prompt_is_favorite})
        telemetry.flush()
        self._refresh_prompt_star()
        self.prompt_favorite_toggled.emit(
            prompt, self._prompt_is_favorite, label or "", source_cat or ""
        )

    # -- fullscreen ----------------------------------------------------------

    def _toggle_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        self._info_panel.setVisible(not self._fullscreen)
        if self._fs_btn is not None:
            self._fs_btn.setText("⤡" if self._fullscreen else "⤢")
            self._fs_btn.setToolTip(
                tr("Exit fullscreen") if self._fullscreen else tr("Fullscreen")
            )
        # Maximize rather than true fullscreen: on macOS, showFullScreen() moves
        # the dialog to its own Space so the (modal) prompt library ends up
        # covering it. Maximized stays a normal window we can raise above the
        # library. raise_/activateWindow forces it to the front either way.
        if self._fullscreen:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _cleanup_loader(self) -> None:
        if self._demo_loader is None:
            return
        for slot in (self._on_image_loaded, self._on_ref_loaded):
            try:
                self._demo_loader.loaded.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def keyPressEvent(self, event):  # noqa: N802 - Qt signature
        if event.key() == Qt.Key.Key_Escape and self._fullscreen:
            self._toggle_fullscreen()
            return
        super().keyPressEvent(event)


def _job_has_location(job: dict) -> bool:
    """Cheap check for whether a generation can be re-georeferenced."""
    bbox = job.get("bbox")
    if job.get("crs_authid") and isinstance(bbox, dict) and all(
        k in bbox for k in ("xmin", "ymin", "xmax", "ymax")
    ):
        return True
    wgs = job.get("bbox_wgs84")
    return isinstance(wgs, dict) and all(
        k in wgs for k in ("west", "south", "east", "north")
    )
