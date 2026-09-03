from __future__ import annotations

import time
import uuid

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtGui import QColor, QPixmap

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.canvas_export.zone_validation import (
    OVERLAP_OK,
    OVERLAP_OUTSIDE,
    OVERLAP_PARTIAL,
    zone_layer_overlap,
)
from ...core.i18n import tr
from ...core.logger import log_debug, log_warning
from ..dock.blocked_reasons import LAUNCH_BLOCK_WORKER_BUSY

# Committed zone chrome: AI Edit's zone blue as an OUTLINE, never a fill. The
# imagery inside the zone is the model input and the generated result the user
# judges, so a veil over it (inherited from AI Segmentation's selector, where
# the interior is the result) sits between the user and what they came to see.
_ZONE_OUTLINE = QColor(65, 105, 225, 220)
_ZONE_NO_FILL = QColor(0, 0, 0, 0)
_ZONE_OUTLINE_WIDTH = 2


class ZoneVersionsMixin:
    def _activate_selection_tool(self):
        """Activate selection tool. Preserves any existing zone."""
        if self._canvas.mapTool() != self._map_tool:
            current_tool = self._canvas.mapTool()
            if current_tool:
                self._previous_map_tool = current_tool
            self._canvas.setMapTool(self._map_tool)
        self._dock_widget.set_status("")

    def _deactivate_selection_tool(self):
        """Restore the map tool that was active before selection started.

        The reference is always dropped, even when the restore fails: it points
        at a foreign tool the plugin only borrowed, and unload must not leave a
        dead wrapper on the plugin graph.
        """
        if self._previous_map_tool is not None and self._canvas is not None:
            try:
                self._canvas.setMapTool(self._previous_map_tool)
            except (RuntimeError, AttributeError):  # nosec B110 - canvas gone
                pass
        self._previous_map_tool = None

    def _cancel_generation_and_reset_zone(self):
        """Shared Stop/Exit teardown: cancel in-flight work, clear zone state.

        Always disarms the swipe and the action pills: stopping a run means
        the previous result's pills must not resurface later.
        """
        self._disarm_swipe()
        self._pills_armed = False
        if self._worker is not None and self._worker.is_active() and not self._generation_cancel_handled:
            duration = time.time() - getattr(self, "_generation_start_time", time.time())
            telemetry.track(te.GENERATION_CANCELLED, self._enrich_generation_props({
                "duration_ms": int(duration * 1000),
                "resolution": getattr(self, "_last_suggested_res", ""),
            }))
            telemetry.flush()
            self._generation_service.cancel()
            # The plugin recovers the UI itself here, so tell the taskTerminated
            # slot not to double-handle this same cancel.
            self._generation_cancel_handled = True
            # Cancel the task too, not just the service. Otherwise finished()
            # sees isCanceled()==False and emits a stale "Generation cancelled"
            # error into the reset UI (plus a spurious generation_failed event).
            try:
                self._worker.cancel()
            except Exception:  # nosec B110
                pass
        # Drop our reference to the cancelled task: disconnect its signals and
        # null the ref so its multi-MB base64 payload is released now instead of
        # lingering until the next run. TaskManager still owns and drains it.
        self._cleanup_worker()
        # Also tear down an in-flight canvas export: without this a Stop/Exit
        # during the export phase still chains into a generation (and a charge)
        # after the user cancelled.
        if self._export_worker is not None and self._export_worker.is_active():
            try:
                self._export_worker.cancel()
            except Exception:  # nosec B110
                pass
            self._export_worker = None
        self._pending_generation = None
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._selected_polygon = None
        self._last_image_b64 = None
        self._last_guidance_b64 = None
        self._last_guidance_format = None
        if self._map_tool:
            self._map_tool.set_has_zone(False)
        self._deactivate_selection_tool()

    def _on_stop(self):
        """Dock closing mid-generation: cancel work and clear zone state.

        Triggered by the dock's closeEvent (title-bar X). The Exit button has
        its own handler - see _on_exit_clicked.
        """
        self._cancel_generation_and_reset_zone()
        # Reset the DOCK VIEW, not just the data. Setting _generation_cancel_handled
        # in the shared teardown suppresses _on_generation_task_terminated (which
        # would otherwise call set_generating(False)), so without this the dock
        # stays stuck on the generating view after a Stop. We cleared the zone, so
        # LAUNCH is the coherent landing state - same as _on_exit_clicked.
        self._dock_widget.set_launch_state()

    def _on_launch_shortcut(self):
        """Global shortcut: open the dock if hidden, then start a new edit."""
        if self._dock_widget is None:
            return
        if not self._dock_widget.isVisible():
            self._dock_widget.setVisible(True)
            self._ensure_dock_height()
        self._dock_widget.raise_()
        self._on_launch_clicked()

    def _on_launch_clicked(self):
        """User clicked 'Launch AI Edit' on the entry screen."""
        # The global shortcut reaches here from any state, so a run in flight
        # would have been thrown away without a word. Say so instead.
        if self._dock_widget.is_generation_in_flight():
            self._dock_widget.set_launch_block_reason(LAUNCH_BLOCK_WORKER_BUSY)
            return
        telemetry.track(te.LAUNCH_CLICKED)
        # First real commitment: nudge new users toward the tutorial (once ever).
        self._maybe_show_tutorial_nudge()
        self._disarm_swipe()
        self._activate_selection_tool()
        self._dock_widget.set_selecting_zone_state()

    def _on_exit_clicked(self):
        """User clicked Exit / Done: cancel work and return to LAUNCH."""
        # Index 0 of the lineage is the seeded Original, so anything past it
        # means at least one generation happened in this session.
        had_generation = len(self._versions or []) > 1
        self._cancel_generation_and_reset_zone()
        # Mark up annotations persist across sessions on a single shared layer.
        # User wipes them explicitly via the Clear all button.
        self._dock_widget.set_launch_state()
        if had_generation:
            # Leaving is not losing: the session stays reachable from the
            # Prompt Library's Sessions page.
            self._notify(
                tr(
                    "Your session stays in your history. Reopen it anytime "
                    "from the Prompt Library."
                ),
                duration=6,
            )

    def _on_project_layers_changed(self, *_args):
        """Re-check the canvas after the layer tree settles on a layer removal.

        Deferred one event-loop tick so QGIS finishes syncing the tree before
        we read visibility (same reason as the dock's
        _schedule_layer_warning_update). Parented to the dock so it can't fire
        into a torn-down plugin.
        """
        if self._dock_widget is None:
            return
        QtC.safe_single_shot(0, self._dock_widget, self._reset_canvas_if_empty)

    def _reset_canvas_if_empty(self):
        """When the user deletes the last visible layer, converge the CANVAS on
        the same empty baseline the dock shows.

        The dock resets its own view (see _update_layer_warning ->
        set_launch_state), but the selection map tool and the zone rubber band
        are plugin-owned. Without this teardown, deleting the last raster mid-
        flow (SELECTING_ZONE or ZONE_SELECTED) would leave the polygon tool
        armed and a stale zone frame floating over a blank canvas. Mirrors the
        tail of _on_exit_clicked, minus the generation cancel (a background run
        already works off captured bytes, so a removed layer must not abort it).
        """
        from qgis.core import QgsProject

        if self._dock_widget is None or self._canvas is None:
            return
        # A live generation owns the flow; never tear it down from here.
        if self._worker is not None and self._worker.is_active():
            return
        root = QgsProject.instance().layerTreeRoot()
        has_visible = any(
            node.isVisible() for node in root.findLayers()
            if node.layer() is not None
        )
        if has_visible:
            return
        self._disarm_swipe()
        self._pills_armed = False
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._selected_polygon = None
        if self._map_tool is not None:
            self._map_tool.set_has_zone(False)
        self._deactivate_selection_tool()

    def _on_base_version_selected(self, index: int):
        """A version tile was clicked: mirror it on the canvas.

        Show only the selected version's layer among the AI results (Original
        hides them all so the clean map shows through); the rest stay hidden.
        The active Mark up layer is user guidance, not an AI edit, so it is left
        untouched. Compare / Vectorize pills act on the AI result, so they drop
        when Original is selected and return for any generated version.

        A version without its layer in the project (restored session, or a
        layer the user deleted) is NOT the Original: its archived output is
        downloaded and added first, and the selection re-enters here once the
        layer exists (history.py._materialize_version_layer).
        """
        prev_index = self._selected_version_index
        version = self._versions[index] if 0 <= index < len(self._versions) else None
        if version is not None:
            self._selected_version_index = index
            # The prompt box follows the base: iterating on V2 starts from
            # V2's own prompt, not whatever the last edit typed.
            self._dock_widget.set_result_prompt_text(version.get("prompt") or "")
        # Original is the tile with no generation behind it, never just "a
        # tile missing its layer_id".
        is_original = version is None or not version.get("request_id")
        if not is_original and self._version_needs_layer(version):
            self._materialize_version_layer(index, prev_index=prev_index)
            return
        sel_layer_id = version["layer_id"] if not is_original else None
        try:
            self._sync_canvas_to_version(sel_layer_id)

            if self._map_tool is not None:
                if is_original:
                    self._pills_armed = False
                    self._disarm_swipe()
                    self._map_tool.hide_action_badges()
                else:
                    self._pills_armed = True
                    self._show_action_pills()
        except Exception as err:
            log_warning(f"version-select layer visibility sync failed: {err}")
        # Preview rule: moving from one browsed version to another replaces the
        # previous preview on the map instead of stacking layers. Selecting
        # Original only hides (so the way back stays instant), and committed or
        # user-touched layers are protected inside _drop_preview_layer.
        if (
            not is_original
            and prev_index != index
            and 0 <= prev_index < len(self._versions)
        ):
            self._drop_preview_layer(self._versions[prev_index])

    def _promote_selected_version(self) -> None:
        """The user is generating from the selected version: it graduated from
        browsed preview to base of new work, so it stays on the map."""
        if 0 <= self._selected_version_index < len(self._versions):
            self._versions[self._selected_version_index]["promoted"] = True

    def _promote_version_for_layer(self, layer_id: str) -> None:
        """Commitment expressed through a layer id (Vectorize acts on the
        layer): that version's preview becomes permanent."""
        if not layer_id:
            return
        for version in self._versions or []:
            if version.get("layer_id") == layer_id:
                version["promoted"] = True

    def _sync_canvas_to_version(self, sel_layer_id: str | None) -> None:
        """Show only ``sel_layer_id`` among the AI-Edit layers, hide the rest.

        ``None`` (Original) hides every AI result so the clean map shows
        through. The active Mark up layer is user guidance, not an AI edit, so it
        is kept checked. Mirrors the export base on the canvas and sets the
        selected version active so the pills act on it."""
        from qgis.core import QgsProject

        from ..layer_groups import set_ai_edit_layers_checked

        except_ids: set[str] = set()
        markup_id = self._markup_layer_id_if_any()
        if markup_id:
            except_ids.add(markup_id)
        # Hide all AI-Edit layers (other versions, vectorize overlays) but keep
        # markup, then re-check only the selected version.
        set_ai_edit_layers_checked(False, except_ids=except_ids)
        if sel_layer_id:
            root = QgsProject.instance().layerTreeRoot()
            node = root.findLayer(sel_layer_id)
            if node is not None:
                node.setItemVisibilityChecked(True)
            layer = QgsProject.instance().mapLayer(sel_layer_id)
            if layer is not None:
                try:
                    self._iface.setActiveLayer(layer)
                except Exception as err:  # nosec B110
                    log_warning(f"setActiveLayer on version select failed: {err}")

    def _markup_layer_id_if_any(self) -> str | None:
        """Return the active Mark up layer id, or None when there is no markup."""
        if self._markup_manager is None or self._markup_manager.annotation_count() <= 0:
            return None
        try:
            markup_layer = self._markup_manager.layer()
            return markup_layer.id() if markup_layer is not None else None
        except RuntimeError:
            return None

    def _on_zone_selected(self, extent: QgsRectangle, polygon: QgsGeometry | None = None):
        """A zone was committed on the canvas.

        ``polygon`` (canvas CRS) comes from PolygonSelectionTool.selection_made
        and is None on every path that isn't a fresh polygon draw: history
        restore (set_zone(rect)), and any programmatic/MCP extent. A None
        polygon renders as a plain rectangle with no context frame, and
        nothing downstream may assume it is ever non-None (spec section 4).
        """
        # The zone must land on the raster picked in the layer header: a zone
        # that misses it entirely would export blank pixels and bill them, so
        # it is refused here, before anything downstream reads it. A zone that
        # mostly hangs outside goes through with a heads-up.
        overlap = self._zone_layer_overlap_verdict(polygon if polygon is not None else extent)
        if overlap == OVERLAP_OUTSIDE:
            self._reject_zone_outside_layer()
            return
        if overlap == OVERLAP_PARTIAL:
            self._warn_zone_partly_outside_layer()

        self._selected_extent = extent
        self._selected_polygon = polygon
        # Keep markup clipped to the new zone if the manager already exists.
        # The polygon (when present) is the actual clip shape: a mark inside
        # the bbox but outside the polygon is rejected too.
        if self._markup_manager is not None:
            self._markup_manager.set_clip_zone(extent, polygon)
        # Fresh zone breaks the iteration chain (parent_request_id + armed template).
        self._last_completed_request_id = None
        self._reset_version_lineage()
        if self._dock_widget is not None:
            try:
                self._dock_widget.clear_active_template()
            except AttributeError:
                pass
            # Drop any Mark up reference baked at the previous zone extent so it
            # is not shipped as context for this new, differently-located zone.
            self._dock_widget.clear_markup_reference()
        self._show_selection_rectangle(extent, polygon)
        self._dock_widget.set_zone_selected()
        # Soft heads-up if the zone is very large on the ground or so zoomed
        # out the model won't resolve small features. Best-effort: never
        # blocks selection.
        try:
            from ..canvas_exporter import (
                estimate_native_ground_resolution_m,
                estimate_zone_area_km2,
            )
            gr = estimate_native_ground_resolution_m(
                self._canvas.mapSettings(), extent
            )
            area_km2 = estimate_zone_area_km2(
                extent, self._canvas.mapSettings().destinationCrs()
            )
            self._dock_widget.set_zone_guidance(gr, area_km2)
        except Exception:  # nosec B110 - advisory hint only.
            pass
        # Align reference renders to this zone so context layers line up with
        # the input image instead of the looser canvas view.
        try:
            zone_crs = self._canvas.mapSettings().destinationCrs()
            self._dock_widget.set_reference_target_extent(QgsRectangle(extent), zone_crs)
        except Exception:  # nosec B110 - alignment is best-effort, never blocks selection.
            pass
        # Captures the common case of drawing a zone without generating, which
        # would otherwise go unmeasured. Dimensions only, never coordinates.
        try:
            mupp = self._canvas.mapSettings().mapUnitsPerPixel()
            w_px = int(round(extent.width() / mupp)) if mupp else 0
            h_px = int(round(extent.height() / mupp)) if mupp else 0
            aspect_ratio = round(w_px / h_px, 3) if h_px else 0
            telemetry.track(te.ZONE_DRAWN, {
                "zone_width_px": w_px,
                "zone_height_px": h_px,
                "aspect_ratio": aspect_ratio,
            })
        except Exception:  # nosec B110 - telemetry must never block selection.
            pass
        log_debug("Zone selected")

    def _input_layer(self):
        """The raster picked in the dock's layer header, or None."""
        if self._dock_widget is None:
            return None
        try:
            return self._dock_widget.selected_input_layer()
        except (RuntimeError, AttributeError):
            return None

    def _zone_layer_overlap_verdict(self, zone) -> str:
        """OVERLAP_OUTSIDE / OVERLAP_PARTIAL / OVERLAP_OK for ``zone`` (canvas
        CRS) against the picked raster. No raster, no verdict."""
        layer = self._input_layer()
        if layer is None:
            return OVERLAP_OK
        try:
            return zone_layer_overlap(
                zone,
                self._canvas.mapSettings().destinationCrs(),
                layer.extent(),
                layer.crs(),
            )
        except Exception:  # nosec B110 - a guard that cannot judge lets the zone through.
            return OVERLAP_OK

    def _reject_zone_outside_layer(self) -> None:
        """Refuse a zone that misses the picked raster: clear the sketch the
        way the delete badge does, keep the draw tool armed, say why."""
        layer = self._input_layer()
        name = layer.name() if layer is not None else ""
        if self._map_tool is not None:
            self._map_tool.set_has_zone(False)
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._selected_polygon = None
        message = tr(
            'Your zone is outside "{layer}". Pick the right raster or draw inside it.'
        ).format(layer=name)
        if self._dock_widget is not None:
            # set_zone_cleared reopens the draw step and wipes the notice, so
            # the notice goes on after it, never before.
            self._dock_widget.set_zone_cleared()
            self._dock_widget.set_zone_step_notice(message)
        self._iface.messageBar().pushWarning("AI Edit", message)

    def _warn_zone_partly_outside_layer(self) -> None:
        layer = self._input_layer()
        name = layer.name() if layer is not None else ""
        self._iface.messageBar().pushInfo(
            "AI Edit",
            tr('Part of your zone is outside "{layer}". That part will come back blank.')
            .format(layer=name),
        )

    def _on_zone_too_small(self):
        try:
            canvas = self._canvas
            canvas_w = canvas.width() if canvas else 0
            min_pct = int(round(50 * 100 / canvas_w)) if canvas_w > 0 else 5
        except Exception:
            min_pct = 5
        message = tr(
            "Selected zone too small. Draw a rectangle at least "
            "{pct}% of the canvas size."
        ).format(pct=max(1, min_pct))
        self._dock_widget.set_status(message, is_error=True)
        self._dock_widget.set_zone_step_notice(message)

    def _on_zone_invalid(self, code: str, message: str):
        """Edge-zone refusal at draw time (antimeridian, polar, oversized,
        rotated map, invalid CRS). Surfaces a clear localized banner so the
        user can adjust before clicking Generate."""
        self._dock_widget.set_status(message, is_error=True)
        log_warning(f"Zone refused: {code} - {message}")

    def _on_zone_delete_requested(self):
        """Clear the current zone and return to the SELECTING_ZONE step.

        Triggered by right-click 'Clear zone', the badge button, or Escape
        from the prompt step. We keep the typed prompt and edit group so the
        user can redraw and continue iterating.
        """
        # The × pill doubles as the exit from a live comparison: stop the swipe
        # before tearing the zone down so the canvas is not left in swipe mode.
        self._disarm_swipe()
        self._pills_armed = False
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._selected_polygon = None
        # Clearing the zone breaks the iteration chain.
        self._last_completed_request_id = None
        self._reset_version_lineage()
        if self._map_tool is not None:
            self._map_tool.set_has_zone(False)
        # The captured Mark up reference belongs to the zone we are clearing.
        if self._dock_widget is not None:
            self._dock_widget.clear_markup_reference()
        self._dock_widget.set_zone_cleared()
        log_debug("Zone cleared")

    def _reset_version_lineage(self) -> None:
        """Start a fresh lineage: empty the version list and clear the strip.

        A new zone (or Exit then a new zone) is a new session, so mint a fresh
        session id here. Restore overrides it afterwards to re-enter a session."""
        # Browsed previews the user never committed to leave with the lineage.
        # The one they were looking at stays: leaving is not losing what was
        # on screen, and its file is indexed for an instant return anyway.
        for i, version in enumerate(self._versions or []):
            if i != self._selected_version_index:
                self._drop_preview_layer(version)
        self._versions = []
        self._selected_version_index = 0
        self._session_id = uuid.uuid4().hex
        # Invalidate any in-flight session-restore download: its thumbnails
        # must not seed a lineage the user has since broken.
        self._pending_session_rid = None
        if self._dock_widget is not None:
            try:
                self._dock_widget.reset_version_strip()
            except AttributeError:
                pass

    def _pixmap_from_b64(self, image_b64: str | None) -> QPixmap | None:
        """Decode the export's base64 bytes into a pixmap for the Original tile."""
        if not image_b64:
            return None
        try:
            import base64

            pixmap = QPixmap()
            pixmap.loadFromData(base64.b64decode(image_b64))
            return pixmap if not pixmap.isNull() else None
        except Exception as err:  # nosec B110 - a missing thumb is non-fatal.
            log_warning(f"version thumb decode failed: {err}")
            return None

    def _render_layer_thumb(self, layer) -> QPixmap | None:
        """Render a result raster layer to a pixmap for its version tile."""
        try:
            from ..layer_renderer import render_layers_to_qimage

            image = render_layers_to_qimage([layer])
            if image is None or image.isNull():
                return None
            return QPixmap.fromImage(image)
        except Exception as err:  # nosec B110 - a missing thumb is non-fatal.
            log_warning(f"version thumb render failed: {err}")
            return None

    def _selected_version_layer(self):
        """The raster layer of the selected version (or the newest version
        that has one). None when the lineage holds no on-map layer."""
        from qgis.core import QgsProject

        if not self._versions:
            return None
        candidates = []
        if 0 <= self._selected_version_index < len(self._versions):
            candidates.append(self._versions[self._selected_version_index])
        candidates.extend(reversed(self._versions))
        for version in candidates:
            layer_id = version.get("layer_id")
            if layer_id:
                layer = QgsProject.instance().mapLayer(layer_id)
                if layer is not None:
                    return layer
        return None

    # --- Selection rectangle management ---

    def _show_selection_rectangle(self, extent, polygon: QgsGeometry | None = None):
        """Render the committed zone's chrome: a blue outline, no fill.

        The zone is drawn as a frame and nothing else (owner call, 2026-07-30:
        the interior stays untouched imagery, see _ZONE_OUTLINE; and owner
        call, 2026-07-28: no dashed expanded-bbox "honesty" frame either, it
        read as clutter). With a polygon (a fresh draw) the outline follows the
        drawn shape; without one (history restore, MCP/dev extents, the
        pixel-aligned actual-export extent from generation.py) it frames the
        plain rectangle.

        The frame stays on for as long as the zone exists (owner call,
        2026-08-03), result on the map or not: a generated version, a browsed
        one, Original, a live Before/After and a restored session all keep it.
        It used to take itself off once a result landed, back when the zone
        carried a tinted interior that would have veiled that result; with
        only a stroke left there is nothing to hide from. The teardown paths
        alone remove it.
        """
        self._clear_selection_rectangle()
        if polygon is not None and not polygon.isEmpty():
            self._show_polygon_zone_bands(extent, polygon)
            return
        rb = QgsRubberBand(self._canvas, QtC.PolygonGeometry)
        rb.setColor(_ZONE_NO_FILL)
        rb.setStrokeColor(_ZONE_OUTLINE)
        rb.setWidth(_ZONE_OUTLINE_WIDTH)
        # Sit above the Before/After swipe overlay (zValue 100) so the zone
        # outline stays fully visible while the user swipes, instead of the
        # overlay covering its right half.
        rb.setZValue(110)
        for x, y, last in (
            (extent.xMinimum(), extent.yMinimum(), False),
            (extent.xMaximum(), extent.yMinimum(), False),
            (extent.xMaximum(), extent.yMaximum(), False),
            (extent.xMinimum(), extent.yMaximum(), True),
        ):
            rb.addPoint(QgsPointXY(x, y), last)
        self._selection_rubber_band = rb

    def _show_polygon_zone_bands(self, extent: QgsRectangle, polygon: QgsGeometry) -> None:
        """Polygon outline, fill-free (see _show_selection_rectangle).

        Fills the ``_selection_rubber_band`` slot that
        ``_clear_selection_rectangle`` already tears down generically; the
        ``_selection_rubber_band_halo`` slot stays None since the dashed
        bbox frame is gone, and the teardown keeps handling both slots so
        older state still clears.
        """
        band = QgsRubberBand(self._canvas, QtC.PolygonGeometry)
        band.setColor(_ZONE_NO_FILL)
        band.setStrokeColor(_ZONE_OUTLINE)
        band.setWidth(_ZONE_OUTLINE_WIDTH)
        band.setZValue(110)
        band.setToGeometry(polygon, None)
        self._selection_rubber_band = band

    def _redraw_zone_outline(self) -> None:
        """Re-assert the zone chrome after something else repainted the canvas
        (a result layer landing, a browsed version materializing).

        Draws against the geometry that is current now: the drawn polygon when
        the plugin still holds one, the rectangle otherwise. A restored session
        legitimately has only the rectangle, and framing that is the accepted
        outcome. No zone, nothing to draw.
        """
        extent = getattr(self, "_selected_extent", None)
        polygon = getattr(self, "_selected_polygon", None)
        has_polygon = polygon is not None and not polygon.isEmpty()
        if extent is None and not has_polygon:
            return
        self._show_selection_rectangle(extent, polygon)

    def _clear_selection_rectangle(self):
        for attr in ("_selection_rubber_band_halo", "_selection_rubber_band"):
            band = getattr(self, attr, None)
            if not band:
                continue
            try:
                scene = band.scene()
                if scene is not None:
                    scene.removeItem(band)
            except (RuntimeError, AttributeError):
                pass
            setattr(self, attr, None)
