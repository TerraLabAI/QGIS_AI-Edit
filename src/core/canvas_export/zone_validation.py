from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)

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
        if coords_in_range and max_abs_lat > _POLAR_ABS_LAT_DEG:
            raise AIEditError(
                ErrorCode.POLAR,
                tr(
                    "Zone is too close to a pole (above {limit} degrees latitude). "
                    "AI Edit cannot estimate ground resolution there."
                ).format(limit=int(_POLAR_ABS_LAT_DEG)),
            )
