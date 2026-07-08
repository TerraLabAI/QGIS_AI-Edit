"""UI construction for the generation detail dialog (info panel, actions)."""
from __future__ import annotations

try:  # SIP ships with both PyQt5 and PyQt6 - used to detect dead C++ objects.
    from qgis.PyQt import sip as _sip
except ImportError:  # pragma: no cover - defensive only
    _sip = None

from qgis.PyQt.QtCore import QSize, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
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

from ....core import qt_compat as QtC
from ....core.date_format import format_smart_date
from ....core.i18n import tr
from ....core.prompts import prompt_history
from ....core.prompts.hex_highlight import prompt_to_hex_html
from ....core.prompts.prompt_presets import format_template_prompt, lookup_template_by_prompt
from ...before_after_slider import BeforeAfterSlider
from .styles import (
    _ACTION_BTN,
    _BADGE_STYLE,
    _CHIP_CAPTION,
    _CHIP_STYLE,
    _CHIP_VALUE,
    _COPY_BTN,
    _COPY_SVG,
    _DOWNLOAD_SVG,
    _FS_BTN,
    _PRIMARY_BTN,
    _PROMPT_STYLE,
    _SECTION_STYLE,
    _SEPARATOR,
    _TITLE_STYLE,
)
from .widgets import _AspectBox, _RefThumb


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


class BuildUiMixin:
    """Builds the dialog layout: slider pane, info panel, footer actions."""

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
        label = str(src.get("category_label") or src.get("category") or "").strip()
        return label or tr("Template")

    def _image_sources_present(self) -> bool:
        if self._is_generation:
            return bool(self._job.get("input_url") or self._job.get("output_url"))
        src = self._preset or {}
        has_url = bool(src.get("demo_url_before") or src.get("demo_url_after"))
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
