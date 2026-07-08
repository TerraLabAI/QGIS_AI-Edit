from __future__ import annotations

import time
import uuid

from qgis.core import QgsApplication, QgsGeometry, QgsPointXY, QgsRectangle
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
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.logger import log_debug, log_warning
from ..dock.blocked_reasons import LAUNCH_BLOCK_WORKER_BUSY





_ZONE_OUTLINE = QColor(65, 105, 225, 220)
_ZONE_NO_FILL = QColor(0, 0, 0, 0)
_ZONE_OUTLINE_WIDTH = 2


_NOTIFY_EXIT_HISTORY_HINT_S = 6


class ZoneVersionsMixin:
    def _activate_selection_tool(self):

        if self._canvas.mapTool() != self._map_tool:
            current_tool = self._canvas.mapTool()
            if current_tool:
                self._previous_map_tool = current_tool
            self._canvas.setMapTool(self._map_tool)
        self._dock_widget.set_status("")

    def _deactivate_selection_tool(self):






        if self._previous_map_tool is not None and self._canvas is not None:
            try:
                self._canvas.setMapTool(self._previous_map_tool)
            except (RuntimeError, AttributeError):  # nosec B110
                pass
        elif self._canvas is not None and self._map_tool is not None:
            try:
                if self._canvas.mapTool() is self._map_tool:
                    self._canvas.unsetMapTool(self._map_tool)
            except (RuntimeError, AttributeError):  # nosec B110
                pass
        self._previous_map_tool = None

    def _cancel_generation_and_reset_zone(self):





        self._disarm_swipe()
        self._pills_armed = False


        self._size_request_token = None
        size_task = getattr(self, "_export_size_task", None)
        self._export_size_task = None
        if size_task is not None:
            try:
                size_task.cancel()
            except RuntimeError:  # nosec B110
                pass
        if self._worker is not None and self._worker.is_active():
            if not self._generation_cancel_handled:
                duration = time.time() - getattr(self, "_generation_start_time", time.time())
                telemetry.track(te.GENERATION_CANCELLED, self._enrich_generation_props({
                    "duration_ms": int(duration * 1000),
                    "resolution": getattr(self, "_last_suggested_res", ""),
                }))
                telemetry.flush()



            self._generation_service.cancel()


            self._generation_cancel_handled = True



            try:
                self._worker.cancel()
            except Exception:  # nosec B110
                pass



        self._cleanup_worker()



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







        self._cancel_generation_and_reset_zone()





        self._dock_widget.set_launch_state()

    def _on_launch_shortcut(self):

        if self._dock_widget is None:
            return
        if not self._dock_widget.isVisible():
            self._dock_widget.setVisible(True)
            self._ensure_dock_height()
        self._dock_widget.raise_()
        self._on_launch_clicked()

    def _on_launch_clicked(self):



        if self._dock_widget.is_generation_in_flight():
            self._dock_widget.set_launch_block_reason(LAUNCH_BLOCK_WORKER_BUSY)
            return
        telemetry.track(te.LAUNCH_CLICKED)

        self._maybe_show_tutorial_nudge()
        self._disarm_swipe()
        self._activate_selection_tool()
        self._dock_widget.set_selecting_zone_state()

    def _on_exit_clicked(self):




        had_generation = len(self._versions or []) > 1
        self._cancel_generation_and_reset_zone()




        self._reset_version_lineage()


        self._dock_widget.set_launch_state()
        self._reset_version_lineage()
        if had_generation:


            self._notify(
                get_export_copy(
                    "flows.zone_versions.session_kept_in_sessions",
                    tr("Your session is saved. Reopen it from Sessions, the clock at the top."),
                ),
                duration=get_export_dial(
                    "flows.zone_versions.notify_exit_history_hint_s", _NOTIFY_EXIT_HISTORY_HINT_S
                ),
            )

    def _on_project_layers_changed(self, *_args):







        if self._dock_widget is None:
            return
        QtC.safe_single_shot(0, self._dock_widget, self._reset_canvas_if_empty)

    def _reset_canvas_if_empty(self):











        from qgis.core import QgsProject

        if self._dock_widget is None or self._canvas is None:
            return

        if self._worker is not None and self._worker.is_active():
            return
        root = QgsProject.instance().layerTreeRoot()



        shared_zone_id = self._shared_zone_layer_id()
        has_visible = any(
            node.isVisible() for node in root.findLayers()
            if node.layer() is not None and node.layerId() != shared_zone_id
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













        prev_index = self._selected_version_index
        version = self._versions[index] if 0 <= index < len(self._versions) else None
        if version is not None:
            self._selected_version_index = index


            self._dock_widget.set_result_prompt_text(version.get("prompt") or "")


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




        if (
            not is_original
            and prev_index != index
            and 0 <= prev_index < len(self._versions)
        ):
            self._drop_preview_layer(self._versions[prev_index])

    def _promote_selected_version(self) -> None:


        if 0 <= self._selected_version_index < len(self._versions):
            self._versions[self._selected_version_index]["promoted"] = True

    def _promote_version_for_layer(self, layer_id: str) -> None:


        if not layer_id:
            return
        for version in self._versions or []:
            if version.get("layer_id") == layer_id:
                version["promoted"] = True

    def _sync_canvas_to_version(self, sel_layer_id: str | None) -> None:






        from qgis.core import QgsProject

        from ..layer_groups import set_ai_edit_layers_checked

        except_ids: set[str] = set()
        markup_id = self._markup_layer_id_if_any()
        if markup_id:
            except_ids.add(markup_id)


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

        if self._markup_manager is None or self._markup_manager.annotation_count() <= 0:
            return None
        try:
            markup_layer = self._markup_manager.layer()
            return markup_layer.id() if markup_layer is not None else None
        except RuntimeError:
            return None

    def _on_zone_selected(self, extent: QgsRectangle, polygon: QgsGeometry | None = None):













        zone_label = str(getattr(self, "_pending_zone_label", "") or "")
        self._pending_zone_label = ""




        overlap = self._zone_layer_overlap_verdict(polygon if polygon is not None else extent)
        if overlap == OVERLAP_OUTSIDE:
            self._reject_zone_outside_layer()
            return
        if overlap == OVERLAP_PARTIAL:
            self._warn_zone_partly_outside_layer()

        self._selected_extent = extent
        self._selected_polygon = polygon



        if self._markup_manager is not None:
            self._markup_manager.set_clip_zone(extent, polygon)

        self._last_completed_request_id = None
        self._reset_version_lineage()
        if self._dock_widget is not None:
            try:
                self._dock_widget.clear_active_template()
            except AttributeError:
                pass


            self._dock_widget.clear_markup_reference()
        self._show_selection_rectangle(extent, polygon)
        self._dock_widget.set_zone_selected()



        try:
            from ..canvas_exporter import estimate_zone_area_km2

            area_km2 = estimate_zone_area_km2(
                extent, self._canvas.mapSettings().destinationCrs()
            )
            self._dock_widget.set_zone_guidance(None, area_km2)
            self._request_zone_ground_resolution(QgsRectangle(extent), area_km2)
        except Exception:  # nosec B110
            pass


        try:
            zone_crs = self._canvas.mapSettings().destinationCrs()
            self._dock_widget.set_reference_target_extent(QgsRectangle(extent), zone_crs)
        except Exception:  # nosec B110
            pass
        self._attach_layers_above(extent)


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
        except Exception:  # nosec B110
            pass
        self._publish_shared_zone(zone_label)
        log_debug("Zone selected")

    def _request_zone_ground_resolution(self, extent, area_km2) -> None:




        from ...workers.generic_request_task import GenericRequestTask
        from ..canvas_exporter import ground_resolution_for_size, native_size_inputs

        settings = self._canvas.mapSettings()
        inputs = native_size_inputs(settings, extent)
        auth = self._auth_manager.get_auth_header()
        if not inputs or not auth:
            return

        def _on_size(result, ext=extent, ms=settings, area=area_km2):
            if self._dock_widget is None or self._selected_extent is None:
                return
            if QgsRectangle(self._selected_extent) != ext:
                return
            if not isinstance(result, dict):
                return
            w, h = result.get("width"), result.get("height")
            if not (isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0):
                return
            gr = ground_resolution_for_size(ms, ext, w, h)
            try:
                self._dock_widget.set_zone_guidance(gr, area)
            except RuntimeError:  # nosec B110
                pass

        task = GenericRequestTask(
            "AI Edit zone size",
            lambda c=self._client, a=auth, i=inputs: c.get_export_size(a, i),
            silent=True,
        )
        task.succeeded.connect(_on_size)
        self._zone_size_task = task
        QgsApplication.taskManager().addTask(task)

    def _publish_shared_zone(self, label: str = "") -> None:

















        from ...core import zone_of_interest as zoi

        polygon = getattr(self, "_selected_polygon", None)
        extent = getattr(self, "_selected_extent", None)
        if polygon is not None and not polygon.isEmpty():
            geometry = QgsGeometry(polygon)
        elif extent is not None:
            geometry = QgsGeometry.fromRect(QgsRectangle(extent))
        else:
            return
        try:
            crs = self._canvas.mapSettings().destinationCrs()
            zoi.write_zone(geometry, crs, label=str(label or ""))
        except Exception as err:  # nosec B110
            log_debug(f"Shared zone not published: {err}")

    def _shared_zone_layer_id(self) -> str:







        try:
            from ...core import zone_of_interest as zoi

            layer = zoi.zone_layer()
            return layer.id() if layer is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def _on_zone_source_picked(self, kind: str, layer_id: str = "") -> None:









        from qgis.core import QgsProject

        from ...core import zone_of_interest as zoi

        if self._dock_widget is not None and self._dock_widget.is_generation_in_flight():
            self._dock_widget.set_launch_block_reason(LAUNCH_BLOCK_WORKER_BUSY)
            return
        if str(kind) == "zone":
            zone = zoi.read_zone()
            if zone is None:
                self._report_zone_source_gone()
                return
            geometry, crs, label = zone.geometry, zone.crs, zone.label
        else:
            layer = QgsProject.instance().mapLayer(str(layer_id or ""))
            found = zoi.outline_of_layer(layer, selected_only=str(kind) == "selection")
            if found is None:
                self._report_zone_source_gone()
                return
            geometry, crs, _approximate = found
            label = layer.name()
        self._commit_zone_from_geometry(geometry, crs, label)

    def _report_zone_source_gone(self) -> None:

        message = tr("That zone is no longer in the project. Pick another one.")
        if self._dock_widget is None:
            return
        self._dock_widget.set_zone_step_notice(message)
        self._dock_widget.refresh_zone_sources()

    def _commit_zone_from_geometry(self, geometry, crs, label: str = "") -> None:









        from qgis.core import QgsProject

        from ...core import zone_of_interest as zoi

        canvas_crs = self._canvas.mapSettings().destinationCrs()
        moved = zoi.to_crs(geometry, crs, canvas_crs, QgsProject.instance())
        if moved is None or moved.isEmpty():
            if self._dock_widget is not None:
                self._dock_widget.set_zone_step_notice(
                    tr("That zone cannot be placed on this map. Draw one instead.")
                )
            return
        self._pending_zone_label = str(label or "")
        self._activate_selection_tool()
        api = getattr(self, "mcp_api", None)
        set_zone = getattr(api, "set_zone", None) if api is not None else None
        if callable(set_zone):


            outcome = set_zone(polygon_wkt=moved.asWkt())
            failure = str(outcome.get("_error") or "") if isinstance(outcome, dict) else ""
        else:
            failure = self._commit_zone_polygon_directly(moved)
        if failure:
            self._pending_zone_label = ""
            if self._dock_widget is not None:
                self._dock_widget.set_zone_step_notice(failure)
            return
        if self._map_tool is not None and self._selected_extent is not None:



            try:
                self._map_tool.set_zone(QgsRectangle(self._selected_extent))
            except Exception as err:  # nosec B110
                log_warning(f"zone rect arm after pick failed: {err}")

    def _commit_zone_polygon_directly(self, polygon: QgsGeometry) -> str:




        from ...core.errors import AIEditError
        from ..canvas_exporter import validate_zone
        from ..tools.polygon_selection_tool import (
            PolygonSelectionTool,
            _repair_polygon,
            expand_bbox_to_ratio_and_min_size,
        )

        shape = _repair_polygon(polygon)
        if shape is None:
            return tr("That shape is too thin or too small to edit. Pick another one.")
        try:
            mupp = float(self._canvas.mapSettings().mapUnitsPerPixel())
        except Exception:  # noqa: BLE001
            mupp = 0.0
        extent = expand_bbox_to_ratio_and_min_size(
            shape.boundingBox(), mupp, PolygonSelectionTool.MIN_SIZE_PX
        )
        try:
            validate_zone(
                extent,
                self._canvas.mapSettings().destinationCrs(),
                self._canvas.rotation(),
            )
        except AIEditError as err:
            return err.message or tr("This zone cannot be used.")
        except Exception:  # nosec B110
            pass
        self._on_zone_selected(extent, shape)
        return ""

    def _attach_layers_above(self, extent) -> None:










        if self._dock_widget is None:
            return
        try:
            from ...core.canvas_export.input_render_set import layers_above_input
            from ..layer_groups import collect_ai_edit_layer_ids
            from ..layer_renderer import layer_misses_zone

            settings = self._canvas.mapSettings()
            zone_crs = settings.destinationCrs()
            markup_layer = None
            if self._markup_manager is not None:
                markup_layer = self._markup_manager.layer()



            skip_ids = set(collect_ai_edit_layer_ids())
            shared_zone_id = self._shared_zone_layer_id()
            if shared_zone_id:
                skip_ids.add(shared_zone_id)
            above = [
                layer
                for layer in layers_above_input(
                    settings.layers(),
                    self._input_layer(),
                    skip_ids,
                    markup_layer=markup_layer,
                )
                if not layer_misses_zone([layer], extent, zone_crs)
            ]
            self._dock_widget.set_reference_layers_above(above)
        except Exception as err:  # nosec B110
            log_debug(f"Layers above the input not attached: {err}")

    def _input_layer(self):

        if self._dock_widget is None:
            return None
        try:
            return self._dock_widget.selected_input_layer()
        except (RuntimeError, AttributeError):
            return None

    def _zone_layer_overlap_verdict(self, zone) -> str:


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
        except Exception:  # nosec B110
            return OVERLAP_OK

    def _reject_zone_outside_layer(self) -> None:


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
            "Your zone is too small. Draw it at least {pct}% of the map width."
        ).format(pct=max(1, min_pct))
        self._dock_widget.set_status(message, is_error=True)
        self._dock_widget.set_zone_step_notice(message)

    def _on_zone_invalid(self, code: str, message: str):



        self._dock_widget.set_status(message, is_error=True)
        log_warning(f"Zone refused: {code} - {message}")

    def _on_zone_delete_requested(self):








        self._disarm_swipe()
        self._pills_armed = False
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._selected_polygon = None

        self._last_completed_request_id = None
        self._reset_version_lineage()
        if self._map_tool is not None:
            self._map_tool.set_has_zone(False)

        if self._dock_widget is not None:
            self._dock_widget.clear_markup_reference()
        self._dock_widget.set_zone_cleared()
        log_debug("Zone cleared")

    def _reset_version_lineage(self) -> None:







        for i, version in enumerate(self._versions or []):
            if i != self._selected_version_index:
                self._drop_preview_layer(version)
        self._versions = []
        self._selected_version_index = 0
        self._session_id = uuid.uuid4().hex


        self._pending_session_rid = None
        if self._dock_widget is not None:
            try:
                self._dock_widget.reset_version_strip()
            except AttributeError:
                pass

    def _pixmap_from_b64(self, image_b64: str | None) -> QPixmap | None:

        if not image_b64:
            return None
        try:
            import base64

            pixmap = QPixmap()
            pixmap.loadFromData(base64.b64decode(image_b64))
            return pixmap if not pixmap.isNull() else None
        except Exception as err:  # nosec B110
            log_warning(f"version thumb decode failed: {err}")
            return None

    def _render_layer_thumb(self, layer) -> QPixmap | None:

        try:
            from ..layer_renderer import render_layers_to_qimage

            image = render_layers_to_qimage([layer])
            if image is None or image.isNull():
                return None
            return QPixmap.fromImage(image)
        except Exception as err:  # nosec B110
            log_warning(f"version thumb render failed: {err}")
            return None

    def _selected_version_layer(self):


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



    def _show_selection_rectangle(self, extent, polygon: QgsGeometry | None = None):


















        self._clear_selection_rectangle()
        if polygon is not None and not polygon.isEmpty():
            self._show_polygon_zone_bands(extent, polygon)
            return
        rb = QgsRubberBand(self._canvas, QtC.PolygonGeometry)
        rb.setColor(_ZONE_NO_FILL)
        rb.setStrokeColor(_ZONE_OUTLINE)
        rb.setWidth(_ZONE_OUTLINE_WIDTH)



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








        band = QgsRubberBand(self._canvas, QtC.PolygonGeometry)
        band.setColor(_ZONE_NO_FILL)
        band.setStrokeColor(_ZONE_OUTLINE)
        band.setWidth(_ZONE_OUTLINE_WIDTH)
        band.setZValue(110)
        band.setToGeometry(polygon, None)
        self._selection_rubber_band = band

    def _redraw_zone_outline(self) -> None:








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
