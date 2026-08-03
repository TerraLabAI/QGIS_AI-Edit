"""Before/after swipe tool.

Headless controller that arms/disarms a canvas swipe map tool. There is
no dock panel; the user toggles the swipe through the dock's footer
Before/After button. The swipe target is whatever raster is the active
layer in the QGIS Layers panel; selecting a different raster retargets
the swipe live. Esc on the canvas disarms the tool. Middle-mouse drag
pans without leaving swipe mode.
"""
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
from qgis.PyQt.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor, QImage, QPainter, QPen

from ...core.i18n import tr
from ...core.raster_writer import BEFORE_PATH_PROPERTY


def _is_visible_raster(layer) -> bool:
    """The swipe accepts any visible raster (AI-Edit output or not)."""
    if not isinstance(layer, QgsRasterLayer):
        return False
    node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
    return node is not None and node.isVisible()


def _iter_ai_edit_rasters() -> list:
    """(tree node, raster) pairs under the AI-Edit group, tree order (the
    group inserts newest generations first)."""
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


def _resolve_swipe_target() -> tuple:
    """The raster the swipe should compare, plus whether it is currently
    hidden and must be re-shown to arm.

    The active layer wins when it is a visible raster. Otherwise fall back to
    the newest AI-Edit result: tools routinely leave a VECTOR layer active
    (Vectorize even hides the source raster), which used to permanently grey
    out Before/After until the user manually re-picked a raster - the
    "compare no longer works" report. Returns ``(layer | None, was_hidden)``.
    """
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
    """Canvas overlay that paints the underlying layers (everything except
    the AI-Edit top layer) on the right side of a vertical divider,
    revealing what sits beneath the generated raster.

    The canvas itself keeps rendering normally with every checked layer,
    so the AI-Edit raster stays visible on the LEFT half. The overlay
    paints over the RIGHT half with an opaque render of the underlying
    layers, hiding the AI-Edit raster from that half. No layer-tree
    visibility toggles are needed, so the layer-tree-driven combo never
    loses sight of the swiped layer.
    """

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self.setZValue(100)
        self._image: QImage | None = None
        self._top_layer: QgsMapLayer | None = None
        # Private georeferenced copy of the imagery the swiped generation ran
        # FROM (never added to the project); painted as the before side.
        self._before_layer: QgsRasterLayer | None = None
        self._underlying_layers: list[QgsMapLayer] = []
        self._x_pos: int = -1  # -1 disables painting
        self._job = None

    def set_top_layer(self, layer: QgsMapLayer | None) -> None:
        self._top_layer = layer
        self._load_before_layer()
        self._refresh_underlying()

    def has_true_before(self) -> bool:
        """True when the before side paints the generation's own input."""
        return self._before_layer is not None

    def _load_before_layer(self) -> None:
        """Resolve the swiped layer's stored before GeoTIFF, if any. Kept as
        an instance attribute so the render job's layer reference stays alive
        (a non-project layer is owned by whoever holds it)."""
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
        """Layer set of the before render: the canvas stack without ANY
        AI-Edit result (not just the swiped one - with stacked generations
        the older result is not the before, it is another after), topped by
        the generation's own input image when it is on disk."""
        if self._top_layer is None:
            self._underlying_layers = []
            return
        ai_edit_ids = {layer.id() for _node, layer in _iter_ai_edit_rasters()}
        under = [
            lyr for lyr in self._canvas.layers()
            if lyr != self._top_layer and lyr.id() not in ai_edit_ids
        ]
        if self._before_layer is not None:
            under.insert(0, self._before_layer)
        self._underlying_layers = under

    def _safe_update_canvas(self) -> None:
        """Request a repaint, tolerating an item that already left the scene.

        Guarded HERE rather than at each caller: at QGIS shutdown the canvas
        scene is torn down before the plugin unloads, and an unguarded repaint
        request raised out of stop() before it could remove the item, delete
        the map tool or emit deactivated - leaving Before/After lit while the
        canvas was in another mode.
        """
        try:
            self.updateCanvas()
        except RuntimeError:  # nosec B110 - C++ half or scene already gone
            pass

    def clear(self) -> None:
        self._image = None
        self._x_pos = -1
        self._top_layer = None
        self._before_layer = None
        self._underlying_layers = []
        self._safe_update_canvas()

    def set_divider(self, x: int) -> None:
        """Set the divider X (in widget coords) and request a repaint."""
        self._x_pos = max(0, int(x))
        self._safe_update_canvas()

    def cancel_pending_render(self) -> None:
        """Cancel the in-flight render without dropping the cached image.

        Keeping the old image painted during the transition window means
        the swipe overlay stays VISIBLE while a pan/zoom is in progress.
        The image content is briefly for the previous extent (slightly
        misaligned for a few hundred ms), but the top layer never shows
        on the right side of the divider, which is the contract the
        user expects from a "before / after" swipe.
        """
        prev = self._job
        if prev is not None:
            try:
                prev.cancelWithoutBlocking()
            except Exception:  # nosec B110
                pass
            self._job = None

    def render_image(self) -> None:
        """Render the underlying layers using an exact copy of the canvas
        mapSettings: same extent, CRS, DPR, DPI, flags, size. The image
        ends up pixel-identical to the canvas's own render of the same
        layers at the same moment, so when the swipe is steady the
        overlay is indistinguishable from the canvas. The old image is
        kept painted while the new job runs (no flash of the top layer)."""
        self._refresh_underlying()
        if self._top_layer is None:
            return

        settings = QgsMapSettings(self._canvas.mapSettings())
        settings.setLayers(self._underlying_layers)
        self.setRect(self._canvas.extent())

        # Cancel previous job (without blocking, its finished signal
        # is filtered by the `job is not self._job` guard below).
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
        """Project the swiped raster's 4 corners into canvas pixel coords.

        For a raster whose CRS matches the canvas, this is an axis-aligned
        rectangle. For a raster in a different CRS (typical case: layer
        in EPSG:4326, canvas in EPSG:3857), the projected footprint is a
        non-axis-aligned quad; the AABB of that quad inflates beyond the
        actual rendered area, which is the "overflow on the edges" the
        user sees. Returning the quad lets us clip to the exact footprint.
        """
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
        """Axis-aligned canvas-widget rect covering the raster footprint.
        Used to centre the initial divider and to compute the right-of-
        divider width; the actual clipping uses the tighter polygon path.
        """
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
        # *args swallows the extra (option, widget) Qt6 passes; Qt5 omits them.
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

        # Paint the right-side reveal only once the underlying render has
        # landed. The divider line below is drawn regardless, so pressing
        # Compare shows the split immediately while the image fills in.
        if self._image is not None:
            # Clip to (raster bbox + small edge pad) ∩ right of divider.
            # The pad covers the canvas's anti-aliased edges of the top
            # layer so they don't peek through. Outside the layer, the
            # canvas's native render shows directly. No overlay anywhere
            # else means no visual difference between "swipe on" and
            # "swipe off" outside the raster area.
            # No painter transform: the image is rendered for the canvas's
            # current extent, drawn at the same widget coords the canvas
            # uses. Stale during a transient render but never offset by an
            # animation, so the user sees a momentarily out-of-date overlay
            # rather than one that drifts around the layer.
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

        # Divider line, scoped to the raster's vertical span only.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        halo_pen = QPen(QColor(0, 0, 0, 180))
        halo_pen.setWidth(4)
        painter.setPen(halo_pen)
        painter.drawLine(x_div, y_top, x_div, y_bot)
        core_pen = QPen(QColor("#FFFFFF"))
        core_pen.setWidth(2)
        painter.setPen(core_pen)
        painter.drawLine(x_div, y_top, x_div, y_bot)


