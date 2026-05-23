









from __future__ import annotations

import os

from qgis.core import (
    QgsMapLayer,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProject,
    QgsRasterLayer,
)
from qgis.gui import QgsMapCanvasItem, QgsMapTool
from qgis.PyQt.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor, QFont, QFontMetrics, QImage, QPainter, QPen

from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.raster_writer import BEFORE_PATH_PROPERTY
from ..dock.design_tokens import FIXED_INK, qcolor
from ..icons import render_pixmap



_RENDER_DEBOUNCE_MS = 80


_GRIP_PX = 28
_GRIP_GLYPH_PX = 12
_GRIP_INK = FIXED_INK
_DIVIDER_HALO_ALPHA = 180

_ESC_HINT_DURATION_MS = 4000


_SIDE_LABEL_FONT_PX = 11
_SIDE_LABEL_PAD_X = 8
_SIDE_LABEL_PAD_Y = 3
_SIDE_LABEL_GAP_PX = 8

_KEY_STEP_PX = 10
_KEY_STEP_BIG_PX = 60


def _is_visible_raster(layer) -> bool:




    if not isinstance(layer, QgsRasterLayer):
        return False
    node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
    if node is None or not node.isVisible():
        return False
    return any(other.id() == layer.id() for _node, other in _iter_ai_edit_rasters())


def _iter_ai_edit_rasters() -> list:


    from ..layer_groups import AI_EDIT_GROUP_NAME

    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(AI_EDIT_GROUP_NAME)
    if group is None:
        return []
    pairs = []
    for node in group.findLayers():
        layer = node.layer()
        if isinstance(layer, QgsRasterLayer) and layer.isValid():
            pairs.append((node, layer))
    return pairs


def _ai_edit_layer_ids() -> set:

    from ..layer_groups import AI_EDIT_GROUP_NAME

    group = QgsProject.instance().layerTreeRoot().findGroup(AI_EDIT_GROUP_NAME)
    if group is None:
        return set()
    return {node.layerId() for node in group.findLayers()}


def _resolve_swipe_target() -> tuple:









    try:
        from qgis.utils import iface as _iface
        active = _iface.activeLayer() if _iface is not None else None
    except Exception:
        active = None
    if _is_visible_raster(active):
        return active, False
    pairs = _iter_ai_edit_rasters()
    for node, layer in pairs:
        if node.isVisible():
            return layer, False
    for _node, layer in pairs:
        return layer, True
    return None, False


