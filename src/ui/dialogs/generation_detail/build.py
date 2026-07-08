
from __future__ import annotations

try:
    from qgis.PyQt import sip as _sip
except ImportError:  # pragma: no cover
    _sip = None

from qgis.PyQt.QtCore import QSize
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
from ....core.config_store import get_export_copy, get_export_dial
from ....core.date_format import format_smart_date
from ....core.i18n import tr
from ....core.number_format import format_count
from ....core.prompts import prompt_history
from ....core.prompts.hex_highlight import prompt_to_hex_html
from ....core.prompts.prompt_presets import format_template_prompt, lookup_template_by_prompt
from ....core.resolution_labels import resolution_display_label
from ...before_after_slider import BeforeAfterSlider
from ...dock.design_tokens import BTN_PRIMARY_WIDE_PX, INK, INK_2, SCROLL_AREA_QSS, qcolor
from ...icons import icon_for
from .styles import (
    _ACTION_BTN,
    _CHIP_CAPTION,
    _CHIP_STYLE,
    _CHIP_VALUE,
    _COPY_BTN,
    _DANGER_BTN,
    _DETAIL_BADGE_STYLE,
    _DETAIL_SECTION_STYLE,
    _DETAIL_TITLE_STYLE,
    _FS_BTN,
    _PRIMARY_BTN,
    _PROMPT_STYLE,
    _SEPARATOR,
    _STAR_BTN,
)
from .widgets import _AspectBox, _RefThumb


_COPY_RESET_MS = 1400


_DIALOG_MARGIN_PX = 16
_PANE_GAP_PX = 16


_TITLE_TRUNCATE_CHARS = 60


