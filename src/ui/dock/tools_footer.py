from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QRectF, Qt, QUrl
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from qgis.PyQt.QtWidgets import QLabel, QPushButton, QWidget

from ...core import qt_compat as QtC
from ...core.auth.activation_manager import get_tutorial_url
from ...core.i18n import tr
from ..dialogs.error_report_dialog import (
    REPORT_PROBLEM_HREF,
    SUPPORT_EMAIL,
    show_error_report,
)
from ..panel_helpers import (
    apply_swatch_style,
    build_panel_header,
    make_hidpi_pixmap,
    panel_section_label,
)
from .style import _tinted_svg_icon


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
        """Show/hide the 'no visible layer' notice and lock the Launch button
        until at least one layer is actually checked in the legend.

        We check ``isVisible()`` on the layer tree, not just registered layers
        in the project - a layer that exists but is unchecked produces no
        canvas pixels for AI Edit to capture, so launching from that state
        would just send an empty rectangle to the model.
        """
        if self._zone_selected:
            self._warning_widget.setVisible(False)
            self._launch_btn.setEnabled(True)
            self._launch_btn.setToolTip("")
            return
        root = QgsProject.instance().layerTreeRoot()
        has_visible = any(
            node.isVisible() for node in root.findLayers()
            if node.layer() is not None
        )
        self._warning_widget.setVisible(not has_visible)
        self._launch_btn.setEnabled(has_visible)
        # A disabled button with no explanation reads as broken; say why.
        self._launch_btn.setToolTip(
            "" if has_visible else tr(
                "Add a visible imagery layer first, or click "
                "'Try it on an example' above."
            )
        )

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
        QDesktopServices.openUrl(QUrl(get_tutorial_url()))

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

    def _build_panel_header(
        self, title: str, on_back, subtitle: str | None = None
    ) -> QWidget:
        del on_back  # panels exit via the Done button at the bottom
        return build_panel_header(title, subtitle)

    @staticmethod
    def _panel_section_label(text: str) -> QLabel:
        return panel_section_label(text)

    @staticmethod
    def _apply_swatch_style(button: QPushButton, color: QColor) -> None:
        apply_swatch_style(button, color)

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

    # Public API consumed by the plugin layer ---------------------------

    def set_markup_state(self) -> None:
        """Swap the dock view to the Mark up panel."""
        self._stop_progress_animation()
        self._hide_status_box()
        self._vectorize_panel.deactivate()
        self._main_widget.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._markup_panel.setVisible(True)
        self._markup_panel.activate()

    def set_vectorize_state(self) -> None:
        """Swap the dock view to the Vectorize panel."""
        self._stop_progress_animation()
        self._hide_status_box()
        self._main_widget.setVisible(False)
        self._markup_panel.setVisible(False)
        self._vectorize_panel.setVisible(True)
        self._vectorize_panel.activate()
        self._vectorize_btn.set_active(True)
        # Swipe and Vectorize fight for the canvas; lock Swipe while the
        # Vectorize panel is open.
        self._swipe_panel_lock = True
        self._refresh_swipe_enabled()

    def exit_tool_panel(self) -> None:
        """Hide whichever tool panel is showing and restore _main_widget."""
        self._vectorize_panel.deactivate()
        self._markup_panel.setVisible(False)
        self._vectorize_panel.setVisible(False)
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

    def set_vectorize_button_active(self, active: bool) -> None:
        """Light the green tint on the Vectorize footer icon while its
        panel is open. Same visual language as the swipe button so the
        user always knows which AI Edit action owns the canvas.
        """
        self._vectorize_btn.set_active(active)

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

    def set_vectorize_suggestion(
        self,
        layer_id: str | None,
        color_hex: str | None,
        class_label: str = "",
    ) -> None:
        """Inject (or clear) the post-generation Vectorize CTA.

        Called by the plugin orchestrator after a successful generation
        when the template carried a vector_color in the catalog. Hidden
        the moment the user navigates away from the result section.
        ``class_label`` (when known) flows down to the vectorize panel
        so the produced polygons land with a sensible class_name value.
        """
        if not layer_id or not color_hex:
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
        self._vectorize_cta_swatch.setStyleSheet(
            f"background: {normalised};"
            " border: 1px solid rgba(128,128,128,0.5); border-radius: 3px;"
        )
        # The swatch communicates the pre-filled color already; the label
        # stays generic so it works for every template.
        self._vectorize_cta_btn.setText(tr("Vectorize this result") + " →")
        self._vectorize_cta_pending = (layer_id, normalised, class_label or "")
        self._vectorize_cta_section.setVisible(True)

    def _on_vectorize_cta_clicked(self) -> None:
        if self._vectorize_cta_pending is None:
            return
        layer_id, color_hex, class_label = self._vectorize_cta_pending
        self.vectorize_suggestion_clicked.emit(layer_id, color_hex, class_label)

    def _on_contact_us(self, _link=None):
        """Show a dialog with email + Calendly options."""
        from qgis.PyQt.QtWidgets import QApplication, QDialog
        from qgis.PyQt.QtWidgets import QVBoxLayout as _VBox

        calendly_url = "https://calendly.com/barbot-yvann/30min"

        dlg = QDialog(self._main_window_for_dialog())
        dlg.setWindowTitle(tr("Contact us"))
        dlg.setMinimumWidth(350)
        dlg.setMaximumWidth(450)
        lay = _VBox(dlg)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        msg = QLabel(tr("Bug, question, feature request?\nWe'd love to hear from you!"))
        msg.setWordWrap(True)
        lay.addWidget(msg)

        email_label = QLabel(f"<b>{SUPPORT_EMAIL}</b>")
        email_label.setTextInteractionFlags(QtC.TextSelectableByMouse)
        lay.addWidget(email_label)

        copy_btn = QPushButton(tr("Copy email address"))
        copy_btn.clicked.connect(
            lambda: (
                QApplication.clipboard().setText(SUPPORT_EMAIL),
                copy_btn.setText(tr("Copied!")),
            )
        )
        lay.addWidget(copy_btn)

        or_label = QLabel(tr("or"))
        or_label.setAlignment(QtC.AlignCenter)
        or_label.setStyleSheet("color: palette(text);")
        lay.addWidget(or_label)

        call_btn = QPushButton(tr("Book a video call"))
        call_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(calendly_url))
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
        in-app log dialog; any real URL opens in the browser."""
        if href == REPORT_PROBLEM_HREF:
            show_error_report(
                self._main_window_for_dialog(),
                request_id=getattr(self, "_pending_report_request_id", "") or "",
            )
            return
        QDesktopServices.openUrl(QUrl(href))

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
        key_style = (
            "background-color: rgba(128,128,128,0.18);"
            "border: 1px solid rgba(128,128,128,0.35);"
            "border-radius: 3px; padding: 1px 5px; font-family: monospace;"
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
            f"<tr><td>{k.format('Esc')}</td><td>{tr('Cancel selection')}</td></tr>"
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