class _SwipeOverlay(QgsMapCanvasItem):












    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self.setZValue(100)
        self._image: QImage | None = None
        self._top_layer: QgsMapLayer | None = None


        self._before_layer: QgsRasterLayer | None = None
        self._underlying_layers: list[QgsMapLayer] = []
        self._x_pos: int = -1
        self._job = None

    def set_top_layer(self, layer: QgsMapLayer | None) -> None:
        self._top_layer = layer
        self._load_before_layer()
        self._refresh_underlying()

    def has_true_before(self) -> bool:

        return self._before_layer is not None

    def _load_before_layer(self) -> None:



        self._before_layer = None
        if self._top_layer is None:
            return
        try:
            path = str(self._top_layer.customProperty(BEFORE_PATH_PROPERTY, "") or "")
        except (AttributeError, RuntimeError):
            return
        if not path or not os.path.exists(path):
            return
        candidate = QgsRasterLayer(path, "ai-edit-before")
        if candidate.isValid():
            self._before_layer = candidate

    def _refresh_underlying(self) -> None:




        if self._top_layer is None:
            self._underlying_layers = []
            return



        ai_edit_ids = _ai_edit_layer_ids()
        under = [
            lyr for lyr in self._canvas.layers()
            if lyr != self._top_layer and lyr.id() not in ai_edit_ids
        ]
        if self._before_layer is not None:
            under.insert(0, self._before_layer)
        self._underlying_layers = under

    def _safe_update_canvas(self) -> None:








        try:
            self.updateCanvas()
        except RuntimeError:  # nosec B110
            pass

    def clear(self) -> None:
        self._image = None
        self._x_pos = -1
        self._top_layer = None
        self._before_layer = None
        self._underlying_layers = []
        self._safe_update_canvas()

    def set_divider(self, x: int) -> None:

        self._x_pos = max(0, int(x))
        self._safe_update_canvas()

    def nudge_divider(self, dx: int) -> None:


        bounds = self._top_layer_pixel_bounds()
        x = (self._x_pos if self._x_pos >= 0 else self._canvas.width() // 2) + int(dx)
        if bounds is not None:
            x = max(bounds.left(), min(x, bounds.right()))
        self.set_divider(x)

    def cancel_pending_render(self) -> None:









        prev = self._job
        if prev is not None:
            try:
                prev.cancelWithoutBlocking()
            except Exception:  # nosec B110
                pass
            self._job = None

    def render_image(self) -> None:






        self._refresh_underlying()
        if self._top_layer is None:
            return

        settings = QgsMapSettings(self._canvas.mapSettings())
        settings.setLayers(self._underlying_layers)
        self.setRect(self._canvas.extent())



        prev = self._job
        if prev is not None:
            try:
                prev.cancelWithoutBlocking()
            except Exception:  # nosec B110
                pass

        job = QgsMapRendererParallelJob(settings)
        self._job = job
        job.finished.connect(lambda j=job: self._on_render_finished(j))
        job.start()

    def _on_render_finished(self, job=None) -> None:
        if job is not None and job is not self._job:
            return
        target = job if job is not None else self._job
        if target is None:
            return
        try:
            new_image = target.renderedImage()
        except RuntimeError:
            return
        if new_image is None or new_image.isNull():
            return
        self._image = new_image
        self._safe_update_canvas()

    def _top_layer_corner_pixels(self) -> list[tuple[float, float]] | None:









        if self._top_layer is None:
            return None
        try:
            extent = self._top_layer.extent()
        except (AttributeError, RuntimeError):
            return None
        if extent.isEmpty():
            return None
        try:
            ms = self._canvas.mapSettings()
            src_crs = self._top_layer.crs()
            dst_crs = ms.destinationCrs()
            corners = [
                (extent.xMinimum(), extent.yMaximum()),
                (extent.xMaximum(), extent.yMaximum()),
                (extent.xMaximum(), extent.yMinimum()),
                (extent.xMinimum(), extent.yMinimum()),
            ]
            if src_crs != dst_crs:
                from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsProject
                xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
                corners = [
                    (lambda p: (p.x(), p.y()))(xform.transform(QgsPointXY(x, y)))
                    for (x, y) in corners
                ]
            m2p = ms.mapToPixel()
            return [(m2p.transform(x, y).x(), m2p.transform(x, y).y()) for (x, y) in corners]
        except Exception:
            return None

    def _top_layer_pixel_bounds(self) -> QRect | None:




        corners = self._top_layer_corner_pixels()
        if not corners:
            return None
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        canvas_w = self._canvas.width()
        canvas_h = self._canvas.height()
        x_min = max(0, int(min(xs)))
        x_max = min(canvas_w, int(max(xs)))
        y_min = max(0, int(min(ys)))
        y_max = min(canvas_h, int(max(ys)))
        if x_max <= x_min or y_max <= y_min:
            return None
        return QRect(x_min, y_min, x_max - x_min, y_max - y_min)

    def paint(self, painter: QPainter, *args) -> None:  # noqa: ARG002

        if self._x_pos < 0:
            return
        bounds = self._top_layer_pixel_bounds()
        if bounds is None:
            return

        canvas_w = float(self._canvas.width())
        canvas_h = float(self._canvas.height())
        x_div = max(bounds.left(), min(int(self._x_pos), bounds.right()))
        y_top = bounds.top()
        y_bot = bounds.bottom()




        if self._image is not None:











            edge_pad = 3
            layer_clip = QRectF(
                bounds.left() - edge_pad,
                bounds.top() - edge_pad,
                bounds.width() + 2 * edge_pad,
                bounds.height() + 2 * edge_pad,
            )
            right_clip = QRectF(x_div, 0, canvas_w - x_div, canvas_h)
            clip = layer_clip.intersected(right_clip)
            if not clip.isEmpty():
                painter.save()
                painter.setClipRect(clip)
                painter.drawImage(0, 0, self._image)
                painter.restore()



        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        halo = QColor(Qt.GlobalColor.black)
        halo.setAlpha(_DIVIDER_HALO_ALPHA)
        halo_pen = QPen(halo)
        halo_pen.setWidth(4)
        painter.setPen(halo_pen)
        painter.drawLine(x_div, y_top, x_div, y_bot)
        core_pen = QPen(QColor(Qt.GlobalColor.white))
        core_pen.setWidth(2)
        painter.setPen(core_pen)
        painter.drawLine(x_div, y_top, x_div, y_bot)
        self._paint_grip(painter, x_div, (y_top + y_bot) / 2.0)
        self._paint_side_labels(painter, x_div, bounds)

    @staticmethod
    def _paint_side_labels(painter: QPainter, x: float, bounds: QRect) -> None:




        font = QFont(painter.font())
        font.setPixelSize(_SIDE_LABEL_FONT_PX)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        height = _SIDE_LABEL_FONT_PX + 2 * _SIDE_LABEL_PAD_Y
        top = bounds.top() + _SIDE_LABEL_GAP_PX
        if top + height > bounds.bottom():
            return
        pill = QColor(Qt.GlobalColor.black)
        pill.setAlpha(_DIVIDER_HALO_ALPHA)
        for text, on_left in (
            (get_export_copy("widgets.swipe_panel.result_side", tr("Result")), True),
            (get_export_copy("widgets.swipe_panel.original_side", tr("Original")), False),
        ):
            width = metrics.horizontalAdvance(text) + 2 * _SIDE_LABEL_PAD_X
            left = x - _SIDE_LABEL_GAP_PX - width if on_left else x + _SIDE_LABEL_GAP_PX
            if left < bounds.left() or left + width > bounds.right():
                continue
            rect = QRectF(left, top, width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(pill)
            painter.drawRoundedRect(rect, height / 2.0, height / 2.0)
            painter.setPen(QPen(QColor(Qt.GlobalColor.white)))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)

    @staticmethod
    def _paint_grip(painter: QPainter, x: float, y: float) -> None:


        radius = _GRIP_PX / 2.0
        if radius <= 0:
            return
        halo = QColor(Qt.GlobalColor.black)
        halo.setAlpha(_DIVIDER_HALO_ALPHA)
        painter.setPen(QPen(halo, 1.5))
        painter.setBrush(QColor(Qt.GlobalColor.white))
        painter.drawEllipse(QPointF(x, y), radius, radius)
        ink = qcolor(_GRIP_INK)
        glyph = _GRIP_GLYPH_PX
        for name, dx in (("chevron_left", -glyph + 2), ("chevron_right", -2)):
            pixmap = render_pixmap(name, ink, glyph, 2.0)
            painter.drawPixmap(QRectF(x + dx, y - glyph / 2.0, glyph, glyph), pixmap,
                               QRectF(pixmap.rect()))


class _SwipeSignals(QObject):
    escape_pressed = pyqtSignal()


class _SwipeMapTool(QgsMapTool):







    def __init__(self, canvas, overlay: _SwipeOverlay, on_overlay_click=None):
        super().__init__(canvas)
        self._canvas = canvas
        self._overlay = overlay
        self._dragging = False
        self.signals = _SwipeSignals()





        self._on_overlay_click = on_overlay_click

    def activate(self) -> None:
        super().activate()
        self._canvas.setCursor(QCursor(Qt.CursorShape.SplitHCursor))



        try:
            self._canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:  # nosec B110
            pass
        self._overlay.render_image()



        bounds = self._overlay._top_layer_pixel_bounds()  # noqa: SLF001
        if bounds is not None:
            self._overlay.set_divider(bounds.center().x())
        else:
            self._overlay.set_divider(self._canvas.width() // 2)

    def deactivate(self) -> None:
        self._dragging = False
        self._canvas.unsetCursor()
        super().deactivate()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.signals.escape_pressed.emit()
            e.accept()
            return


        step = self._key_step(e)
        if step is not None:
            self._overlay.nudge_divider(step)



            e.ignore()
            return



        super().keyPressEvent(e)

    @staticmethod
    def _key_step(e):


        key = e.key()
        big = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        step = _KEY_STEP_BIG_PX if big else _KEY_STEP_PX
        if key == Qt.Key.Key_Left:
            return -step
        if key == Qt.Key.Key_Right:
            return step
        if key == Qt.Key.Key_Home:
            return -100000
        if key == Qt.Key.Key_End:
            return 100000
        return None

    def canvasPressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return


        if self._on_overlay_click is not None:
            try:
                if self._on_overlay_click(self._event_point(e)):
                    e.accept()
                    return
            except Exception:  # nosec B110
                pass
        self._dragging = True
        self._overlay.set_divider(self._event_x(e))

    def canvasMoveEvent(self, e) -> None:  # noqa: N802
        if self._dragging:
            self._overlay.set_divider(self._event_x(e))

    def canvasReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = False

    @staticmethod
    def _event_x(ev) -> int:

        if hasattr(ev, "position"):
            try:
                return int(ev.position().x())
            except (TypeError, AttributeError):
                pass
        return int(ev.pos().x())

    @staticmethod
    def _event_point(ev) -> QPoint:
        if hasattr(ev, "position"):
            try:
                return ev.position().toPoint()
            except (TypeError, AttributeError):
                pass
        return ev.pos()


class SwipeController(QObject):









    activated = pyqtSignal()
    deactivated = pyqtSignal()


    eligibility_changed = pyqtSignal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._overlay: _SwipeOverlay | None = None
        self._tool: _SwipeMapTool | None = None
        self._previous_tool = None
        self._top_layer_id: str | None = None


        self._revealed_layer_id: str | None = None
        self._extents_connected: bool = False
        self._iface_layer_connected: bool = False
        self._project_connected: bool = False
        self._maptool_set_connected: bool = False
        self._render_debounce_timer: QTimer | None = None


        self._eligibility_root = None


        self._eligibility_timer = QTimer(self)
        self._eligibility_timer.setSingleShot(True)
        self._eligibility_timer.timeout.connect(self._emit_eligibility_now)


        self._connect_eligibility_tracker()



    def is_active(self) -> bool:
        return self._tool is not None

    def can_swipe_now(self) -> bool:


        target, _was_hidden = _resolve_swipe_target()
        return target is not None

    def has_true_before(self) -> bool:


        return self._overlay is not None and self._overlay.has_true_before()

    def toggle(self) -> None:
        if self.is_active():
            self.stop()
        else:
            self.start()

    def start(self, on_overlay_click=None) -> None:
        if self.is_active():
            return
        try:
            from qgis.utils import iface as _iface
        except ImportError:  # pragma: no cover
            return
        if _iface is None:
            return
        target, was_hidden = _resolve_swipe_target()
        if target is None:
            return
        if was_hidden:



            node = QgsProject.instance().layerTreeRoot().findLayer(target.id())
            if node is not None:
                node.setItemVisibilityChecked(True)
                self._revealed_layer_id = target.id()
        if _iface.activeLayer() is not target:
            try:
                _iface.setActiveLayer(target)
            except Exception:  # nosec B110
                pass

        canvas = _iface.mapCanvas()
        self._previous_tool = canvas.mapTool()
        self._top_layer_id = target.id()

        self._overlay = _SwipeOverlay(canvas)
        self._overlay.set_top_layer(target)
        self._bring_result_into_view(canvas, target)
        self._tool = _SwipeMapTool(canvas, self._overlay, on_overlay_click)


        self._tool.signals.escape_pressed.connect(self.stop)
        canvas.setMapTool(self._tool)





        self._render_debounce_timer = QTimer(self)
        self._render_debounce_timer.setSingleShot(True)
        self._render_debounce_timer.timeout.connect(self._overlay.render_image)
        canvas.extentsChanged.connect(self._on_extents_changed)
        self._extents_connected = True





        try:
            canvas.mapToolSet.connect(self._on_maptool_set)
            self._maptool_set_connected = True
        except (TypeError, RuntimeError):
            pass

        self._connect_active_layer_tracker()
        self._connect_project_signals()
        self.activated.emit()
        try:
            from qgis.utils import iface as _iface_msg
            if _iface_msg is not None:
                _iface_msg.statusBarIface().showMessage(
                    get_export_copy(
                        "widgets.swipe_panel.compare_hint_short",
                        tr("Drag the line or use the arrow keys. Esc stops."),
                    ),
                    get_export_dial("widgets.swipe_panel.esc_hint_duration_ms", _ESC_HINT_DURATION_MS),
                )
        except Exception:  # nosec B110
            pass

    def _bring_result_into_view(self, canvas, target) -> None:



        if self._overlay is None or self._overlay._top_layer_pixel_bounds() is not None:  # noqa: SLF001
            return
        try:
            from qgis.core import QgsCoordinateTransform

            extent = target.extent()
            dest = canvas.mapSettings().destinationCrs()
            if target.crs() != dest:
                extent = QgsCoordinateTransform(
                    target.crs(), dest, QgsProject.instance()
                ).transformBoundingBox(extent)
            if extent.isEmpty():
                return
            canvas.setExtent(extent.buffered(extent.width() * 0.05))
            canvas.refresh()
        except Exception:  # nosec B110
            pass

    def _on_extents_changed(self) -> None:



        if self._overlay is not None:
            self._overlay.cancel_pending_render()
        if self._render_debounce_timer is not None:
            self._render_debounce_timer.start(
                get_export_dial("widgets.swipe_panel.render_debounce_ms", _RENDER_DEBOUNCE_MS)
            )

    def _on_maptool_set(self, new_tool, _old=None) -> None:





        if new_tool is self._tool:
            return
        if not self.is_active():
            return
        self._previous_tool = None
        self.stop()

    def _destroy_debounce_timer(self) -> None:









        timer = self._render_debounce_timer
        self._render_debounce_timer = None
        if timer is None:
            return

        try:
            timer.stop()
        except RuntimeError:  # nosec B110
            pass
        try:
            timer.timeout.disconnect()
        except (RuntimeError, TypeError):  # nosec B110
            pass
        try:
            timer.deleteLater()
        except (RuntimeError, AttributeError):  # nosec B110
            pass

    def stop(self) -> None:
        if not self.is_active():
            return

        try:
            from qgis.utils import iface as _iface_msg
            if _iface_msg is not None:
                _iface_msg.statusBarIface().clearMessage()
        except Exception:  # nosec B110
            pass
        try:
            from qgis.utils import iface as _iface
        except ImportError:  # pragma: no cover
            _iface = None
        try:
            canvas = _iface.mapCanvas() if _iface is not None else None
        except RuntimeError:  # nosec B110
            canvas = None

        self._disconnect_active_layer_tracker()
        self._disconnect_project_signals()

        if canvas is not None and self._extents_connected:
            try:
                canvas.extentsChanged.disconnect(self._on_extents_changed)
            except (TypeError, RuntimeError):
                pass
        if canvas is not None and self._maptool_set_connected:
            try:
                canvas.mapToolSet.disconnect(self._on_maptool_set)
            except (TypeError, RuntimeError):
                pass
        self._destroy_debounce_timer()
        self._extents_connected = False
        self._maptool_set_connected = False

        if self._overlay is not None:




            try:
                self._overlay.cancel_pending_render()
            except RuntimeError:
                pass
            self._overlay.clear()
            try:
                scene = canvas.scene() if canvas is not None else None
                if scene is not None:
                    scene.removeItem(self._overlay)
            except RuntimeError:
                pass



            self._overlay = None

        tool = self._tool
        if canvas is not None and tool is not None:



            try:
                if self._previous_tool is not None:
                    canvas.setMapTool(self._previous_tool)
                else:
                    canvas.unsetMapTool(tool)
            except RuntimeError:
                try:
                    canvas.unsetMapTool(tool)
                except RuntimeError:  # nosec B110
                    pass
        if tool is not None:


            try:
                tool.signals.escape_pressed.disconnect(self.stop)
            except (RuntimeError, TypeError):
                pass
            try:
                tool.deleteLater()
            except (RuntimeError, AttributeError):
                pass
        self._tool = None
        self._previous_tool = None
        self._top_layer_id = None
        if self._revealed_layer_id is not None:
            try:
                node = QgsProject.instance().layerTreeRoot().findLayer(
                    self._revealed_layer_id
                )
                if node is not None:
                    node.setItemVisibilityChecked(False)
            except Exception:  # nosec B110
                pass
            self._revealed_layer_id = None
        self.deactivated.emit()

    def cleanup(self) -> None:

        self.stop()
        self._disconnect_eligibility_tracker()



    def _connect_active_layer_tracker(self) -> None:
        if self._iface_layer_connected:
            return
        try:
            from qgis.utils import iface as _iface
            if _iface is not None:
                _iface.currentLayerChanged.connect(self._on_active_layer_changed)
                self._iface_layer_connected = True
        except (ImportError, TypeError, RuntimeError):
            pass

    def _disconnect_active_layer_tracker(self) -> None:
        if not self._iface_layer_connected:
            return
        try:
            from qgis.utils import iface as _iface
            if _iface is not None:
                _iface.currentLayerChanged.disconnect(self._on_active_layer_changed)
        except (ImportError, TypeError, RuntimeError):
            pass
        self._iface_layer_connected = False

    def _on_active_layer_changed(self, layer) -> None:




        if not self.is_active():
            return
        if not _is_visible_raster(layer):
            return
        if self._top_layer_id == layer.id():
            return
        self._top_layer_id = layer.id()
        if self._overlay is not None:
            self._overlay.set_top_layer(layer)
            self._overlay.render_image()



    def _connect_project_signals(self) -> None:
        if self._project_connected:
            return
        try:
            QgsProject.instance().layersWillBeRemoved.connect(
                self._on_layers_will_be_removed
            )
            self._project_connected = True
        except (TypeError, RuntimeError):
            pass

    def _disconnect_project_signals(self) -> None:
        if not self._project_connected:
            return
        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(
                self._on_layers_will_be_removed
            )
        except (TypeError, RuntimeError):
            pass
        self._project_connected = False

    def _on_layers_will_be_removed(self, layer_ids) -> None:
        if self._top_layer_id is not None and self._top_layer_id in layer_ids:
            self.stop()



    def _connect_eligibility_tracker(self) -> None:


        try:
            from qgis.utils import iface as _iface
            if _iface is not None:
                _iface.currentLayerChanged.connect(self._emit_eligibility)
        except (ImportError, TypeError, RuntimeError):
            pass
        try:
            project = QgsProject.instance()
            project.layersAdded.connect(self._emit_eligibility)
            project.layersRemoved.connect(self._emit_eligibility)


            project.readProject.connect(self._on_project_loaded)
            project.cleared.connect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass
        self._connect_eligibility_root()

    def _connect_eligibility_root(self) -> None:





        try:
            root = QgsProject.instance().layerTreeRoot()
            root.visibilityChanged.connect(self._emit_eligibility)
            root.addedChildren.connect(self._emit_eligibility)
            root.removedChildren.connect(self._emit_eligibility)
            self._eligibility_root = root
        except (TypeError, RuntimeError):
            self._eligibility_root = None

    def _disconnect_eligibility_root(self) -> None:

        if self._eligibility_root is None:
            return
        for signal in (
            self._eligibility_root.visibilityChanged,
            self._eligibility_root.addedChildren,
            self._eligibility_root.removedChildren,
        ):
            try:
                signal.disconnect(self._emit_eligibility)
            except (TypeError, RuntimeError):
                pass
        self._eligibility_root = None

    def _on_project_loaded(self, *_args) -> None:

        self._disconnect_eligibility_root()
        self._connect_eligibility_root()
        self._emit_eligibility()

    def _disconnect_eligibility_tracker(self) -> None:
        try:
            from qgis.utils import iface as _iface
            if _iface is not None:
                _iface.currentLayerChanged.disconnect(self._emit_eligibility)
        except (ImportError, TypeError, RuntimeError):
            pass


        try:
            project = QgsProject.instance()
        except RuntimeError:
            project = None
        if project is not None:
            for signal, slot in (
                (project.layersAdded, self._emit_eligibility),
                (project.layersRemoved, self._emit_eligibility),
                (project.readProject, self._on_project_loaded),
                (project.cleared, self._on_project_loaded),
            ):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._disconnect_eligibility_root()

    def _emit_eligibility(self, *_args) -> None:
        self._eligibility_timer.start(0)

    def _emit_eligibility_now(self) -> None:
        self.eligibility_changed.emit(self.can_swipe_now())
