









from __future__ import annotations

from .mcp_api_support import _never_raises, not_found_error



MARKUP_SHAPES = ("pencil", "line", "arrow", "circle")



MARKUP_ACTIONS = ("draw", "undo", "clear", "done")

_DEFAULT_MARKUP_RGB = (230, 0, 230)


class MarkupMixin:


    def _markup_manager(self):
        return getattr(self._plugin, "_markup_manager", None)

    def _enter_markup(self) -> dict | None:

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







        manager = self._markup_manager()
        count = 0
        will_render = False
        if manager is not None:
            try:
                count = int(manager.annotation_count())
                will_render = bool(manager.markup_will_render())
            except Exception:  # nosec B110
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


                outline = geometry.convertToType(LineGeometry, False)
                if outline is not None and not outline.isEmpty():
                    geometry = outline
                guessed = shape or "circle"
            elif kind not in (LineGeometry, PolygonGeometry):
                return {"_error": "A mark needs a line or a polygon, not a point."}
        except Exception:  # nosec B110
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
