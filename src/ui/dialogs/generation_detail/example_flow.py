








from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QApplication,
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core import telemetry
from ....core import telemetry_events as te
from ....core.i18n import tr
from ....core.number_format import format_count
from ...dock.design_tokens import FONT_MICRO, INK_3, picked_qss
from .images import _with_preview_size
from .styles import _ACTION_BTN, _COPY_BTN, _PROMPT_STYLE


_LIMITS_STYLE = (
    f"color: {INK_3}; font-size: {FONT_MICRO}px;"
    " background: transparent; border: none;"
)


FLOW_STEP_BEFORE = "before"
FLOW_STEP_AI_EDIT = "ai_edit"
FLOW_STEP_VECTORIZED = "vectorized"


FLOW_ROW_EXTRA_PX = 44


class ExampleFlowMixin:




    def _example_info(self) -> dict | None:
        if self._is_generation:
            return None
        example = (self._preset or {}).get("example")
        return example if isinstance(example, dict) and example else None

    def _has_vector_step(self) -> bool:
        if self._is_generation or self._demo_loader is None:
            return False
        return bool((self._preset or {}).get("demo_url_vector"))

    def _example_zone_bbox(self) -> list | None:
        zone = (self._example_info() or {}).get("zone")
        bbox = zone.get("bbox_4326") if isinstance(zone, dict) else None
        return bbox if isinstance(bbox, list) and len(bbox) == 4 else None



    def _build_flow_steps(self) -> QWidget:
        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(6)
        self._flow_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(host)
        group.setExclusive(True)
        steps = (
            (FLOW_STEP_BEFORE, tr("Before")),
            (FLOW_STEP_AI_EDIT, tr("AI Edit")),
            (FLOW_STEP_VECTORIZED, tr("Vectorized")),
        )
        for key, text in steps:
            btn = QPushButton(text, host)
            btn.setCheckable(True)
            btn.setCursor(QtC.PointingHandCursor)
            btn.setStyleSheet(_ACTION_BTN + picked_qss("QPushButton"))
            btn.setChecked(key == self._flow_step)
            btn.clicked.connect(lambda _checked=False, k=key: self._on_flow_step(k))
            group.addButton(btn)
            row.addWidget(btn, 1)
            self._flow_buttons[key] = btn
        self._flow_group = group
        return host

    def _on_flow_step(self, step: str) -> None:
        if step == self._flow_step:
            return
        self._flow_step = step
        if step == FLOW_STEP_VECTORIZED:
            self._load_vector_image()
        self._apply_flow_step()
        telemetry.track(te.EXAMPLE_FLOW_VIEWED, {
            "template_id": str((self._preset or {}).get("id") or ""),
            "step": step,
        })

    def _load_vector_image(self) -> None:

        if getattr(self, "_vector_requested", False):
            return
        rel = (self._preset or {}).get("demo_url_vector")
        if not rel or self._demo_loader is None or self._absolute_url is None:
            return
        self._vector_requested = True
        self._demo_loader.request(self._thumb_key, "vector", self._absolute_url(rel))
        self._demo_loader.request(
            self._full_key, "vector", self._absolute_url(_with_preview_size(rel))
        )

    def _apply_flow_step(self) -> None:

        if self._slider is None:
            return
        pix = self._flow_pixmaps
        step = self._flow_step
        if step == FLOW_STEP_BEFORE:
            self._slider.set_before(pix.get("before"))
            self._slider.set_after(pix.get("before"))
            self._slider.set_badge_texts(tr("Before"), "")
        elif step == FLOW_STEP_VECTORIZED:
            self._slider.set_before(pix.get("after"))
            self._slider.set_after(pix.get("vector"))
            self._slider.set_badge_texts(tr("AI Edit"), tr("Vectorized"))
        else:
            self._slider.set_before(pix.get("before"))
            self._slider.set_after(pix.get("after"))
            self._slider.set_badge_texts(None, None)



    def _build_example_block(self) -> QWidget | None:
        example = self._example_info()
        if example is None:
            return None
        chips: list[tuple[str, str]] = []
        place = example.get("place")
        if place:
            chips.append((tr("Place"), str(place)))
        gsd = example.get("gsd_m")
        if gsd:
            chips.append((tr("Ground resolution"), tr("{n} m per pixel").format(n=f"{float(gsd):g}")))
        res = example.get("resolution")
        credits = example.get("credits")
        if res or credits is not None:
            parts = []
            if res:
                parts.append(str(res))
            if credits is not None:
                parts.append(tr("{n} credits").format(n=format_count(int(credits))))
            chips.append((tr("Resolution and credits"), ", ".join(parts)))
        vector_text = self._example_vector_text(example.get("vectorize"))
        if vector_text:
            chips.append((tr("Vectorized"), vector_text))
        chain = example.get("chain") if isinstance(example.get("chain"), dict) else {}
        chain_prompt = str(chain.get("prompt") or "")
        limits = str(example.get("limits") or "")
        if not chips and not chain_prompt and not limits:
            return None
        host = QWidget(self)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        if chips:
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(6)
            for idx, (cap, val) in enumerate(chips):
                r, c = divmod(idx, 2)
                grid.addWidget(self._chip(cap, val), r, c)
            col.addLayout(grid)
        self._chain_prompt_text = chain_prompt
        self._chain_copy_btn = None
        if chain_prompt:
            header = QHBoxLayout()
            header.setContentsMargins(0, 4, 0, 0)
            header.addWidget(self._section_label(tr("Next step for the vector layer")))
            header.addStretch(1)
            btn = QPushButton(tr("Copy"), host)
            btn.setFlat(True)
            btn.setCursor(QtC.PointingHandCursor)
            btn.setToolTip(tr("Copy the mask prompt"))
            btn.setStyleSheet(_COPY_BTN)
            btn.clicked.connect(self._on_copy_chain_prompt)
            self._chain_copy_btn = btn
            header.addWidget(btn)
            col.addLayout(header)
            body = QLabel(chain_prompt, host)
            body.setWordWrap(True)
            body.setTextFormat(QtC.PlainText)
            body.setTextInteractionFlags(QtC.TextSelectableByMouse)
            body.setStyleSheet(_PROMPT_STYLE)
            body.setObjectName("exampleChainPrompt")
            col.addWidget(body)
        if limits:
            line = QLabel(limits, host)
            line.setWordWrap(True)
            line.setTextFormat(QtC.PlainText)
            line.setStyleSheet(_LIMITS_STYLE)
            line.setObjectName("exampleLimits")
            col.addWidget(line)
        return host

    def _on_copy_chain_prompt(self) -> None:
        text = getattr(self, "_chain_prompt_text", "") or ""
        clipboard = QApplication.clipboard() if text else None
        if clipboard is not None:
            clipboard.setText(text)
            if self._chain_copy_btn is not None:
                self._chain_copy_btn.setText(tr("Copied"))

    @staticmethod
    def _example_vector_text(vectorize) -> str:
        if not isinstance(vectorize, dict):
            return ""
        if vectorize.get("mode") == "none":
            return str(vectorize.get("note") or tr("No vector step"))
        parts = []
        classes = vectorize.get("classes") or []
        labels = [str(c.get("label")) for c in classes if isinstance(c, dict) and c.get("label")]
        if labels:
            parts.append(", ".join(labels))
        count = vectorize.get("feature_count")
        if isinstance(count, (int, float)) and count >= 0:
            parts.append(tr("{n} polygons").format(n=format_count(int(count))))
        if vectorize.get("note"):
            parts.append(str(vectorize.get("note")))
        return " - ".join(parts)



    def _build_try_zone_button(self) -> QPushButton | None:
        if self._example_zone_bbox() is None:
            return None
        btn = QPushButton(tr("Try on this zone"), self)
        btn.setStyleSheet(_ACTION_BTN)
        btn.setCursor(QtC.PointingHandCursor)
        btn.setToolTip(tr("Load this example's imagery and zone. You still click Generate."))
        btn.setEnabled(not self._browse_only)
        btn.clicked.connect(self._on_try_zone)
        self._try_zone_btn = btn
        return btn

    def _on_try_zone(self) -> None:
        telemetry.track(te.EXAMPLE_ZONE_TRIED, {
            "template_id": str((self._preset or {}).get("id") or ""),
        })
        self._outcome = "try_zone"
        self.accept()
