"""The mark up group of the AI Edit public API.

Mark up is drawing on the map to show the model where an instruction applies.
A circle round one roof, an arrow at one junction, a line along one wall. The
marks are drawn into the picture sent with the prompt, and the model is told to
read them as pointers and leave no trace of them in the result.

Reach for it when the where is hard to put into words. "Repaint the roof I
circled" beats three sentences of directions.
"""
from __future__ import annotations

from .mcp_api_support import _never_raises, not_found_error

# The four kinds of mark the panel offers. "pencil" is a free line, "line" a
# straight run of segments, "arrow" points at one spot, "circle" rings one.
MARKUP_SHAPES = ("pencil", "line", "arrow", "circle")

# What markup() can be asked to do. "draw" adds one mark, "done" hands the map
# back; the marks survive either way until the next run consumes them.
MARKUP_ACTIONS = ("draw", "undo", "clear", "done")

_DEFAULT_MARKUP_RGB = (230, 0, 230)


class MarkupMixin:
    """Drawing on the zone, and clearing or finishing those marks."""

    def _markup_manager(self):
        return getattr(self._plugin, "_markup_manager", None)

    def _enter_markup(self) -> dict | None:
        """Put the panel into Mark up mode. Returns an error dict, or None."""
        if getattr(self._plugin, "_in_tool_panel", None) == "markup":
            return None
        enter = getattr(self._plugin, "_on_markup_clicked", None)
        if not callable(enter):
            return {"_error": "Mark up is not available. Select a zone first."}
        self._open_dock()
        enter()
        return None

    @_never_raises
    def markup_status(self) -> dict:
        """Report the marks drawn on the zone. Costs nothing, no network call.

        Returns ``active`` (True while the panel is in Mark up mode),
        ``annotation_count``, ``will_render`` (False when the marks would not
        reach the picture, usually because the layer was hidden), ``shapes``,
        the kinds of mark ``markup()`` draws, and ``actions``, what it accepts.
        """
        manager = self._markup_manager()
        count = 0
        will_render = False
        if manager is not None:
            try:
                count = int(manager.annotation_count())
                will_render = bool(manager.markup_will_render())
            except Exception:  # nosec B110 - a missing manager means no marks.
                pass
        return {
            "active": getattr(self._plugin, "_in_tool_panel", None) == "markup",
            "annotation_count": count,
            "will_render": will_render,
            "shapes": list(MARKUP_SHAPES),
            "actions": list(MARKUP_ACTIONS),
        }

    @_never_raises
    def markup(
        self,
        action: str,
        geometry_wkt: str | None = None,
        color: str | None = None,
        shape: str | None = None,
    ) -> dict:
        """Draw on the zone to show the model where to work. Costs nothing.

        ``action`` is one of:

        * "draw" adds one mark from ``geometry_wkt``, a line or a polygon
          outline in the map canvas CRS, in ``color`` (a colour name or
          "#rrggbb", bright magenta by default). ``shape`` says how the model
          should read it: "pencil" a free line, "line" a straight run, "arrow"
          a pointer, "circle" a ring round something. Left out, it is guessed
          from the geometry. A polygon is traced as its own outline, the same
          ring the Circle tool draws.
        * "undo" removes the last mark.
        * "clear" removes every mark.
        * "done" closes Mark up mode and hands the map back. The marks stay on
          screen and are drawn into the picture at the next ``generate()``,
          then dropped once the result arrives. Nothing is saved by "done", so
          it is safe to skip it and call ``generate()`` straight away.

        Returns ``ok``, the ``action`` taken and ``annotation_count``. A mark
        that falls entirely outside the zone is refused, and ``ok`` is False.
        """
        from qgis.core import QgsGeometry
        from qgis.PyQt.QtGui import QColor

        from .core.qt_compat import LineGeometry, PolygonGeometry

        action = (action or "").strip().lower()
        if action not in MARKUP_ACTIONS:
            return not_found_error(
                "mark up action", action, MARKUP_ACTIONS,
                means="action says what to do with the marks, not what to draw.",
            )
        if shape is not None:
            shape = str(shape).strip().lower()
            if shape not in MARKUP_SHAPES:
                return not_found_error(
                    "mark shape", shape, MARKUP_SHAPES,
                    means="shape says how the model should read the mark.",
                )

        # Only drawing needs the tool. Entering for "undo" or "clear" opened
        # the panel and armed a map tool on the user's canvas, to then take
        # something away.
        if action == "draw":
            entered = self._enter_markup()
            if entered is not None:
                return entered

        manager = self._markup_manager()

        if action == "clear":
            handler = getattr(self._plugin, "_on_markup_clear_clicked", None)
            if callable(handler):
                handler()
            elif manager is not None:
                manager.clear_all()
            else:
                return {"_error": "Mark up is not active."}
            count = manager.annotation_count() if manager is not None else 0
            return {"ok": True, "action": "clear", "annotation_count": count}

        if action == "undo":
            handler = getattr(self._plugin, "_on_markup_undo", None)
            # The panel's own undo does nothing outside Mark up mode, and this
            # call no longer enters that mode, so drive the manager instead.
            in_markup = getattr(self._plugin, "_in_tool_panel", None) == "markup"
            if callable(handler) and in_markup:
                handler()
            elif manager is not None:
                manager.undo_last()
            else:
                return {"_error": "Mark up is not active."}
            count = manager.annotation_count() if manager is not None else 0
            return {"ok": True, "action": "undo", "annotation_count": count}

        if action == "done":
            handler = getattr(self._plugin, "_on_markup_done_clicked", None)
            if not callable(handler):
                return {"_error": "The Mark up done handler is not available."}
            handler()
            return {
                "ok": True,
                "action": "done",
                "note": (
                    "Mark up mode is closed. The marks stay and are drawn into the "
                    "picture at the next generate()."
                ),
                "hint": "Call generate(prompt) to send the marks with your instruction.",
            }

        if not geometry_wkt:
            return {"_error": "geometry_wkt is required to draw a mark."}
        if manager is None or not hasattr(manager, "commit"):
            return {"_error": "Mark up is not active. Select a zone first."}

        geometry = QgsGeometry.fromWkt(str(geometry_wkt))
        if geometry is None or geometry.isEmpty():
            return {"_error": "geometry_wkt is not a readable geometry."}
        guessed = shape or "pencil"
        try:
            kind = geometry.type()
            if kind == PolygonGeometry:
                # The marks are stored as lines, so a polygon is traced as its
                # own outline, which draws the same ring the Circle tool makes.
                outline = geometry.convertToType(LineGeometry, False)
                if outline is not None and not outline.isEmpty():
                    geometry = outline
                guessed = shape or "circle"
            elif kind not in (LineGeometry, PolygonGeometry):
                return {"_error": "A mark needs a line or a polygon, not a point."}
        except Exception:  # nosec B110 - fall through with the geometry as given.
            pass

        pen = QColor(str(color)) if color else QColor(*_DEFAULT_MARKUP_RGB)
        if not pen.isValid():
            pen = QColor(*_DEFAULT_MARKUP_RGB)
        before = manager.annotation_count()
        manager.commit(geometry, pen, guessed)
        after = manager.annotation_count()
        return {
            "ok": after > before,
            "action": "draw",
            "shape": guessed,
            "annotation_count": after,
            "note": None if after > before else "The mark fell outside the zone.",
            "hint": (
                "Call generate(prompt) to send this mark with your instruction."
                if after > before
                else "Call get_zone() to read the zone, then draw inside it."
            ),
        }
