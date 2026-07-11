"""Vector-layer build, styling, and GeoPackage persistence for Vectorize.

Everything here creates or mutates ``QgsVectorLayer`` objects, so it is
main-thread only (call from a QgsTask's ``finished()``, never ``run()``).
The heavy pixel compute lives in ``vectorization_service``.
"""
from __future__ import annotations

import os
import time

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsDefaultValue,
    QgsEditorWidgetSetup,
    QgsFillSymbol,
    QgsProject,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QDate, QLocale, Qt

from ..errors import AIEditError, ErrorCode
from ..i18n import tr
from ..logger import log_debug, log_warning

# One GeoPackage next to the generated rasters holds every vectorize run (one
# table per run). Hoisted to a constant so the filename lives in exactly one
# place instead of being spelled inline at the call site.
AI_EDIT_GPKG_FILENAME = "ai_edit.gpkg"


def _plugin_version() -> str:
    """Plugin version from metadata.txt (best-effort, '' if unreadable)."""
    root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    )
    try:
        with open(os.path.join(root, "metadata.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass  # nosec B110 - version is cosmetic provenance, never block a run
    return ""


def _project_layer_names() -> set[str]:
    """Names of every layer currently in the project (for name dedup)."""
    names: set[str] = set()
    try:
        for layer in QgsProject.instance().mapLayers().values():
            if layer is not None:
                names.add(layer.name())
    except Exception:  # nosec B110 - dedup is best-effort, never block a run
        pass
    return names


def friendly_vector_layer_name(class_label: str, raster_name: str) -> str:
    """Friendly, dated tree name for a vectorize result, e.g. "Buildings (3 Jul)".

    Uses the class label when known (only the first letter is capitalized, the
    rest stays as typed), else falls back to "<raster> (vector)". The date is
    locale-short, and a same-name-same-day rerun becomes "Buildings 2 (3 Jul)"
    by scanning existing project layer names. Mirrors AI Segmentation's
    friendly_layer_name."""
    label = (class_label or "").strip()
    if label:
        base = label[0].upper() + label[1:]
    else:
        raster = (raster_name or "").strip()
        base = f"{raster} (vector)" if raster else tr("Vector")
    date_str = QLocale().toString(QDate.currentDate(), "d MMM")
    existing = _project_layer_names()
    candidate = f"{base} ({date_str})"
    counter = 2
    while candidate in existing:
        candidate = f"{base} {counter} ({date_str})"
        counter += 1
    return candidate


def build_vector_layer(
    feats: list,
    raster_crs,
    layer_name: str,
    classes: list[dict],
    source_raster_name: str = "",
) -> QgsVectorLayer:
    """Build the styled in-memory polygon layer from precomputed features.

    ``classes`` is the traced class list (``[{"rgb": (r,g,b), "label": str},
    ...]``); it drives the categorized style and the provenance text. Creates a
    ``QgsVectorLayer`` and adds features, so it MUST run on the main thread."""
    # Minimal per-feature schema (industry pattern: stable machine code in
    # class_color + free-text label in class_name, plus the geodesic measure).
    # Run-level provenance lives in the layer metadata, not repeated per row.
    # CRS-agnostic URI + explicit setCrs(): EPSG:4326 fallback would corrupt alignment.
    mem_layer = QgsVectorLayer(
        (
            "Polygon"
            "?field=feature_id:integer"
            # Generous length: the memory provider silently DROPS any feature
            # whose value overflows a field, so a too-short field makes the
            # whole layer come back empty. class_name is user-editable.
            "&field=class_name:string(254)"
            "&field=class_color:string(9)"
            "&field=area_m2:double"
        ),
        layer_name,
        "memory",
    )
    if raster_crs is not None and raster_crs.isValid():
        mem_layer.setCrs(raster_crs)
    mem_provider = mem_layer.dataProvider()
    mem_provider.addFeatures(feats)
    mem_layer.updateExtents()
    # The memory provider drops features whose attributes overflow a field
    # instead of raising. Guard against that so we never hand back an empty
    # layer while reporting success ("Vectorize done: N" but 0 on the map).
    if mem_layer.featureCount() != len(feats):
        raise AIEditError(
            ErrorCode.WRITE_ERROR,
            tr("Could not store the vectorized polygons (internal field error)."),
        )
    default_label = classes[0]["label"] if len(classes) == 1 else ""
    _configure_attribute_table(mem_layer, default_label)
    set_layer_provenance(mem_layer, source_raster_name, classes)
    apply_class_style(mem_layer, classes)
    log_debug(f"Vectorize layer built: {mem_layer.featureCount()} polygons")
    return mem_layer


def make_layer_permanent(
    mem_layer: QgsVectorLayer,
    gpkg_path: str,
    table_name: str,
    classes: list[dict],
    source_raster_name: str = "",
) -> QgsVectorLayer | None:
    """Persist the freshly built vector layer into the output GeoPackage and
    return the disk-backed replacement, or None to keep the memory layer.

    Memory layers silently vanish when the project closes; GeoPackage is the
    QGIS-native container, so each run becomes one table in ai_edit.gpkg next
    to the generated rasters. Best-effort: any failure (locked file, read-only
    folder) keeps the volatile in-memory layer instead of failing the run.
    Main-thread only (creates layers, reads QgsProject)."""
    from qgis.core import QgsVectorFileWriter

    try:
        os.makedirs(os.path.dirname(gpkg_path), exist_ok=True)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = table_name
        options.actionOnExistingFile = (
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            if os.path.exists(gpkg_path)
            else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        )
        writer = (
            QgsVectorFileWriter.writeAsVectorFormatV3
            if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3")
            else QgsVectorFileWriter.writeAsVectorFormatV2
        )
        res = writer(
            mem_layer, gpkg_path, QgsProject.instance().transformContext(), options
        )
        code = res[0] if isinstance(res, tuple) else res
        if code != QgsVectorFileWriter.WriterError.NoError:
            log_warning(f"Vectorize: GeoPackage write failed ({res}), keeping memory layer")
            return None
        layer = QgsVectorLayer(
            f"{gpkg_path}|layername={table_name}", mem_layer.name(), "ogr"
        )
        if not layer.isValid() or layer.featureCount() != mem_layer.featureCount():
            log_warning("Vectorize: GeoPackage layer failed to load back, keeping memory layer")
            return None
    except Exception as err:  # noqa: BLE001 - persistence is best-effort
        log_warning(f"Vectorize: GeoPackage persist skipped ({err})")
        return None

    default_label = classes[0]["label"] if len(classes) == 1 else ""
    _configure_attribute_table(layer, default_label)
    set_layer_provenance(layer, source_raster_name, classes)
    apply_class_style(layer, classes)
    try:
        # Stored inside the GeoPackage, so the style survives outside this project.
        layer.saveStyleToDatabase(table_name, "AI Edit Vectorize", True, "")
    except Exception:  # nosec B110 - cosmetic only
        pass
    log_debug(f"Vectorize layer persisted: {gpkg_path}|{table_name}")
    return layer


def transplant_features(existing: QgsVectorLayer, new_layer: QgsVectorLayer) -> bool:
    """Replace ``existing``'s features with ``new_layer``'s, mapping attributes
    by FIELD NAME, never by position.

    A GeoPackage layer carries an implicit integer ``fid`` primary key that the
    in-memory build does not have, so positional attribute copy shifts every
    value one field left ("Got QString, expected int" on feature_id). Building
    each feature against the destination's own fields keeps ``fid`` unset (the
    provider assigns it) and every named field aligned. Returns False when the
    provider rejected the edit."""
    provider = existing.dataProvider()
    old_ids = [f.id() for f in existing.getFeatures()]
    delete_ok = provider.deleteFeatures(old_ids) if old_ids else True

    dest_fields = existing.fields()
    src_fields = new_layer.fields()
    index_map = [
        (src_idx, dest_fields.indexOf(src_fields.at(src_idx).name()))
        for src_idx in range(src_fields.count())
    ]
    from qgis.core import QgsFeature

    fresh: list[QgsFeature] = []
    for feat in new_layer.getFeatures():
        nf = QgsFeature(dest_fields)
        nf.setGeometry(feat.geometry())
        attrs = feat.attributes()
        for src_idx, dest_idx in index_map:
            if dest_idx >= 0:
                nf.setAttribute(dest_idx, attrs[src_idx])
        fresh.append(nf)
    add_ok = provider.addFeatures(fresh)
    return bool(delete_ok and add_ok)


def set_layer_provenance(
    layer: QgsVectorLayer,
    source_raster_name: str,
    classes: list[dict],
) -> None:
    """Record run-level provenance on the LAYER (Properties > Metadata), the
    QGIS convention, instead of repeating it as an attribute on every row."""
    described = ", ".join(
        f"{c['label']} ({_hex(c['rgb'])})" if c.get("label") else _hex(c["rgb"])
        for c in classes
    )
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    md = layer.metadata()
    md.setTitle(layer.name())
    abstract = f"Polygons traced by AI Edit (TerraLab) Vectorize. Classes: {described}."
    if source_raster_name:
        abstract += f" Source raster: {source_raster_name}."
    version = _plugin_version()
    if version:
        abstract += f" Plugin version: {version}."
    md.setAbstract(abstract)
    md.setHistory([f"{created} vectorized from '{source_raster_name}' ({described})"])
    # Keywords make the layer discoverable in the QGIS Metadata search; kept
    # defensive so a metadata quirk can never block a (paid) vectorize.
    try:
        labels = [c["label"] for c in classes if c.get("label")]
        keywords = [k for k in (["AI Edit", "Vectorize"] + labels + [source_raster_name]) if k]
        md.addKeywords("AI Edit", keywords)
    except Exception:  # nosec B110 - metadata is cosmetic, never block a run
        pass
    layer.setMetadata(md)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _class_fill_symbol(rgb: tuple[int, int, int]) -> QgsFillSymbol:
    """Mostly-opaque fill + solid outline in the class color: the source raster
    is hidden after vectorizing, so ~80% alpha keeps polygons legible while
    still hinting at any basemap underneath."""
    r, g, b = rgb
    return QgsFillSymbol.createSimple(
        {
            "color": f"{r},{g},{b},205",
            "style": "solid",
            "outline_color": f"{r},{g},{b},255",
            "outline_width": "0.4",
            "outline_style": "solid",
        }
    )


def apply_class_style(layer: QgsVectorLayer, classes: list[dict]) -> None:
    """Categorized renderer on class_name, one category per traced class in its
    own map color, so the legend in the Layers panel doubles as the class key.
    Falls back to a single symbol when there is exactly one class (keeps the
    layer restylable the way users already know)."""
    if len(classes) <= 1:
        # A fresh renderer, not renderer().setSymbol(): the layer may carry a
        # categorized renderer from a previous multi-class run.
        rgb = classes[0]["rgb"] if classes else (255, 0, 0)
        layer.setRenderer(QgsSingleSymbolRenderer(_class_fill_symbol(rgb)))
        layer.triggerRepaint()
        return
    categories = []
    for cls in classes:
        label = cls.get("label") or _hex(cls["rgb"])
        categories.append(
            QgsRendererCategory(label, _class_fill_symbol(cls["rgb"]), label)
        )
    layer.setRenderer(QgsCategorizedSymbolRenderer("class_name", categories))
    layer.triggerRepaint()


def _configure_attribute_table(layer: QgsVectorLayer, class_label: str) -> None:
    """Set displayExpression, default value, editor widget and default sort
    so the user gets a readable attribute table out of the box.

    - displayExpression makes the form-view feature list show
      `<id> - <class> (<area> m2)` instead of repeating the same color hex.
    - QgsDefaultValue gives rows added manually via the table a sensible default.
    - TextEdit widget on class_name unlocks QGIS's per-column unique-values
      autocomplete so the user types once then picks from prior values.
    - Default sort by area_m2 descending puts large polygons at the top.
    """
    layer.setDisplayExpression(
        "format('%1 - %2 (%3 m²)', \"feature_id\","
        " coalesce(\"class_name\", ''), round(\"area_m2\"))"
    )

    idx = layer.fields().indexOf("class_name")
    if idx >= 0:
        escaped = (class_label or "").replace("'", "''")
        layer.setDefaultValueDefinition(idx, QgsDefaultValue(f"'{escaped}'"))
        layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("TextEdit", {}))

    config = layer.attributeTableConfig()
    config.setSortExpression('"area_m2"')
    config.setSortOrder(Qt.SortOrder.DescendingOrder)
    layer.setAttributeTableConfig(config)