def _job_has_location(job: dict) -> bool:

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


    def _resolve_title(self, src: dict) -> str:


        if self._session is not None:
            title = " ".join(str(self._session.get("title") or "").split())
            if title:
                return title
        if not self._is_generation:
            return str(src.get("label") or "").strip() or get_export_copy(
                "dialogs.build.template_fallback", tr("Template"))



        match = lookup_template_by_prompt(str(src.get("prompt") or ""))
        if match and match[1]:
            return match[1]
        prompt = " ".join(str(src.get("prompt") or "").split())
        if not prompt:
            return get_export_copy("dialogs.build.generation_fallback", tr("Generation"))
        limit = get_export_dial("dialogs.build.title_truncate_chars", _TITLE_TRUNCATE_CHARS)
        if len(prompt) <= limit:
            return prompt

        cut = prompt[:limit - 1]
        if prompt[limit - 1] != " " and " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        return cut.rstrip(" ,.;:-") + "…"

    def _build_ui(self) -> bool:
        root = QHBoxLayout(self)
        root.setContentsMargins(
            _DIALOG_MARGIN_PX, _DIALOG_MARGIN_PX, _DIALOG_MARGIN_PX, _DIALOG_MARGIN_PX
        )
        root.setSpacing(_PANE_GAP_PX)

        has_images = self._image_sources_present()


        if has_images:




            self._slider = BeforeAfterSlider(
                None, auto_loop=False, show_badges=True, example_badge=None
            )


            self._aspect_box = _AspectBox(self._slider, self._aspect, self)
            self._aspect_box.setMinimumSize(260, 240)
            self._aspect_box.setSizePolicy(
                QtC.SizePolicyExpanding, QtC.SizePolicyExpanding
            )


            self._fs_btn = QToolButton(self._aspect_box)
            self._fs_btn.setIcon(icon_for(self, "expand", 16, qcolor(INK)))
            self._fs_btn.setIconSize(QSize(16, 16))
            self._fs_btn.setCheckable(True)
            self._fs_btn.setToolTip(
                get_export_copy("dialogs.dialog.fullscreen_tooltip", tr("Fullscreen")))
            self._fs_btn.setAccessibleName(self._fs_btn.toolTip())
            self._fs_btn.setCursor(QtC.PointingHandCursor)
            self._fs_btn.setStyleSheet(_FS_BTN)
            self._fs_btn.setFixedSize(30, 30)
            self._fs_btn.clicked.connect(self._toggle_fullscreen)
            self._aspect_box.set_overlay(self._fs_btn)
            root.addWidget(self._aspect_box, 1)
        else:
            self._slider = None
            self._fs_btn = None








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
        col.setSpacing(14)



        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge = QLabel(self._badge_text())
        badge.setStyleSheet(_DETAIL_BADGE_STYLE)
        badge_row.addWidget(badge)
        badge_row.addStretch(1)
        col.addLayout(badge_row)

        title = QLabel(self._title_text)
        title.setWordWrap(True)
        title.setTextFormat(QtC.PlainText)
        title.setStyleSheet(_DETAIL_TITLE_STYLE)
        col.addWidget(title)

        col.addWidget(self._build_prompt_block())

        if self._is_generation:
            refs = self._job.get("reference_image_urls") or []
            if refs:

                col.addWidget(self._section_label(get_export_copy(
                    "dialogs.build.references_label", tr("References"))))
                col.addWidget(self._build_reference_row(refs))
            meta = self._build_meta_block()
            if meta is not None:
                col.addWidget(meta)

        col.addStretch(1)
        info_scroll.setWidget(info)


        info_scroll.setStyleSheet(SCROLL_AREA_QSS)
        info_scroll.viewport().setAutoFillBackground(False)
        info.setAutoFillBackground(False)
        right_col.addWidget(info_scroll, 1)



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
        if self._session is not None:
            return get_export_copy("dialogs.build.session_badge", tr("Session"))
        if self._is_generation:
            return get_export_copy("dialogs.build.your_result_badge", tr("Your result"))
        src = self._preset or {}
        label = str(src.get("category_label") or src.get("category") or "").strip()
        return label or get_export_copy("dialogs.build.template_fallback", tr("Template"))

    def _image_sources_present(self) -> bool:
        if self._is_generation:
            return bool(self._job.get("input_url") or self._job.get("output_url"))
        src = self._preset or {}
        has_url = bool(src.get("demo_url_before") or src.get("demo_url_after"))
        return bool(has_url and self._demo_loader is not None)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_DETAIL_SECTION_STYLE)
        return lbl

    def _build_prompt_block(self) -> QWidget:
        src = self._job or self._preset or {}
        prompt = str(src.get("prompt") or "")
        self._prompt_text = prompt
        self._copy_btn = None

        wrap = QWidget(self)
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)



        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        header.addWidget(self._section_label(get_export_copy(
            "dialogs.build.prompt_section_label", tr("Prompt"))))
        header.addStretch(1)
        if prompt.strip():
            btn = QPushButton(get_export_copy("dialogs.build.copy_button", tr("Copy")))
            btn.setIcon(icon_for(self, "copy", 14, qcolor(INK_2)))
            btn.setIconSize(QSize(13, 13))
            btn.setCursor(QtC.PointingHandCursor)
            btn.setFlat(True)
            btn.setToolTip(get_export_copy("dialogs.build.copy_prompt_tooltip", tr("Copy prompt")))
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

        text = getattr(self, "_prompt_text", "") or ""
        if not text:
            return
        from qgis.PyQt.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        if self._copy_btn is not None:
            self._copy_btn.setText(get_export_copy("dialogs.build.copied_label", tr("Copied")))


            QtC.safe_single_shot(
                get_export_dial("dialogs.build.copy_reset_ms", _COPY_RESET_MS),
                self._copy_btn,
                self._reset_copy_btn,
            )

    def _reset_copy_btn(self) -> None:
        btn = getattr(self, "_copy_btn", None)
        if btn is None:
            return
        if _sip is not None and _sip.isdeleted(btn):
            return
        btn.setText(get_export_copy("dialogs.build.copy_button", tr("Copy")))

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
        chip.setObjectName("detailFactCard")
        chip.setAttribute(QtC.WA_StyledBackground, True)
        chip.setStyleSheet(_CHIP_STYLE)
        v = QVBoxLayout(chip)
        v.setContentsMargins(10, 8, 10, 8)
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


        if self._session is not None:

            count = int(self._session.get("count") or 1)
            chips.append((get_export_copy("dialogs.build.chip_versions", tr("Versions")), str(count)))



        res = str(job.get("resolution") or "").strip()
        if res:
            chips.append((
                get_export_copy("dialogs.build.chip_quality", tr("Quality")),
                resolution_display_label(res) or res,
            ))
        w, h = job.get("output_w"), job.get("output_h")
        if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:

            chips.append((
                get_export_copy("dialogs.build.chip_output_size", tr("Output size")),
                f"{format_count(w)} × {format_count(h)} px",
            ))

        dur = job.get("duration_ms")
        if isinstance(dur, (int, float)) and dur > 0:
            chips.append((get_export_copy("dialogs.build.chip_duration", tr("Duration")), f"{dur / 1000.0:.1f} s"))

        date_text = format_smart_date(job.get("created_at") or "")
        if date_text:
            chips.append((get_export_copy("dialogs.build.chip_date", tr("Date")), date_text))

        if not chips:
            return None

        host = QWidget(self)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)


        columns = 2 if len(chips) in (2, 4) else min(3, len(chips))
        for idx, (cap, val) in enumerate(chips):
            r, c = divmod(idx, columns)
            grid.addWidget(self._chip(cap, val), r, c)
        return host

    def _build_actions(self):
        if self._session is not None:
            return self._build_session_actions()
        if not self._is_generation:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            self._prompt_star_btn = QToolButton(self)
            self._prompt_star_btn.setIconSize(QSize(18, 18))
            self._prompt_star_btn.setFixedSize(BTN_PRIMARY_WIDE_PX, BTN_PRIMARY_WIDE_PX)
            self._prompt_star_btn.setCursor(QtC.PointingHandCursor)
            self._prompt_star_btn.setStyleSheet(_STAR_BTN)
            self._prompt_is_favorite = prompt_history.is_favorite(
                str((self._preset or {}).get("prompt") or "")
            )
            self._refresh_prompt_star()
            self._prompt_star_btn.clicked.connect(self._on_prompt_star)
            row.addWidget(self._prompt_star_btn)

            use_btn = QPushButton(get_export_copy("dialogs.build.use_prompt_button", tr("Use this prompt")))
            use_btn.setStyleSheet(_PRIMARY_BTN)
            use_btn.setMinimumHeight(BTN_PRIMARY_WIDE_PX)
            use_btn.setCursor(QtC.PointingHandCursor)
            use_btn.setEnabled(not self._browse_only)
            use_btn.clicked.connect(self._on_use)
            row.addWidget(use_btn, 1)
            return row




        can_apply = not self._browse_only and _job_has_location(self._job)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._star_btn = QToolButton(self)
        self._star_btn.setIconSize(QSize(18, 18))
        self._star_btn.setFixedSize(BTN_PRIMARY_WIDE_PX, BTN_PRIMARY_WIDE_PX)
        self._star_btn.setCursor(QtC.PointingHandCursor)
        self._star_btn.setStyleSheet(_STAR_BTN)
        self._refresh_star()
        self._star_btn.clicked.connect(self._on_star)
        row.addWidget(self._star_btn)

        use_btn = QPushButton(get_export_copy("dialogs.build.reuse_setup_button", tr("Reuse this setup")))
        use_btn.setStyleSheet(_PRIMARY_BTN)
        use_btn.setMinimumHeight(BTN_PRIMARY_WIDE_PX)
        use_btn.setCursor(QtC.PointingHandCursor)
        if can_apply:
            use_btn.setToolTip(
                get_export_copy(
                    "dialogs.build.reuse_setup_tooltip_v2",
                    tr("Load this prompt, its references and the same map zone "
                       "back into AI Edit, replacing what you have now."))
            )
        elif not self._browse_only:

            use_btn.setToolTip(get_export_copy(
                "dialogs.build.reuse_no_location_tooltip",
                tr("This result has no saved map zone, so it cannot be reused.")))
        use_btn.setEnabled(can_apply)
        use_btn.clicked.connect(self._on_use)
        row.addWidget(use_btn, 1)
        return row

    def _build_session_actions(self):




        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)


        if (self._session or {}).get("session_id"):
            rename_btn = QPushButton(get_export_copy("dialogs.build.rename_button", tr("Rename")))
            rename_btn.setStyleSheet(_ACTION_BTN)
            rename_btn.setCursor(QtC.PointingHandCursor)
            rename_btn.clicked.connect(self._on_rename_session)
            row.addWidget(rename_btn)

        delete_btn = QPushButton(get_export_copy("dialogs.build.delete_button", tr("Delete")))
        delete_btn.setStyleSheet(_DANGER_BTN)
        delete_btn.setCursor(QtC.PointingHandCursor)
        delete_btn.clicked.connect(self._on_delete_session)
        row.addWidget(delete_btn)

        resume_btn = QPushButton(
            get_export_copy("dialogs.build.resume_session_button", tr("Resume this session")))
        resume_btn.setStyleSheet(_PRIMARY_BTN)
        resume_btn.setMinimumHeight(BTN_PRIMARY_WIDE_PX)
        resume_btn.setCursor(QtC.PointingHandCursor)
        resume_btn.setToolTip(
            get_export_copy(
                "dialogs.build.resume_session_tooltip_v2",
                tr("Reopen this session in AI Edit: its prompt, references "
                   "and the same map zone."))
        )
        resume_btn.setEnabled(not self._browse_only)
        resume_btn.clicked.connect(self._on_resume_session)
        row.addWidget(resume_btn, 1)
        return row

    def _build_download_group(self) -> QWidget | None:


        has_in = bool(self._job.get("input_url"))
        has_out = bool(self._job.get("output_url"))
        if not has_in and not has_out:
            return None
        host = QWidget(self)
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(self._section_label(get_export_copy(
            "dialogs.build.download_section_label", tr("Download as GeoTIFF"))))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        icon = icon_for(self, "download", 16)

        dl_in = QPushButton(
            get_export_copy("dialogs.build.original_image_button", tr("Original"))
        )
        dl_in.setIcon(icon)
        dl_in.setStyleSheet(_ACTION_BTN)
        dl_in.setCursor(QtC.PointingHandCursor)
        dl_in.setToolTip(get_export_copy(
            "dialogs.build.download_original_tooltip",
            tr("Download Original as a georeferenced GeoTIFF (.tif)")))

        dl_in.setVisible(has_in)
        dl_in.clicked.connect(lambda: self._on_download("input"))
        row.addWidget(dl_in, 1)

        dl_out = QPushButton(get_export_copy("dialogs.build.ai_result_button", tr("AI result")))
        dl_out.setIcon(icon)
        dl_out.setStyleSheet(_ACTION_BTN)
        dl_out.setCursor(QtC.PointingHandCursor)
        dl_out.setToolTip(get_export_copy(
            "dialogs.build.ai_result_tooltip",
            tr("Download the AI result as a georeferenced GeoTIFF (.tif)")))
        dl_out.setVisible(has_out)
        dl_out.clicked.connect(lambda: self._on_download("output"))
        row.addWidget(dl_out, 1)
        v.addLayout(row)
        return host
