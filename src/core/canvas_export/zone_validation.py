from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)

from ..config_store import get_export_dial
from ..errors import AIEditError, ErrorCode
from ..i18n import tr

# Above this absolute latitude the Mercator world distortion makes
# ground_resolution estimates unreliable and most basemaps stop. Refuse to
# avoid silent corruption of the output GeoTIFF.
_POLAR_ABS_LAT_DEG = 85.0


def validate_zone(extent: QgsRectangle, map_crs, map_rotation: float = 0.0) -> None:
    """Raise AIEditError if the zone can't be exported safely (CRS, rotation, antimeridian, polar).

    No area guard here: oversized zones are handled downstream (sizing caps the
    export resolution; the submit path refuses oversized request bodies)."""
    if map_crs is None or not map_crs.isValid():
        raise AIEditError(
            ErrorCode.INVALID_CRS,
            tr("This project's CRS is invalid. Set a project CRS before drawing a zone."),
        )
    if not map_crs.authid():
        raise AIEditError(
            ErrorCode.INVALID_CRS,
            tr(
                "AI Edit needs a standard CRS (EPSG code). "
                "Your project uses a custom CRS without an authority ID."
            ),
        )
    if abs(float(map_rotation)) > 0.01:
        raise AIEditError(
            ErrorCode.MAP_ROTATED,
            tr(
                "Map rotation is not supported. "
                "Reset rotation to 0 in the map navigation controls and try again."
            ),
        )

    geographic_extent = extent
    crosses_antimeridian = False
    if not map_crs.isGeographic():
        try:
            to_wgs = QgsCoordinateTransform(
                map_crs,
                QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance(),
            )
            geographic_extent = to_wgs.transformBoundingBox(extent)
            # transformBoundingBox collapses the box to [min_lon, max_lon],
            # which makes a true dateline crossing look identical to a merely
            # very wide zone (both report a > 180 deg span). Compare the actual
            # left and right edges instead: when the zone wraps past 180 deg,
            # proj normalizes the east edge to a longitude west of the west edge.
            y_mid = (extent.yMinimum() + extent.yMaximum()) / 2.0
            left_lon = to_wgs.transform(QgsPointXY(extent.xMinimum(), y_mid)).x()
            right_lon = to_wgs.transform(QgsPointXY(extent.xMaximum(), y_mid)).x()
            crosses_antimeridian = right_lon < left_lon
        except Exception:
            geographic_extent = None
    else:
        # Geographic project: only a narrow zone can genuinely wrap the dateline.
        # A span >= 180 deg is just a very wide (or out-of-range) zone, not a
        # crossing - mirror the projected path and don't flag it. A true wrap is
        # a narrow zone whose edges land in different 360-deg longitude cells.
        raw_width = extent.xMaximum() - extent.xMinimum()
        lo_cell = math.floor((extent.xMinimum() + 180.0) / 360.0)
        hi_cell = math.floor((extent.xMaximum() + 180.0) / 360.0)
        crosses_antimeridian = raw_width < 180.0 and lo_cell != hi_cell

    if geographic_extent is not None:
        max_abs_lat = max(abs(geographic_extent.yMinimum()), abs(geographic_extent.yMaximum()))
        # The antimeridian and polar guards only make sense for real lat/lon.
        # When the data sits outside valid geographic bounds (a layer in meters
        # or a non-georeferenced layer tagged EPSG:4326, so latitude exceeds
        # +/-90 deg), neither concept applies - skip the guards and let the zone
        # through rather than block the user with a misleading refusal.
        lon_min = geographic_extent.xMinimum()
        lon_max = geographic_extent.xMaximum()
        coords_in_range = max_abs_lat <= 90.0 and lon_min >= -540.0 and lon_max <= 540.0
        if coords_in_range and crosses_antimeridian:
            raise AIEditError(
                ErrorCode.ANTIMERIDIAN,
                tr(
                    "This zone crosses the antimeridian (180 deg longitude). "
                    "AI Edit does not support that yet. Split your zone into two."
                ),
            )
        polar_limit = get_export_dial("zone.polar_abs_lat_deg", _POLAR_ABS_LAT_DEG)
        if coords_in_range and max_abs_lat > polar_limit:
            raise AIEditError(
                ErrorCode.POLAR,
                tr(
                    "Zone is too close to a pole (above {limit} degrees latitude). "
                    "AI Edit cannot estimate ground resolution there."
                ).format(limit=int(polar_limit)),
            )


# Under this share of the zone inside the chosen raster, the part that hangs
# outside comes back blank and is worth a warning. Server dial
# ``zone.layer_overlap_min_share``, one fallback.
_LAYER_OVERLAP_MIN_SHARE = 0.5

OVERLAP_OK = "ok"
OVERLAP_PARTIAL = "partial"
OVERLAP_OUTSIDE = "outside"


def _geometry_op_ok(result) -> bool:
    """QgsGeometry.transform() returns 0 / Success on QGIS 3 and a scoped
    Qgis.GeometryOperationResult on QGIS 4. Both read as zero when it worked."""
    try:
        return int(result) == 0
    except (TypeError, ValueError):
        return "Success" in str(result)


def zone_layer_overlap(zone, zone_crs, layer_extent: QgsRectangle, layer_crs) -> str:
    """How a freshly drawn zone sits on the chosen raster's data extent.

    ``zone`` is a QgsGeometry or a QgsRectangle in ``zone_crs``. Returns
    OVERLAP_OUTSIDE when the two do not touch at all (refuse the zone: the
    export would be blank and billed), OVERLAP_PARTIAL when less than the dial
    share of the zone lies inside (warn, the rest comes back blank), else
    OVERLAP_OK. Online providers report a world-sized extent, so the guard
    never fires for them, which is intended: the footgun is a local raster.
    Any transform or geometry failure reads as OVERLAP_OK, never as a refusal
    built on coordinates in the wrong frame.
    """
    from qgis.core import QgsGeometry

    try:
        if layer_extent is None or layer_extent.isEmpty():
            return OVERLAP_OK
        if layer_extent.width() <= 0 or layer_extent.height() <= 0:
            return OVERLAP_OK
        if isinstance(zone, QgsRectangle):
            geom = QgsGeometry.fromRect(zone)
        else:
            geom = QgsGeometry(zone)  # copy: transform() mutates
        if geom.isEmpty():
            return OVERLAP_OK
        if zone_crs != layer_crs:
            if not zone_crs.isValid() or not layer_crs.isValid():
                return OVERLAP_OK
            xform = QgsCoordinateTransform(zone_crs, layer_crs, QgsProject.instance())
            if not _geometry_op_ok(geom.transform(xform)) or geom.isEmpty():
                return OVERLAP_OK
        extent_geom = QgsGeometry.fromRect(layer_extent)
        if not geom.intersects(extent_geom):
            return OVERLAP_OUTSIDE
        zone_area = geom.area()
        if zone_area <= 0:
            return OVERLAP_OK
        inside = geom.intersection(extent_geom).area() / zone_area
        min_share = get_export_dial("zone.layer_overlap_min_share", _LAYER_OVERLAP_MIN_SHARE)
        if inside < min_share:
            return OVERLAP_PARTIAL
    except Exception:  # nosec B110 - guard rail only, never block on failure
        return OVERLAP_OK
    return OVERLAP_OK
