






















from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QMenu,
)

from ...core.i18n import tr
from ...core.number_format import format_count
from . import design_tokens as tokens




_ROW_MAX_PX = 260



_MAX_SOURCES = 24


def format_area_km2(km2: float) -> str:






    try:
        value = float(km2)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value < 0.1:
        return tr("< 0.1 km²")
    shown = format_count(int(round(value))) if value >= 10 else f"{value:.1f}"
    return tr("{area} km²").format(area=shown)


def describe_source(source) -> str:

    label = str(getattr(source, "label", "") or "")
    kind = str(getattr(source, "kind", "") or "")
    if kind == "selection":
        count = int(getattr(source, "feature_count", 0) or 0)
        label = tr("{layer} ({count} selected)").format(
            layer=label, count=format_count(count)
        )
    area = format_area_km2(getattr(source, "area_km2", 0.0) or 0.0)
    if not area:
        return label
    return f"{label}  ·  {area}"


class DockZoneSourcesMixin:


    def refresh_zone_sources(self) -> None:






        link = getattr(self, "_zone_source_link", None)
        if link is None:
            return


        try:
            from ...core import zone_of_interest as zoi

            found = zoi.has_zone_sources()
        except Exception:  # noqa: BLE001
            found = False

        link.setVisible(found)

    def _on_zone_source_link_clicked(self) -> None:

        link = getattr(self, "_zone_source_link", None)
        if link is None:
            return
        try:
            from ...core import zone_of_interest as zoi

            sources = zoi.zone_sources(limit=_MAX_SOURCES)
        except Exception:  # noqa: BLE001
            sources = []
        if not sources:
            link.setVisible(False)
            return
        menu = QMenu(link)
        menu.setStyleSheet(tokens.MENU_QSS)
        menu.setToolTipsVisible(True)
        metrics = link.fontMetrics()
        previous_kind = ""
        for source in sources:
            kind = str(getattr(source, "kind", "") or "")
            if previous_kind and kind != previous_kind:
                menu.addSeparator()
            previous_kind = kind
            full = describe_source(source)
            shown = metrics.elidedText(full, Qt.TextElideMode.ElideRight, _ROW_MAX_PX)
            action = menu.addAction(shown.replace("&", "&&"))
            action.setToolTip(full)
            action.triggered.connect(
                lambda _checked=False, s=source: self._emit_zone_source(s)
            )
        menu.exec(link.mapToGlobal(link.rect().bottomLeft()))

    def _emit_zone_source(self, source) -> None:
        self.zone_source_picked.emit(
            str(getattr(source, "kind", "") or ""),
            str(getattr(source, "layer_id", "") or ""),
        )
