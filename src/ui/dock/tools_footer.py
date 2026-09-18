from __future__ import annotations

from qgis.core import QgsLayerTree, QgsProject, QgsRasterLayer
from qgis.PyQt.QtGui import QColor

from ...core.auth.activation_manager import get_tutorial_url
from ...core.i18n import tr
from ..dialogs.error_report_dialog import (
    REPORT_PROBLEM_HREF,
    show_error_report,
)
from ..external_url import open_external
from ..icons import icon_for
from ..layers_panel import show_layers_panel
from .blocked_reasons import (
    LAUNCH_BLOCK_NO_KEY,
    LAUNCH_BLOCK_NO_RASTER,
    LAUNCH_BLOCK_TILES_WARMING,
)
from .design_tokens import (
    INK,
    INK_2,
    INK_3,
    qcolor,
    repolish_widget,
)
from .tool_bar import set_compare_icon, tool_available


def _tool_panel_is_open(dock) -> bool:
    """Compatibility helper for callers that inspect the tool-panel guard."""
    checker = getattr(dock, "_tool_panel_open", None)
    if callable(checker):
        if bool(checker()):
            return True
    for name in ("_markup_panel", "_vectorize_panel", "_reference_panel"):
        panel = getattr(dock, name, None)
        try:
            if panel is not None and panel.isVisible():
                return True
        except RuntimeError:
            continue
    return False


def tree_has_visible_raster(node) -> bool:
    """True as soon as one checked raster sits under checked groups.

    A raster, not any layer: the edit starts from the raster picked in "Image
    to edit", and a project showing only vectors has nothing to offer there.

    Same answer as ``any(n.isVisible() for n in root.findLayers() if raster)``
    but stops at the first hit instead of listing the whole tree, which QGIS
    rebuilds on every layer added."""
    for child in node.children():
        if not child.itemVisibilityChecked():
            continue
        if QgsLayerTree.isLayer(child):
            if isinstance(child.layer(), QgsRasterLayer):
                return True
        elif QgsLayerTree.isGroup(child) and tree_has_visible_raster(child):
            return True
    return False


