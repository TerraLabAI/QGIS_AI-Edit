from __future__ import annotations

import time
import uuid

from qgis.core import QgsPointXY, QgsRectangle
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtGui import QColor, QPixmap

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.i18n import tr
from ...core.logger import log_debug, log_warning


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
        """Restore the map tool that was active before selection started."""
        if self._previous_map_tool:
            try:
                self._canvas.setMapTool(self._previous_map_tool)
            except RuntimeError:
                pass
        self._previous_map_tool = None

    def _on_stop(self):
        """Dock closing mid-generation: cancel work and clear zone state.

        Triggered by the dock's closeEvent (title-bar X). The Exit button has
        its own handler that also returns the dock to LAUNCH - see
        _on_exit_clicked.
        """
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
        self._last_image_b64 = None
        self._last_guidance_b64 = None
        self._last_guidance_format = None
        if self._map_tool:
            self._map_tool.set_has_zone(False)
        self._deactivate_selection_tool()
        # Reset the DOCK VIEW, not just the data. Setting _generation_cancel_handled
        # above suppresses _on_generation_task_terminated (which would otherwise
        # call set_generating(False)), so without this the dock stays stuck on the
        # generating view after a Stop. We cleared the zone, so LAUNCH is the
        # coherent landing state - same as the Exit sibling (_on_exit_clicked).
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
        telemetry.track(te.LAUNCH_CLICKED)
        # First real commitment: nudge new users toward the tutorial (once ever).
        self._maybe_show_tutorial_nudge()
        self._disarm_swipe()
        self._activate_selection_tool()
        self._dock_widget.set_selecting_zone_state()

    def _on_exit_clicked(self):
        """User clicked Exit / Done: cancel work and return to LAUNCH."""
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
        self._last_image_b64 = None
        self._last_guidance_b64 = None
        self._last_guidance_format = None
        if self._map_tool:
            self._map_tool.set_has_zone(False)
        self._deactivate_selection_tool()
        # Mark up annotations persist across sessions on a single shared layer.
        # User wipes them explicitly via the Clear all button.
        self._dock_widget.set_launch_state()

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
        flow (SELECTING_ZONE or ZONE_SELECTED) would leave the rectangle tool
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
        """
        if 0 <= index < len(self._versions):
            self._selected_version_index = index
        sel_layer_id = self._versions[index]["layer_id"] if 0 <= index < len(self._versions) else None
        is_original = sel_layer_id is None
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

    def _on_zone_selected(self, extent: QgsRectangle):
        self._selected_extent = extent
        # Keep markup clipped to the new zone if the manager already exists.
        if self._markup_manager is not None:
            self._markup_manager.set_clip_zone(extent)
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
        self._show_selection_rectangle(extent)
        self._dock_widget.set_zone_selected()
        # Soft heads-up if the zone is so zoomed out the model won't resolve
        # small features. Best-effort: never blocks selection.
        try:
            from ..canvas_exporter import estimate_native_ground_resolution_m
            gr = estimate_native_ground_resolution_m(
                self._canvas.mapSettings(), extent
            )
            self._dock_widget.set_zone_guidance(gr)
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

    def _on_zone_too_small(self):
        try:
            canvas = self._canvas
            canvas_w = canvas.width() if canvas else 0
            min_pct = int(round(50 * 100 / canvas_w)) if canvas_w > 0 else 5
        except Exception:
            min_pct = 5
        self._dock_widget.set_status(
            tr(
                "Selected zone too small. Draw a rectangle at least "
                "{pct}% of the canvas size."
            ).format(pct=max(1, min_pct)),
            is_error=True,
        )

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

    def _show_selection_rectangle(self, extent):
        self._clear_selection_rectangle()
        rb = QgsRubberBand(self._canvas, QtC.PolygonGeometry)
        rb.setColor(QColor(0, 0, 0, 0))
        rb.setStrokeColor(QColor(65, 105, 225, 180))
        rb.setWidth(2)
        # Sit above the Before/After swipe overlay (zValue 100) so the blue zone
        # frame stays fully visible on all four sides while the user swipes,
        # instead of the overlay covering its right half.
        rb.setZValue(110)
        for x, y, last in (
            (extent.xMinimum(), extent.yMinimum(), False),
            (extent.xMaximum(), extent.yMinimum(), False),
            (extent.xMaximum(), extent.yMaximum(), False),
            (extent.xMinimum(), extent.yMaximum(), True),
        ):
            rb.addPoint(QgsPointXY(x, y), last)
        self._selection_rubber_band = rb

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
