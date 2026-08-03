from __future__ import annotations

import html

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from qgis.PyQt.QtWidgets import QLabel, QPushButton

from ...core import qt_compat as QtC
from ...core.auth.activation_manager import (
    get_contact_call_url,
    get_support_email,
    get_tutorial_url,
)
from ...core.i18n import tr
from ..dialogs.error_report_dialog import (
    REPORT_PROBLEM_HREF,
    SUPPORT_EMAIL,
    show_error_report,
)
from ..external_url import open_external
from ..panel_helpers import make_hidpi_pixmap
from .style import _BTN_BLUE, _BTN_GREEN, _tinted_svg_icon


class DockToolsFooterMixin:
    """Layer-visibility gate, tool panels (Mark up / Vectorize / swipe),
    footer glyphs, and help/support dialogs for AIEditDockWidget."""

    def _schedule_layer_warning_update(self, *_args):
        """Re-check the Launch gate after the layer tree has settled.

        Connected to ``layersAdded`` / ``layersRemoved``, which fire mid-sync:
        the layer tree node for the new layer is not yet present in
        ``layerTreeRoot().findLayers()`` at emit time. Deferring by one event
        loop tick lets QGIS finish wiring the node before we evaluate visibility.
        """
        QtC.safe_single_shot(0, self, self._update_layer_warning)

    def _update_layer_warning(self, *_args):
        """Show/hide the empty-canvas hero and keep the entry flow coherent with
        what is actually visible on the map.

        We check ``isVisible()`` on the layer tree, not just registered layers
        in the project - a layer that exists but is unchecked produces no
        canvas pixels for AI Edit to capture, so launching from that state
        would just send an empty rectangle to the model.

        When nothing is visible we FIRST drive the flow back to the canonical
        empty baseline (``set_launch_state`` hides the zone / prompt / result /
        progress sections and clears ``_zone_selected``), so a raster deleted
        mid-flow (SELECTING_ZONE, PROMPT, RESULT) converges on the exact same
        centered hero as a fresh start, instead of stranding a half-open flow
        over a blank canvas (the old ``if self._zone_selected`` early-out kept
        the stale flow and suppressed the hero). Then the hero shows and Launch
        hides.
        """
        # During generation the entry chrome is intentionally hidden by
        # set_generating; never reset the flow out from under a running edit.
        if self._progress_widget.isVisible():
            return
        root = QgsProject.instance().layerTreeRoot()
        has_visible = any(
            node.isVisible() for node in root.findLayers()
            if node.layer() is not None
        )
        if not has_visible:
            # Reset first, then reveal the hero. One info per state (Yvann
            # 2026-07-08): the empty screen shows ONLY the hero card - a big
            # greyed Launch under it read as clutter, so Launch hides (not
            # grays) instead.
            self.set_launch_state()
            self._warning_widget.setVisible(True)
            self._sync_demo_button()
            # Re-decide the hero's secondary action on every (re)show: the
            # project may have gone from "no layers" to "layers, all hidden"
            # since the last time the card was up.
            self._sync_warning_actions()
            self._launch_btn.setVisible(False)
            self._launch_btn.setEnabled(False)
            self._past_sessions_link.setVisible(False)
        else:
            self._warning_widget.setVisible(False)
            self._launch_btn.setVisible(True)
            self._launch_btn.setEnabled(True)
            self._past_sessions_link.setVisible(True)
        # The first-steps banner follows the same rule: never stacked on the
        # hero card, back once imagery exists.
        try:
            self._update_first_steps_visibility()
        except (RuntimeError, AttributeError):
            pass

    def _on_project_loaded(self, *_args):
        """Re-bind to the fresh layerTreeRoot and re-evaluate the Launch gate.

        New-project / open-project replace the layerTreeRoot instance, so the
        original visibilityChanged binding (made in __init__) ends up pointing
        at an orphaned tree. Rebind here and defer the gate check by one event
        loop tick so QGIS finishes syncing the new tree's layers first.
        """
        try:
            QgsProject.instance().layerTreeRoot().visibilityChanged.disconnect(
                self._update_layer_warning
            )
        except (TypeError, RuntimeError):
            pass
        QgsProject.instance().layerTreeRoot().visibilityChanged.connect(
            self._update_layer_warning
        )
        QtC.safe_single_shot(0, self, self._update_layer_warning)

    def _on_open_tutorial(self):
        """Open the tutorial URL in the user's default browser."""
        open_external(get_tutorial_url())

    def _on_open_guide_footer(self):
        """Footer book button: open the step-by-step written guide (with UTM +
        best-effort telemetry). Always opens the URL, even if telemetry is off."""
        from ..onboarding_hint import open_guide
        open_guide("footer_tutorial")

    def _on_layer_saved_link_clicked(self, _link: str) -> None:
        """Focus the saved layer in the QGIS Layers panel."""
        layer_id = self._saved_layer_id
        if not layer_id:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return
        try:
            from qgis.utils import iface
        except ImportError:
            return
        if iface is None:
            return
        iface.setActiveLayer(layer)
        tree_view = iface.layerTreeView()
        if tree_view is None:
            return
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer_id) if root is not None else None
        if node is None:
            return
        model = tree_view.layerTreeModel()
        if model is None:
            return
        index = model.node2index(node)
        tree_view.setCurrentIndex(index)
        tree_view.scrollTo(index)

    # ------------------------------------------------------------------
    # Tool panels (Mark up, Vectorize) - full-dock views reached via the
    # 🧰 Tools menu. They swap with `_main_widget` and restore it on Done.
    # ------------------------------------------------------------------

    def _make_polygon_glyph_icon(self) -> QIcon:
        """Footer Vectorize button glyph - same square-in-square shape as the
        Prompt Library 'Segment' tab (Unicode ▣). Painted in the palette text
        colour (like the pencil chip) so it stays legible on dark themes and
        Windows instead of rendering as an invisible black-on-black square.
        """
        size = 40  # 2x for crisp rendering at 20px
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        pen = QPen(ink)
        pen.setWidthF(2.4)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        outer = QRectF(6, 6, 28, 28)
        p.drawRect(outer)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(ink))
        inner = QRectF(13, 13, 14, 14)
        p.drawRect(inner)
        p.end()
        return QIcon(pm)

    def _make_swipe_glyph_icon(self) -> QIcon:
        """Footer Before/after glyph - swipe.svg tinted to the palette text
        colour so it carries the same weight as the gear / help glyphs rather
        than looking dim and half-transparent on a dark theme.
        """
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        return _tinted_svg_icon("swipe.svg", ink)

    def _make_gear_glyph_icon(self) -> QIcon:
        """Footer Settings glyph - a vector gear painted in the palette text
        colour. Replaces the U+2699 GEAR character, which Windows renders as a
        colour emoji (Segoe UI Emoji) while macOS shows a flat black glyph; the
        painted version is identical on both platforms and crisp at any DPI.
        """
        import math

        from qgis.PyQt.QtCore import QPointF, Qt
        from qgis.PyQt.QtGui import QPainter, QPainterPath

        s = 20
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        pm = make_hidpi_pixmap(s)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx = cy = s / 2.0
        teeth = 8
        step = 2.0 * math.pi / teeth
        r_tip = s * 0.46
        r_root = s * 0.34
        half_tip = step * 0.18
        half_root = step * 0.30
        path = QPainterPath()
        for i in range(teeth):
            a = i * step
            corners = (
                (a - half_root, r_root),
                (a - half_tip, r_tip),
                (a + half_tip, r_tip),
                (a + half_root, r_root),
            )
            for ang, r in corners:
                pt = QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang))
                if i == 0 and ang == corners[0][0]:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
        path.closeSubpath()
        # Center hole: OddEven fill subtracts it from the gear body.
        path.addEllipse(QPointF(cx, cy), s * 0.15, s * 0.15)
        path.setFillRule(Qt.FillRule.OddEvenFill)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        p.drawPath(path)
        p.end()
        return QIcon(pm)

    def _make_book_glyph_icon(self) -> QIcon:
        """Footer Tutorial glyph - two stroked pages meeting on a spine, in the
        palette text colour. Replaces U+1F4D6 OPEN BOOK, which Windows renders
        through Segoe UI Emoji as a full-colour bitmap: next to four flat
        palette-ink vector glyphs it was the one coloured picture in the row.
        (It did not change the row height - the footer button's sizeHint
        measures 89x37 with the emoji, with "?" and with the gear character
        alike.)

        Stroke width and span are set so the painted weight matches its
        neighbours rather than fading next to them: 152.1 ink px over the 20px
        box, against the filled gear's 171.5 and the polygon's 116.2. The
        1.6px/25-75% version measured 97.3.
        """
        from qgis.PyQt.QtCore import QPointF, Qt
        from qgis.PyQt.QtGui import QPainter, QPainterPath, QPen

        s = 20
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        pm = make_hidpi_pixmap(s)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(ink)
        pen.setWidthF(2.2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx = s / 2.0
        top, bottom = s * 0.18, s * 0.82
        edge = s * 0.10
        for outer in (edge, s - edge):
            page = QPainterPath()
            page.moveTo(QPointF(cx, top))
            # Both curves bow away from the spine, so the pages read as open.
            page.quadTo(
                QPointF((cx + outer) / 2.0, top - s * 0.09),
                QPointF(outer, top - s * 0.05),
            )
            page.lineTo(QPointF(outer, bottom - s * 0.05))
            page.quadTo(
                QPointF((cx + outer) / 2.0, bottom - s * 0.09),
                QPointF(cx, bottom),
            )
            # closeSubpath draws the spine back up to the start point.
            page.closeSubpath()
            p.drawPath(page)
        p.end()
        return QIcon(pm)

    # Public API consumed by the plugin layer ---------------------------

    def _hide_reference_panel(self) -> None:
        """The Reference panel is None on docks built without the refs strip
        (no store), so every sibling-swap guards the hide."""
        if getattr(self, "_reference_panel", None) is not None:
            self._reference_panel.setVisible(False)

    def set_markup_state(self) -> None:
        """Swap the dock view to the Mark up panel."""
        self._stop_progress_animation()
        self._hide_status_box()
        self._vectorize_panel.deactivate()
        self._main_widget.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._hide_reference_panel()
        self._markup_panel.setVisible(True)
        self._markup_panel.activate()

    def set_vectorize_state(self) -> None:
        """Swap the dock view to the Vectorize panel."""
        self._stop_progress_animation()
        self._hide_status_box()
        self._main_widget.setVisible(False)
        self._markup_panel.setVisible(False)
        self._hide_reference_panel()
        self._vectorize_panel.setVisible(True)
        self._vectorize_panel.activate()
        self._vectorize_btn.set_active(True)
        # Swipe and Vectorize fight for the canvas; lock Swipe while the
        # Vectorize panel is open.
        self._swipe_panel_lock = True
        self._refresh_swipe_enabled()

    def set_reference_state(self) -> None:
        """Swap the dock view to the Reference panel (import + per-image
        notes). No map tool involved, so nothing canvas-side to arm."""
        if getattr(self, "_reference_panel", None) is None:
            return
        self._stop_progress_animation()
        self._hide_status_box()
        self._vectorize_panel.deactivate()
        self._main_widget.setVisible(False)
        self._markup_panel.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._reference_panel.setVisible(True)
        self._reference_panel.activate()

    def exit_tool_panel(self) -> None:
        """Hide whichever tool panel is showing and restore _main_widget."""
        self._vectorize_panel.deactivate()
        self._markup_panel.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._hide_reference_panel()
        self._main_widget.setVisible(True)
        self._vectorize_btn.set_active(False)
        self._swipe_panel_lock = False
        self._refresh_swipe_enabled()

    def set_swipe_button_checked(self, checked: bool) -> None:
        """Sync the Before/After button visual to the controller state.

        Called by the plugin when the swipe is armed or disarmed by
        anything other than a direct button click (Esc on the canvas,
        layer removal, plugin shutdown). Blocks the toggled signal so we
        don't recurse into the controller.
        """
        if self._swipe_btn.isChecked() == checked:
            return
        self._swipe_btn.blockSignals(True)
        try:
            self._swipe_btn.setChecked(checked)
        finally:
            self._swipe_btn.blockSignals(False)

    def set_swipe_button_enabled(self, can_swipe: bool) -> None:
        """Gate the Before/After button on whether a swipeable layer is
        currently the active layer in the QGIS Layers panel. Stays
        enabled while the swipe is on so the user can always click to
        turn it off. Forced off while the Vectorize panel is open
        (mutually exclusive tools).
        """
        self._swipe_eligible = can_swipe
        self._refresh_swipe_enabled()

    def _refresh_swipe_enabled(self) -> None:
        is_checked = self._swipe_btn.isChecked()
        enabled = (self._swipe_eligible or is_checked) and not self._swipe_panel_lock
        self._swipe_btn.setEnabled(enabled)

    def set_settings_button_active(self, active: bool) -> None:
        """Light the green tint on the Settings (gear) footer icon while
        the Account Settings dialog is open.
        """
        self._settings_btn.set_active(active)

    def _set_swipe_button_visible(self, visible: bool) -> None:
        """Show or hide the Before/after footer button.

        Mirrors the Vectorize visibility rule: revealed whenever the dock is
        activated, hidden otherwise. The button operates on whichever AI-Edit
        raster the user has active in the QGIS Layers panel, not just on a
        fresh generation - per-click eligibility (greyed-out vs clickable)
        is driven separately by set_swipe_button_enabled.

        The ``and self._activated`` guard is a safety net: it keeps the button
        hidden if a caller fires this before set_activated has run.
        """
        self._swipe_btn.setVisible(visible and self._activated)

    def set_markup_annotation_count(self, count: int) -> None:
        self._markup_panel.set_annotation_count(count)

    def set_markup_zone_present(self, has_zone: bool) -> None:
        self._markup_panel.set_zone_present(has_zone)

    def get_markup_color(self) -> QColor:
        return self._markup_panel.get_color()

    def set_markup_tool_unchecked(self, tool_key: str) -> None:
        """Uncheck a Mark up tool button without emitting tool_changed.

        Called by the plugin when the underlying map tool self-deactivates
        from under the panel (Line's two-stage Escape calls
        canvas.unsetMapTool(self) directly), so the button reflects that no
        drawing tool is armed on the canvas anymore.
        """
        self._markup_panel.uncheck_tool(tool_key)

    def set_vectorize_suggestion(
        self,
        layer_id: str | None,
        color_hex: str | None,
        class_label: str = "",
        detected_colors: list[str] | None = None,
        trigger: str = "",
    ) -> None:
        """Inject (or clear) the post-generation Vectorize CTA card.

        Called by the plugin orchestrator after a successful generation when
        a template carried vector hints, a free-form prompt asked to segment
        one target, or the downloaded result itself is a set of flat color
        zones (trigger="flat_output", detected_colors carries the zone
        palette). Hidden the moment the user navigates away from the result
        section. ``class_label`` (when known) flows down to the vectorize
        panel so the produced polygons land with a sensible class_name value.
        """
        from ...core.auth.activation_manager import is_feature_enabled

        # No CTA for a feature the server switched off: offering it and then
        # refusing the click is the one thing worse than not offering it.
        if not layer_id or not color_hex or not is_feature_enabled("vectorize"):
            self._vectorize_cta_section.setVisible(False)
            self._vectorize_cta_pending = None
            return
        # Normalise the hex so we always pass `#RRGGBB` downstream.
        qc = QColor(color_hex)
        if not qc.isValid():
            self._vectorize_cta_section.setVisible(False)
            self._vectorize_cta_pending = None
            return
        normalised = qc.name().upper()
        swatches: list[str] = []
        for candidate in detected_colors or [normalised]:
            sc = QColor(candidate)
            if sc.isValid():
                swatches.append(sc.name().upper())
        if not swatches:
            swatches = [normalised]
        self._set_vectorize_swatches(swatches)
        # Instrument voice: measure what was detected; the button carries
        # the action. The generic line covers template / prompt triggers.
        if trigger == "flat_output":
            n_zones = len(swatches)
            caption = (
                tr("{n} color zone detected in this result").format(n=n_zones)
                if n_zones == 1
                else tr("{n} color zones detected in this result").format(n=n_zones)
            )
        else:
            caption = tr("Turn the colored zones into editable polygons")
        self._vectorize_cta_caption.setText(caption)
        self._vectorize_cta_pending = (
            layer_id, normalised, class_label or "", trigger or ""
        )
        # The AI Segmentation line rides along unless closed for good.
        from ..onboarding_hint import HINT_SEG_CROSS, is_hint_dismissed
        self._vectorize_seg_row.setVisible(not is_hint_dismissed(HINT_SEG_CROSS))
        self._vectorize_cta_section.setVisible(True)

    def _on_vectorize_seg_link_activated(self, href: str) -> None:
        """The Vectorize card's AI Segmentation link: open the plugin's dock
        when installed, else the Plugin Manager pre-filtered to it."""
        if href != "ai_seg":
            return
        from ...core import telemetry
        from ...core import telemetry_events as te
        from ..cross_plugin_discovery import open_ai_segmentation
        installed = open_ai_segmentation()
        telemetry.track(te.SEG_REDIRECT_CLICKED, {
            "guidance_kind": "vectorize_cta",
            "installed": installed,
        })
        telemetry.flush()

    def _on_vectorize_seg_dismissed(self) -> None:
        from ..onboarding_hint import HINT_SEG_CROSS, dismiss_hint
        dismiss_hint(HINT_SEG_CROSS)
        self._vectorize_seg_row.setVisible(False)

    def _set_vectorize_swatches(self, colors: list[str]) -> None:
        """Rebuild the CTA's color swatch row (max 6, one per detected zone)."""
        row = self._vectorize_cta_swatch_row
        while row.count():
            item = row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for hex_color in colors[:6]:
            sw = QLabel()
            sw.setFixedSize(14, 14)
            sw.setStyleSheet(
                f"background: {hex_color};"
                " border: 1px solid rgba(128,128,128,0.5); border-radius: 3px;"
            )
            row.addWidget(sw)

    def _on_vectorize_cta_clicked(self) -> None:
        if self._vectorize_cta_pending is None:
            return
        layer_id, color_hex, class_label, trigger = self._vectorize_cta_pending
        self.vectorize_suggestion_clicked.emit(layer_id, color_hex, class_label, trigger)

    def _on_contact_us(self, _link=None):
        """Show a dialog with email + Calendly options."""
        from qgis.PyQt.QtWidgets import QApplication, QDialog
        from qgis.PyQt.QtWidgets import QVBoxLayout as _VBox

        call_url = get_contact_call_url()
        support_email = get_support_email(SUPPORT_EMAIL)

        dlg = QDialog(self._main_window_for_dialog())
        dlg.setWindowTitle(tr("Contact us"))
        dlg.setMinimumWidth(350)
        dlg.setMaximumWidth(450)
        lay = _VBox(dlg)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        # Same design as the AI Segmentation Contact dialog: styled labels,
        # green primary (copy email), blue secondary (book a call), so support
        # surfaces match across the TerraLab plugins.
        msg = QLabel(tr("Bug, question, feature request?\nWe'd love to hear from you!"))
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size: 12px; color: palette(text);")
        lay.addWidget(msg)

        # Bold means rich text, and the address may come from the server, so it
        # is escaped here as well as validated there.
        email_label = QLabel(f"<b>{html.escape(support_email, quote=False)}</b>")
        email_label.setTextInteractionFlags(QtC.TextSelectableByMouse)
        email_label.setStyleSheet("font-size: 12px; color: palette(text);")
        lay.addWidget(email_label)

        # Primary action: green filled CTA, like the dock's own primary buttons.
        copy_btn = QPushButton(tr("Copy email address"))
        copy_btn.setStyleSheet(_BTN_GREEN)
        copy_btn.setCursor(QtC.PointingHandCursor)
        copy_btn.clicked.connect(
            lambda: (
                QApplication.clipboard().setText(support_email),
                copy_btn.setText(tr("Copied!")),
            )
        )
        lay.addWidget(copy_btn)

        or_label = QLabel(tr("or"))
        or_label.setAlignment(QtC.AlignCenter)
        or_label.setStyleSheet("color: palette(text); font-size: 11px;")
        lay.addWidget(or_label)

        # Secondary action: blue filled, one step down from the green primary.
        call_btn = QPushButton(tr("Book a video call"))
        call_btn.setStyleSheet(_BTN_BLUE)
        call_btn.setCursor(QtC.PointingHandCursor)
        call_btn.clicked.connect(
            lambda: open_external(call_url)
        )
        lay.addWidget(call_btn)

        dlg.exec()

    def _on_report_problem(self, _link=None):
        """User-initiated report: copy the session logs and email support."""
        show_error_report(self._main_window_for_dialog())

    def arm_report_context(self, request_id: str = "") -> None:
        """Stash the request id for the next inline 'Report a problem' link so the
        emailed log carries the server correlation key."""
        self._pending_report_request_id = request_id or ""

    def _on_status_link(self, href: str) -> None:
        """Route a clicked link in the status box: the report sentinel opens the
        in-app log dialog; any real URL opens in the browser (http/https only)."""
        if href == REPORT_PROBLEM_HREF:
            show_error_report(
                self._main_window_for_dialog(),
                request_id=getattr(self, "_pending_report_request_id", "") or "",
            )
            return
        open_external(href)

    def _on_show_shortcuts(self, _link=None):
        from qgis.PyQt.QtWidgets import QDialog
        from qgis.PyQt.QtWidgets import QVBoxLayout as _VBox

        def native(seq: str) -> str:
            return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)

        undo_key = QKeySequence(QKeySequence.StandardKey.Undo).toString(
            QKeySequence.SequenceFormat.NativeText
        )
        launch_key = native("Ctrl+Alt+E")
        markup_key = native("Alt+M")
        vectorize_key = native("Alt+V")
        swipe_key = native("Alt+B")
        # Real family names, not the CSS generic `monospace`: Qt maps no font to
        # it, so on Windows the keycaps fell back to Segoe UI and the shortcut
        # column lost its alignment.
        key_style = (
            "background-color: rgba(128,128,128,0.18);"
            "border: 1px solid rgba(128,128,128,0.35);"
            "border-radius: 3px; padding: 1px 5px;"
            ' font-family: Consolas, "Cascadia Mono", Menlo, monospace;'
        )
        k = f"<span style='{key_style}'>{{}}</span>"

        enter_key = QKeySequence(QtC.Key_Return).toString(
            QKeySequence.SequenceFormat.NativeText
        )
        shortcuts_html = (
            "<table cellspacing='4' cellpadding='2'>"
            f"<tr><td colspan='2' style='padding-bottom:2px;'><b>{tr('Editing')}</b></td></tr>"
            f"<tr><td>{k.format(launch_key)}</td><td>{tr('Launch AI Edit')}</td></tr>"
            f"<tr><td>{k.format(enter_key)}</td><td>{tr('Generate')}</td></tr>"
            f"<tr><td>{k.format('Esc')}</td><td>{tr('Go back one step (comparison, drawing, zone)')}</td></tr>"
            f"<tr><td>{k.format(undo_key)}</td><td>{tr('Undo')}</td></tr>"
            f"<tr><td>{k.format(markup_key)}</td><td>{tr('Mark up')}</td></tr>"
            f"<tr><td>{k.format(vectorize_key)}</td><td>{tr('Vectorize')}</td></tr>"
            f"<tr><td>{k.format(swipe_key)}</td><td>{tr('Before / after')}</td></tr>"
            "</table>"
        )

        dlg = QDialog(self._main_window_for_dialog())
        dlg.setWindowTitle(tr("Shortcuts"))
        lay = _VBox(dlg)
        lay.setContentsMargins(16, 16, 16, 12)
        label = QLabel(shortcuts_html)
        label.setTextFormat(QtC.RichText)
        lay.addWidget(label)
        ok_btn = QPushButton(tr("OK"))
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(dlg.accept)
        lay.addWidget(ok_btn, alignment=QtC.AlignCenter)
        dlg.exec()
