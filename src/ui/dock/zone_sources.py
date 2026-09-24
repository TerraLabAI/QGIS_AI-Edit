























from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.number_format import format_count
from . import design_tokens as tokens





_LARGE_ZONE_KM2 = 20.0




_ROW_MAX_PX = 260



_CARD_MARGINS = (12, 10, 12, 10)



_MAX_SOURCES = 24


def large_zone_km2() -> float:

    return float(get_export_dial("guidance.large_zone_km2", _LARGE_ZONE_KM2))


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
    if float(getattr(source, "area_km2", 0.0) or 0.0) >= large_zone_km2():
        area = tr("{area}, very large").format(area=area)
    return f"{label}  ·  {area}"


class ZoneOfInterestCard(QFrame):






    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setStyleSheet(tokens.CARD_QSS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_CARD_MARGINS)
        layout.setSpacing(tokens.SPACE_CARD)

        self._name_label = QLabel("")


        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet(tokens.TITLE_QSS)
        layout.addWidget(self._name_label)

        self._area_label = QLabel("")
        self._area_label.setWordWrap(True)
        self._area_label.setStyleSheet(tokens.HINT_QSS)
        layout.addWidget(self._area_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.use_button = QPushButton(
            get_export_copy("dock.zone_sources.use_zone_btn", tr("Use this zone"))
        )
        self.use_button.setToolTip(
            get_export_copy(
                "dock.zone_sources.use_zone_tooltip",
                tr("Edit inside the zone this project already holds"),
            )
        )
        self.use_button.setCursor(QtC.PointingHandCursor)
        self.use_button.setStyleSheet(tokens.BTN_PRIMARY_QSS)
        row.addWidget(self.use_button, 1)
        layout.addLayout(row)

        self.setVisible(False)

    def show_zone(self, name: str, area_km2: float, approximate: bool = False) -> None:

        self._name_label.setText(str(name or ""))
        area = format_area_km2(area_km2)
        parts = []
        if area:
            parts.append(area)
        if area and float(area_km2 or 0.0) >= large_zone_km2():
            parts.append(tr("very large"))
        if approximate:



            parts.append(tr("box around the source"))
        text = "  ·  ".join(parts)
        self._area_label.setText(text)
        self._area_label.setVisible(bool(text))
        warn = bool(area) and float(area_km2 or 0.0) >= large_zone_km2()
        self._area_label.setStyleSheet(
            f"font-size: {tokens.FONT_HINT}px; background: transparent; border: none;"
            f" color: {tokens.ORANGE_TEXT if warn else tokens.INK_2};"
        )
        self.setVisible(True)


class DockZoneSourcesMixin:


    def refresh_zone_sources(self) -> None:






        card = getattr(self, "_zone_source_card", None)
        link = getattr(self, "_zone_source_link", None)
        if card is None and link is None:
            return
        zone = None
        count = 0
        try:
            from ...core import zone_of_interest as zoi

            zone = zoi.read_zone()
            count = len(zoi.zone_sources(limit=_MAX_SOURCES))
        except Exception:  # noqa: BLE001
            zone = None
            count = 0
        if card is not None:
            if zone is None:
                card.setVisible(False)
            else:
                card.show_zone(
                    zone.label or _zone_layer_name(zone),
                    zone.area_km2(),
                    approximate=bool(getattr(zone, "approximate", False)),
                )
        if link is not None:

            link.setVisible(count > 0)

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

    def _on_zone_card_use_clicked(self) -> None:

        self.zone_source_picked.emit("zone", "")

    def _emit_zone_source(self, source) -> None:
        self.zone_source_picked.emit(
            str(getattr(source, "kind", "") or ""),
            str(getattr(source, "layer_id", "") or ""),
        )


def _zone_layer_name(zone) -> str:

    try:
        from qgis.core import QgsProject

        layer = QgsProject.instance().mapLayer(getattr(zone, "layer_id", "") or "")
        if layer is not None:
            return str(layer.name())
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    from ...core.zone_of_interest import ZONE_LAYER_NAME

    return ZONE_LAYER_NAME
