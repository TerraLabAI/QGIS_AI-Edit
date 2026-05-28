





from __future__ import annotations

from qgis.core import QgsPointXY
from qgis.PyQt.QtWidgets import QMenu

from ...core.config_store import get_export_copy
from ...core.i18n import tr
from .selection_map_tool import _ZoneActionBadge, _ZoneDeleteBadge


class ZoneBadgeOverlayMixin:


    def _show_zone_context_menu(self, event) -> None:
        pos = event.globalPos() if hasattr(event, "globalPos") else event.globalPosition().toPoint()



        menu = QMenu(self._canvas)
        menu.addAction(
            get_export_copy("widgets.polygon_selection_tool.clear_zone_menu", tr("Clear zone")),
            self._on_delete_zone,
        )
        menu.exec(pos)
        menu.deleteLater()



    def _badge_anchor(self) -> QgsPointXY:





        rect = self._zone_rect
        if self._zone_polygon is not None and not self._zone_polygon.isEmpty():
            points = [QgsPointXY(v.x(), v.y()) for v in self._zone_polygon.vertices()]
        else:
            points = []
        if not points:
            points = [
                QgsPointXY(rect.xMaximum(), rect.yMaximum()),
                QgsPointXY(rect.xMinimum(), rect.yMaximum()),
                QgsPointXY(rect.xMinimum(), rect.yMinimum()),
                QgsPointXY(rect.xMaximum(), rect.yMinimum()),
            ]
        try:
            to_screen = self.canvas().getCoordinateTransform()
            screen = [to_screen.transform(p) for p in points]
        except Exception:  # noqa: BLE001
            return QgsPointXY(rect.xMaximum(), rect.yMaximum())
        right = max(p.x() for p in screen)
        top = min(p.y() for p in screen)
        best = min(
            range(len(points)),
            key=lambda i: (screen[i].x() - right) ** 2 + (screen[i].y() - top) ** 2,
        )
        return points[best]

    def _show_delete_badge(self) -> None:




        if self._zone_rect is None or self._compare_active:
            return
        if self._delete_badge is None:
            self._delete_badge = _ZoneDeleteBadge(self.canvas())
        self._delete_badge.set_anchor(self._badge_anchor())
        self._delete_badge.set_enabled(not self._locked)
        self._delete_badge.show()

    def _hide_delete_badge(self) -> None:
        if self._delete_badge is not None:
            self._delete_badge.hide()



    _BADGE_GAP_FROM_CORNER = 8
    _BADGE_GAP_BETWEEN = 6

    def show_action_badges(self, compare: bool, vectorize: bool) -> None:







        if self._zone_rect is None:
            return
        if compare and self._compare_badge is None:
            self._compare_badge = _ZoneActionBadge(
                self.canvas(),
                "compare",
                get_export_copy("widgets.polygon_selection_tool.compare_badge_label", tr("Compare")),
            )

            self._compare_badge.setToolTip(get_export_copy(
                "widgets.polygon_selection_tool.compare_tooltip",
                tr("Swipe between the original map and this result"),
            ))
        if vectorize and self._vectorize_badge is None:
            self._vectorize_badge = _ZoneActionBadge(
                self.canvas(),
                "vectorize",
                get_export_copy("widgets.polygon_selection_tool.vectorize_badge_label", tr("Vectorize")),
            )
        top_right = self._badge_anchor()


        cursor = _ZoneDeleteBadge.RADIUS + self._BADGE_GAP_FROM_CORNER
        ordered = (
            (self._compare_badge, compare),
            (self._vectorize_badge, vectorize),
        )
        for badge, wanted in ordered:
            if badge is None:
                continue
            if not wanted:
                badge.hide()
                continue
            badge.set_anchor(top_right)
            badge.set_offset(cursor + badge.width / 2.0)
            badge.show()
            cursor += badge.width + self._BADGE_GAP_BETWEEN


        self._sync_delete_badge_with_compare()

    def hide_action_badges(self) -> None:
        if self._compare_badge is not None:
            self._compare_badge.hide()
        if self._vectorize_badge is not None:
            self._vectorize_badge.hide()


        self._sync_delete_badge_with_compare()

    def set_compare_active(self, active: bool) -> None:













        if self._compare_badge is not None:
            self._compare_badge.set_active(active)
            if active:
                exit_tip = get_export_copy(
                    "widgets.polygon_selection_tool.end_comparison_tooltip",
                    tr("Click to end the comparison"),
                )
                self._compare_badge.setToolTip(exit_tip)
            else:
                before_after_tip = get_export_copy(
                    "widgets.polygon_selection_tool.compare_tooltip",
                    tr("Swipe between the original map and this result"),
                )
                self._compare_badge.setToolTip(before_after_tip)
        self._compare_active = bool(active)
        self._sync_delete_badge_with_compare()

    def _sync_delete_badge_with_compare(self) -> None:








        live = (
            self._compare_active
            and self._compare_badge is not None
            and self._compare_badge.isVisible()
        )
        self._compare_active = live
        if live:
            self._hide_delete_badge()
        else:
            self._show_delete_badge()

    def overlay_hit(self, canvas_pt) -> str | None:












        if self._vectorize_badge is not None and self._vectorize_badge.hit_test(canvas_pt):
            return "vectorize"
        if self._compare_badge is not None and self._compare_badge.hit_test(canvas_pt):
            return "compare"
        if self._delete_badge is not None and self._delete_badge.hit_test(canvas_pt):
            return "delete"
        return None