class DockToolsFooterMixin:
    """Layer-visibility gate, tool panels (Draw / References / Vectorize),
    the tool bar's state, and the status box's report link for AIEditDockWidget."""

    def _schedule_layer_warning_update(self, *_args):
        """Re-check the Launch gate after the layer tree has settled.

        Connected to ``layersAdded`` / ``layersRemoved`` and the tree's
        ``visibilityChanged``. The first two fire mid-sync: the node for the
        new layer is not in the tree yet at emit time. Deferring by one event
        loop tick lets QGIS finish wiring it. One reused timer folds a burst
        (300 layers added at once) into a single check.
        """
        self._layer_warning_timer.start(0)

    def _run_layer_warning_update(self) -> None:
        """Timer target: skip the tree walk while the dock is hidden and
        catch up when it shows (see AIEditDockWidget.showEvent)."""
        if not self.isVisible():
            self._layer_warning_dirty = True
            return
        self._layer_warning_dirty = False
        self._update_layer_warning()

    def _update_layer_warning(self, *_args):
        """Show/hide the empty-canvas hero and keep the entry flow coherent with
        what is actually visible on the map.

        We check ``isVisible()`` on the layer tree, not just registered layers
        in the project - a layer that exists but is unchecked produces no
        canvas pixels for AI Edit to capture, so launching from that state
        would just send an empty rectangle to the model.

        When nothing is visible we FIRST drive the flow back to the canonical
        empty baseline (``set_launch_state`` hides the zone / prompt / result /
        progress sections and clears ``_zone_selected``), so a layer deleted
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
        # A visible RASTER, not any visible layer: the edit starts from one
        # raster picked in "Image to edit", and a project showing only vectors
        # has nothing that field could offer.
        has_visible = tree_has_visible_raster(QgsProject.instance().layerTreeRoot())
        if not has_visible:
            # A tool panel (Draw, References, Vectorize) covers the flow: the
            # reset used to run behind it, and Done then landed on an empty
            # home screen with the zone and the prompt gone and no word why
            # (findings 2.3). Held until the panel closes (exit_tool_panel).
            if _tool_panel_is_open(self):
                self._layer_warning_dirty = True
                return
            # The typed prompt survives the reset: hiding the last layer is
            # not a request to throw the sentence away. The next zone finds it
            # back in the box. Kept across the repeated checks while the map
            # stays blank, released once a layer shows again.
            keep_prompt = self._zone_selected or getattr(self, "_prompt_kept_for_blank_map", False)
            kept_text = self._prompt_input.toPlainText() if keep_prompt else ""
            kept_template = (self._active_template_id, self._active_template_name)
            # Reset first, then reveal the hero. Launch STAYS on screen,
            # greyed, with the reason written under it (2026-09-03): hiding it
            # left 29 people a month on an entry screen with no primary button
            # and nothing telling them what was missing.
            self.set_launch_state()
            if kept_text:
                self._prompt_input.blockSignals(True)
                try:
                    self._prompt_input.setPlainText(kept_text)
                finally:
                    self._prompt_input.blockSignals(False)
                self._active_template_id, self._active_template_name = kept_template
            self._prompt_kept_for_blank_map = bool(kept_text)
            self._warning_widget.setVisible(True)
            self._sync_demo_button()
            # Re-decide the hero's secondary action on every (re)show: the
            # project may have gone from "no layers" to "layers, all hidden"
            # since the last time the card was up.
            self._sync_warning_actions()
            # The card IS the call to action here, so the Launch row stands
            # down rather than repeating it (Yvann 2026-09-03).
            self.set_launch_block_reason(
                LAUNCH_BLOCK_NO_RASTER, has_own_card=True
            )
            self._past_sessions_link.setVisible(False)
            if getattr(self, "_launch_hero", None) is not None:
                self._launch_hero.setVisible(False)
            # The card asks for imagery; an empty "Image to edit" over it
            # would ask the same thing twice.
            if getattr(self, "_layer_header", None) is not None:
                self._layer_header.setVisible(False)
        else:
            if getattr(self, "_layer_header", None) is not None:
                self._layer_header.setVisible(True)
            self._prompt_kept_for_blank_map = False
            self._warning_widget.setVisible(False)
            # The account check outranks the imagery: a layer toggled while the
            # key was being confirmed used to switch Launch back on mid-check.
            if getattr(self, "_launch_account_pending", False):
                reason = LAUNCH_BLOCK_NO_KEY
            elif self._imagery_loading:
                reason = LAUNCH_BLOCK_TILES_WARMING
            else:
                reason = None
            self.set_launch_block_reason(reason)
            self._past_sessions_link.setVisible(True)
            if getattr(self, "_launch_hero", None) is not None:
                self._launch_hero.setVisible(True)

    def _tool_panel_open(self) -> bool:
        """True while Draw, References or Vectorize has the dock."""
        for name in ("_markup_panel", "_vectorize_panel", "_reference_panel"):
            panel = getattr(self, name, None)
            try:
                if panel is not None and not panel.isHidden():
                    return True
            except RuntimeError:  # panel deleted during teardown
                continue
        return False

    def _on_project_loaded(self, *_args):
        """Re-bind to the fresh layerTreeRoot and re-evaluate the Launch gate.

        New-project / open-project replace the layerTreeRoot instance, so the
        original visibilityChanged binding (made in __init__) ends up pointing
        at an orphaned tree. Rebind here and defer the gate check by one event
        loop tick so QGIS finishes syncing the new tree's layers first.
        """
        try:
            QgsProject.instance().layerTreeRoot().visibilityChanged.disconnect(
                self._schedule_layer_warning_update
            )
        except (TypeError, RuntimeError):  # old layer tree already gone or never connected
            pass
        QgsProject.instance().layerTreeRoot().visibilityChanged.connect(
            self._schedule_layer_warning_update
        )
        self._schedule_layer_warning_update()

    def _on_open_tutorial(self):
        """Open the tutorial URL in the user's default browser."""
        open_external(get_tutorial_url())

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
        # With the Layers panel closed the selection had nothing to show and
        # the link read as dead: open the panel first, then select in it.
        show_layers_panel(tree_view)
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

    # Tool panels (Draw, Vectorize, References) swap with `_main_widget`
    # and restore it on Done. Vectorize and Compare are hidden toggles
    # (tool_bar.py) shown as buttons on the result screen only.

    # Public API consumed by the plugin layer ---------------------------

    def _hide_reference_panel(self) -> None:
        """The Reference panel is None on docks built without the refs strip
        (no store), so every sibling-swap guards the hide."""
        if getattr(self, "_reference_panel", None) is not None:
            self._reference_panel.setVisible(False)

    def set_markup_state(self) -> None:
        """Swap the dock view to the Draw panel."""
        self._stop_progress_animation()
        self._hide_status_box()
        self._vectorize_panel.deactivate()
        self._main_widget.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._hide_reference_panel()
        self._markup_panel.setVisible(True)
        self._markup_panel.activate()
        self._refresh_dock_key_shortcuts()

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
        self._refresh_dock_key_shortcuts()

    def set_reference_capture_armed(self, armed: bool) -> None:
        """Mirror the map-capture tool's state on the panel's "Map" chip. The
        plugin owns the tool; the dock only shows whether it is armed."""
        panel = getattr(self, "_reference_panel", None)
        if panel is not None:
            panel.set_capture_armed(armed)

    def set_reference_state(self) -> None:
        """Swap the dock view to the References panel (import + per-image
        notes). The panel's "Map" chip asks the plugin to arm a canvas tool,
        nothing here touches the canvas."""
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
        self._refresh_dock_key_shortcuts()

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
        self._refresh_dock_key_shortcuts()
        # A layer change held while the panel was open runs now, on the
        # screen the user is back on (see _update_layer_warning).
        if self._layer_warning_dirty:
            self._schedule_layer_warning_update()

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
        self._sync_result_tools_row()

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
        self._sync_result_tools_row()

    def _on_result_compare_toggled(self, checked: bool) -> None:
        """Panel Compare button: drive the footer twin, which owns the swipe.

        The equality guard is what stops the two toggles from bouncing off
        each other, since each one mirrors the other.
        """
        if self._swipe_btn.isChecked() != checked:
            self._swipe_btn.setChecked(checked)

    def _sync_result_tools_row(self) -> None:
        """Mirror the hidden Vectorize and Before/after toggles onto the
        result screen's buttons. The toggles stay the source of truth, so
        the buttons can never allow what the toggles refuse; a tool that is
        not available (signed out, switched off) is not shown at all."""
        compare = getattr(self, "_result_compare_btn", None)
        vectorize = getattr(self, "_result_vectorize_btn", None)
        row = getattr(self, "_result_tools_row", None)
        if compare is None or vectorize is None or row is None:
            return
        try:
            swipe_on = tool_available(self._swipe_btn)
            vectorize_on = tool_available(self._vectorize_btn)
            compare.setVisible(swipe_on)
            compare.setEnabled(self._swipe_btn.isEnabled())
            checked = self._swipe_btn.isChecked()
            if compare.isChecked() != checked:
                compare.blockSignals(True)
                try:
                    compare.setChecked(checked)
                finally:
                    compare.blockSignals(False)
            set_compare_icon(compare)
            vectorize.setVisible(vectorize_on)
            row.setVisible(swipe_on or vectorize_on)
        except RuntimeError:
            pass  # the dock is being torn down

    def set_settings_button_active(self, active: bool) -> None:
        """Light the selected tint on the Settings (gear) header icon while
        the Account Settings dialog is open.
        """
        self._settings_btn.set_active(active)

    def _set_swipe_button_visible(self, visible: bool) -> None:
        """Make Before/after available or not (the result screen's Compare
        button follows).

        Mirrors the Vectorize rule: available whenever the dock is
        activated, not otherwise. The button operates on whichever AI-Edit
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
        """Arm (or clear) the post-generation Vectorize suggestion.

        Called by the plugin orchestrator after a successful generation when
        a template carried vector hints, a free-form prompt asked to segment
        one target, or the downloaded result itself is a set of flat color
        zones (trigger="flat_output", detected_colors carries the zone
        palette). The result screen's one Vectorize button carries it: a
        stronger label, a tooltip saying what was found, and a click that
        opens the panel pre-filled. ``class_label`` (when known) flows down to
        the vectorize panel so the polygons land with a sensible class_name.
        """
        from ...core.auth.activation_manager import is_feature_enabled

        # No suggestion for a feature the server switched off: offering it and
        # then refusing the click is worse than not offering it.
        if not layer_id or not color_hex or not is_feature_enabled("vectorize"):
            self._clear_vectorize_suggestion()
            return
        # Normalise the hex so we always pass `#RRGGBB` downstream.
        qc = QColor(color_hex)
        if not qc.isValid():
            self._clear_vectorize_suggestion()
            return
        normalised = qc.name().upper()
        # Instrument voice: say what was detected. A template or prompt trigger
        # found nothing to count, and the plain tooltip already says the rest.
        finding = ""
        if trigger == "flat_output":
            n_zones = sum(
                1 for c in (detected_colors or [normalised]) if QColor(c).isValid()
            ) or 1
            finding = (
                tr("{n} color zone detected in this result").format(n=n_zones)
                if n_zones == 1
                else tr("{n} color zones detected in this result").format(n=n_zones)
            )
        self._vectorize_cta_pending = (
            layer_id, normalised, class_label or "", trigger or ""
        )
        self._mark_result_vectorize_button(True, finding)

    def _clear_vectorize_suggestion(self) -> None:
        """Drop the armed suggestion: Vectorize goes back to its plain look."""
        self._vectorize_cta_pending = None
        self._mark_result_vectorize_button(False, "")

    def _mark_result_vectorize_button(self, suggested: bool, finding: str) -> None:
        """The suggested look (strong ink, heavier weight) or the plain one,
        with ``finding`` (what was detected) leading the tooltip."""
        button = getattr(self, "_result_vectorize_btn", None)
        if button is None:
            return
        plain_tip = getattr(self, "_result_vectorize_tip", "") or button.toolTip()
        try:
            button.setToolTip(f"{finding}. {plain_tip}" if finding else plain_tip)
            if bool(button.property("suggested")) != suggested:
                button.setProperty("suggested", suggested)
                repolish_widget(button)
            button.setIcon(
                icon_for(button, "polygon", 16, qcolor(INK if suggested else INK_2),
                         disabled_color=qcolor(INK_3))
            )
        except RuntimeError:
            pass  # the dock is being torn down

    def _on_result_vectorize_clicked(self) -> None:
        """The result screen's Vectorize: pre-filled when a suggestion is
        armed, the plain panel otherwise."""
        pending = getattr(self, "_vectorize_cta_pending", None)
        if pending is None:
            self.vectorize_clicked.emit()
            return
        layer_id, color_hex, class_label, trigger = pending
        self.vectorize_suggestion_clicked.emit(layer_id, color_hex, class_label, trigger)

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
