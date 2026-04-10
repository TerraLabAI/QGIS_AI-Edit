

















from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
)


def has_crs_transform(source_crs: QgsCoordinateReferenceSystem | None,
                      target_crs: QgsCoordinateReferenceSystem | None) -> bool:




    if source_crs is None or target_crs is None:
        return False
    if source_crs == target_crs:
        return True
    if not source_crs.isValid() or not target_crs.isValid():
        return False
    return QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance()).isValid()




def transform_extent(extent: QgsRectangle,
                     source_crs: QgsCoordinateReferenceSystem,
                     target_crs: QgsCoordinateReferenceSystem,
                     project: QgsProject | None = None) -> QgsRectangle | None:





    if source_crs == target_crs:
        return QgsRectangle(extent)
    try:
        transform = QgsCoordinateTransform(source_crs, target_crs, project or QgsProject.instance())
        return transform.transformBoundingBox(extent)
    except Exception:  # noqa: BLE001
        return None