class _SwipeSignals(QObject):
    escape_pressed = pyqtSignal()


class _SwipeMapTool(QgsMapTool):
    """QgsMapTool that drives the swipe overlay from mouse position.

    Left-button drag = move the divider, full stop. To pan without
    leaving swipe mode, use middle-mouse drag (handled natively by
    QgsMapCanvas). Press Esc to exit swipe.
    """

    def __init__(self, canvas, overlay: _SwipeOverlay, on_overlay_click=None):
        super().__init__(canvas)
        self._canvas = canvas
        self._overlay = overlay
        self._dragging = False
        self.signals = _SwipeSignals()
        # Optional callback(canvas_point) -> bool. Lets the post-generation
        # action pills (× / Compare / Vectorize) stay clickable while the
        # swipe owns the canvas: the controller forwards every press here, and
        # if the callback claims the point (a pill was hit) we skip the divider
        # move. Returns True when handled.
        self._on_overlay_click = on_overlay_click

    def activate(self) -> None:
        super().activate()
        self._canvas.setCursor(QCursor(Qt.CursorShape.SplitHCursor))
        # Grab keyboard focus so Esc reaches this tool. Without it,
        # focus can stay on the dock and the user's key presses get
        # swallowed by another widget.
        try:
            self._canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:  # nosec B110
            pass
        self._overlay.render_image()
        # Start the divider at the horizontal centre of the swiped
        # raster's footprint, falling back to canvas centre if the
        # raster isn't on-screen yet.
        bounds = self._overlay._top_layer_pixel_bounds()  # noqa: SLF001
        if bounds is not None:
            self._overlay.set_divider(bounds.center().x())
        else:
            self._overlay.set_divider(self._canvas.width() // 2)

    def deactivate(self) -> None:
        self._dragging = False
        self._canvas.unsetCursor()
        super().deactivate()

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt signature
        if e.key() == Qt.Key.Key_Escape:
            self.signals.escape_pressed.emit()
            e.accept()
            return
        # Space used to toggle a pan mode here; it bugged the swipe state
        # and middle-mouse drag (handled natively by QgsMapCanvas) covers
        # the same use case more reliably.
        super().keyPressEvent(e)

    def canvasPressEvent(self, e) -> None:  # noqa: N802 - Qt signature
        if e.button() != Qt.MouseButton.LeftButton:
            return
        # Let the action pills claim the click before it moves the divider, so
        # × / Compare / Vectorize stay live during a comparison.
        if self._on_overlay_click is not None:
            try:
                if self._on_overlay_click(self._event_point(e)):
                    e.accept()
                    return
            except Exception:  # nosec B110
                pass
        self._dragging = True
        self._overlay.set_divider(self._event_x(e))

    def canvasMoveEvent(self, e) -> None:  # noqa: N802 - Qt signature
        if self._dragging:
            self._overlay.set_divider(self._event_x(e))

    def canvasReleaseEvent(self, e) -> None:  # noqa: N802 - Qt signature
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = False

    @staticmethod
    def _event_x(ev) -> int:
        # Qt6 uses position() (QPointF); Qt5 uses pos() (QPoint).
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
    """Headless controller: arm or disarm the swipe map tool on the canvas.

    There is no dock panel. The user toggles the swipe via the footer
    Before/After button. The swipe target is the QGIS Layers panel's
    active layer (must be a visible AI-Edit output); picking a different
    AI-Edit layer while the swipe is on retargets it live. Press Esc on
    the canvas to disarm.
    """

    activated = pyqtSignal()
    deactivated = pyqtSignal()
    # True when the current iface.activeLayer() is a swipeable AI-Edit
    # output. The dock button uses this to enable/disable itself.
    eligibility_changed = pyqtSignal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._overlay: _SwipeOverlay | None = None
        self._tool: _SwipeMapTool | None = None
        self._previous_tool = None
        self._top_layer_id: str | None = None
        # Result raster that start() had to re-show (hidden by Vectorize);
        # stop() hides it back so the user returns to the state they left.
        self._revealed_layer_id: str | None = None
        self._extents_connected: bool = False
        self._iface_layer_connected: bool = False
        self._project_connected: bool = False
        self._maptool_set_connected: bool = False
        self._render_debounce_timer: QTimer | None = None
        # The layerTreeRoot the eligibility tracker is bound to (project
        # open/clear replaces the root, so disconnect must target this one).
        self._eligibility_root = None
        # Always-on eligibility tracker so the button enable state
        # follows the active layer even when the swipe is off.
        self._connect_eligibility_tracker()

    # ----- public API ----------------------------------------------------

    def is_active(self) -> bool:
        return self._tool is not None

    def can_swipe_now(self) -> bool:
        """True when a swipe target can be resolved: the active layer if it is
        a visible raster, else any AI-Edit result raster in the project."""
        target, _was_hidden = _resolve_swipe_target()
        return target is not None

    def has_true_before(self) -> bool:
        """True while an armed swipe paints the generation's own input as the
        before side (False when off, or on the underlying-layers fallback)."""
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
        except ImportError:  # pragma: no cover - non-QGIS env
            return
        if _iface is None:
            return
        target, was_hidden = _resolve_swipe_target()
        if target is None:
            return  # Caller should have gated this on can_swipe_now()
        if was_hidden:
            # Vectorize hides the result raster once it is traced; comparing
            # is exactly the moment to reveal it again. Remembered so stop()
            # re-hides it and the user returns to their polygons untouched.
            node = QgsProject.instance().layerTreeRoot().findLayer(target.id())
            if node is not None:
                node.setItemVisibilityChecked(True)
                self._revealed_layer_id = target.id()
        if _iface.activeLayer() is not target:
            try:
                _iface.setActiveLayer(target)
            except Exception:  # nosec B110 - retarget is cosmetic
                pass

        canvas = _iface.mapCanvas()
        self._previous_tool = canvas.mapTool()
        self._top_layer_id = target.id()

        self._overlay = _SwipeOverlay(canvas)
        self._overlay.set_top_layer(target)
        self._tool = _SwipeMapTool(canvas, self._overlay, on_overlay_click)
        # Esc on the canvas disarms the swipe; the controller routes that
        # back into stop() so button state stays in sync.
        self._tool.signals.escape_pressed.connect(self.stop)
        canvas.setMapTool(self._tool)

        # On extentsChanged: drop the stale image immediately so the
        # overlay disappears (canvas shows the base layers untouched)
        # then debounce the new render so rapid pan/zoom doesn't spawn
        # 30 jobs per second.
        self._render_debounce_timer = QTimer(self)
        self._render_debounce_timer.setSingleShot(True)
        self._render_debounce_timer.timeout.connect(self._overlay.render_image)
        canvas.extentsChanged.connect(self._on_extents_changed)
        self._extents_connected = True

        # If QGIS swaps the map tool out from under us (e.g. user clicks
        # "Launch AI Edit" which activates the selection tool), the swipe
        # button would stay visually checked but the canvas would be in a
        # different mode. Listen for that and exit cleanly.
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
                    tr("Press Esc to exit Before/After mode"), 4000
                )
        except Exception:  # nosec B110
            pass

    def _on_extents_changed(self) -> None:
        # Keep the old image visible (transient misalignment > flashing
        # the top layer on the swiped side). Cancel any in-flight render
        # and schedule a fresh one; the old image stays until it lands.
        if self._overlay is not None:
            self._overlay.cancel_pending_render()
        if self._render_debounce_timer is not None:
            self._render_debounce_timer.start(80)

    def _on_maptool_set(self, new_tool, _old=None) -> None:
        # Disarm cleanly if anything else (Launch AI Edit, Mark up panel,
        # an external plugin) takes the canvas. Avoids the "button stays
        # green but canvas is in pan mode" inconsistency. `_previous_tool`
        # gets cleared so stop() doesn't try to restore it on top of the
        # new tool the user explicitly picked.
        if new_tool is self._tool:
            return
        if not self.is_active():
            return
        self._previous_tool = None
        self.stop()

    def _destroy_debounce_timer(self) -> None:
        """Stop, unhook and delete the render debounce timer.

        QTimer(self) is a CHILD of this controller, so dropping the Python
        reference leaves the C++ timer alive and still connected to the
        previous overlay's render_image. A _SwipeOverlay is a QgsMapCanvasItem
        (a QGraphicsItem, not a QObject), so PyQt keeps a strong reference to
        it as the receiver: every start/stop cycle used to strand one timer
        plus one overlay shell for the rest of the session.
        """
        timer = self._render_debounce_timer
        self._render_debounce_timer = None
        if timer is None:
            return
        # One try per step: a raise on the first must not skip the delete.
        try:
            timer.stop()
        except RuntimeError:  # nosec B110 - C++ timer already gone
            pass
        try:
            timer.timeout.disconnect()
        except (RuntimeError, TypeError):  # nosec B110 - nothing connected
            pass
        try:
            timer.deleteLater()
        except (RuntimeError, AttributeError):  # nosec B110
            pass

    def stop(self) -> None:
        if not self.is_active():
            return
        try:
            from qgis.utils import iface as _iface
        except ImportError:  # pragma: no cover - non-QGIS env
            _iface = None
        try:
            canvas = _iface.mapCanvas() if _iface is not None else None
        except RuntimeError:  # nosec B110 - main window already torn down
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
            # Cancel the in-flight render FIRST. Its finished slot calls
            # updateCanvas() on this item, and a job landing after the item
            # left the scene runs on a dead sip wrapper (Esc inside the 80 ms
            # pan/zoom debounce is the reproducer).
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
            # removeItem hands ownership back to Python; a QgsMapCanvasItem is
            # a QGraphicsItem, not a QObject, so dropping the reference here is
            # what destroys the C++ half (there is no deleteLater to call).
            self._overlay = None

        tool = self._tool
        if canvas is not None and tool is not None:
            # A canvas whose C++ half is gone raises on BOTH calls, and the old
            # shape let the fallback raise straight out of stop(), skipping the
            # tool delete and the deactivated signal below.
            try:
                if self._previous_tool is not None:
                    canvas.setMapTool(self._previous_tool)
                else:
                    canvas.unsetMapTool(tool)
            except RuntimeError:
                try:
                    canvas.unsetMapTool(tool)
                except RuntimeError:  # nosec B110 - canvas already gone
                    pass
        if tool is not None:
            # QgsMapTool parents itself to the canvas, so the C++ tool outlives
            # this controller and pins the plugin graph unless it is deleted.
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
            except Exception:  # nosec B110 - re-hiding is cosmetic; the emit is not
                pass
            self._revealed_layer_id = None
        self.deactivated.emit()

    def cleanup(self) -> None:
        """Tear down all signal connections; call on plugin unload."""
        self.stop()
        self._disconnect_eligibility_tracker()

    # ----- internals: live retarget --------------------------------------

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
        """While the swipe is on, follow the user's layer pick, provided
        the new layer is also a swipeable AI-Edit output. Non-eligible
        picks are ignored so the swipe stays on its previous target.
        """
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

    # ----- internals: layer removal safety -------------------------------

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

    # ----- internals: button enable/disable tracker ----------------------

    def _connect_eligibility_tracker(self) -> None:
        # Eligibility depends on the active layer AND on which rasters exist /
        # are visible (the fallback target), so track all three change sources.
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
            # New-project / open-project replace the layerTreeRoot (see
            # tools_footer._on_project_loaded), so rebind on project lifecycle.
            project.readProject.connect(self._on_project_loaded)
            project.cleared.connect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass
        self._connect_eligibility_root()

    def _connect_eligibility_root(self) -> None:
        """Bind tree-change signals on the current layerTreeRoot and remember
        it. addedChildren matters because `layersAdded` fires while the layer
        has no tree node yet (addMapLayer(layer, False), THEN insertLayer into
        the AI-Edit group): recomputing only on layersAdded reads a tree the
        result is not in yet and leaves the button stale-disabled."""
        try:
            root = QgsProject.instance().layerTreeRoot()
            root.visibilityChanged.connect(self._emit_eligibility)
            root.addedChildren.connect(self._emit_eligibility)
            root.removedChildren.connect(self._emit_eligibility)
            self._eligibility_root = root
        except (TypeError, RuntimeError):
            self._eligibility_root = None

    def _disconnect_eligibility_root(self) -> None:
        """Unbind from the root we actually connected to, not the current one."""
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
        """Re-bind to the fresh layerTreeRoot and re-evaluate eligibility."""
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
        # One try per signal: batching them meant the first raise (a signal
        # already disconnected) skipped every later one.
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
        self.eligibility_changed.emit(self.can_swipe_now())
