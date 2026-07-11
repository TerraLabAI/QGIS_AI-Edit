





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
from ..raster_writer import ascii_safe_dir




AI_EDIT_GPKG_FILENAME = "ai_edit.gpkg"


def _plugin_version() -> str:

    from ..request_context import plugin_version

    return plugin_version()


def _project_layer_names() -> set[str]:

    names: set[str] = set()
    try:
        for layer in QgsProject.instance().mapLayers().values():
            if layer is not None:
                names.add(layer.name())
    except Exception:  # nosec B110
        pass
    return names


def friendly_vector_layer_name(class_label: str, raster_name: str) -> str:







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









    mem_layer = QgsVectorLayer(
        (
            "Polygon"
            "?field=feature_id:integer"



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
    added = _provider_call_ok(mem_provider.addFeatures(feats))
    mem_layer.updateExtents()



    if not added or mem_layer.featureCount() != len(feats):
        raise AIEditError(
            ErrorCode.WRITE_ERROR,
            tr("Could not store the vectorized polygons (internal field error)."),
        )
    default_label = classes[0].get("label", "") if len(classes) == 1 else ""
    _configure_attribute_table(mem_layer, default_label)
    set_layer_provenance(mem_layer, source_raster_name, classes)
    apply_class_style(mem_layer, classes)
    log_debug(f"Vectorize layer built: {mem_layer.featureCount()} polygons")
    return mem_layer



PERSIST_OK = ""
PERSIST_WRITE_FAILED = "write_failed"
PERSIST_LOAD_BACK_FAILED = "load_back_failed"
PERSIST_ERROR = "error"


def _free_table_name(gpkg_path: str, table_name: str) -> str:





    if not os.path.exists(gpkg_path):
        return table_name
    taken: set[str] = set()
    try:
        from osgeo import ogr

        ds = ogr.Open(gpkg_path)
        try:
            if ds is not None:
                taken = {
                    ds.GetLayerByIndex(i).GetName().lower()
                    for i in range(ds.GetLayerCount())
                }
        finally:

            ds = None
    except Exception as err:  # noqa: BLE001
        log_warning(f"Vectorize: could not list GeoPackage tables ({err})")
    candidate = table_name
    counter = 2
    while candidate.lower() in taken:
        candidate = f"{table_name}_{counter}"
        counter += 1
    return candidate


def make_layer_permanent(
    mem_layer: QgsVectorLayer,
    gpkg_path: str,
    table_name: str,
    classes: list[dict],
    source_raster_name: str = "",
) -> QgsVectorLayer | None:



    layer, _reason = persist_layer_to_gpkg(
        mem_layer, gpkg_path, table_name, classes, source_raster_name
    )
    return layer


def persist_layer_to_gpkg(
    mem_layer: QgsVectorLayer,
    gpkg_path: str,
    table_name: str,
    classes: list[dict],
    source_raster_name: str = "",
) -> tuple[QgsVectorLayer | None, str]:







    from qgis.core import QgsVectorFileWriter

    try:
        gpkg_path = os.path.abspath(gpkg_path)
        os.makedirs(os.path.dirname(gpkg_path), exist_ok=True)




        gpkg_path = os.path.join(
            ascii_safe_dir(os.path.dirname(gpkg_path)), os.path.basename(gpkg_path)
        )
        table_name = _free_table_name(gpkg_path, table_name)
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
            return None, PERSIST_WRITE_FAILED
        layer = QgsVectorLayer(
            f"{gpkg_path}|layername={table_name}", mem_layer.name(), "ogr"
        )
        if not layer.isValid() or layer.featureCount() != mem_layer.featureCount():
            log_warning("Vectorize: GeoPackage layer failed to load back, keeping memory layer")
            return None, PERSIST_LOAD_BACK_FAILED
    except Exception as err:  # noqa: BLE001
        log_warning(f"Vectorize: GeoPackage persist skipped ({err})")
        return None, PERSIST_ERROR

    default_label = classes[0].get("label", "") if len(classes) == 1 else ""
    _configure_attribute_table(layer, default_label)
    set_layer_provenance(layer, source_raster_name, classes)
    apply_class_style(layer, classes)
    try:

        layer.saveStyleToDatabase(table_name, "AI Edit Vectorize", True, "")
    except Exception:  # nosec B110
        pass
    log_debug(f"Vectorize layer persisted: {gpkg_path}|{table_name}")
    return layer, PERSIST_OK


def _provider_call_ok(result) -> bool:


    if isinstance(result, tuple):
        return bool(result[0]) if result else False
    return bool(result)


def transplant_features(existing: QgsVectorLayer, new_layer: QgsVectorLayer) -> bool:














    if not existing.isValid() or not new_layer.isValid() or existing.isEditable():
        return False
    provider = existing.dataProvider()
    old_ids = [f.id() for f in existing.getFeatures()]

    dest_fields = existing.fields()
    src_fields = new_layer.fields()
    index_map = [
        (src_idx, dest_fields.indexOf(src_fields.at(src_idx).name()))
        for src_idx in range(src_fields.count())
    ]
    if any(dest_idx < 0 for _, dest_idx in index_map):
        return False
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
    try:
        add_ok = _provider_call_ok(provider.addFeatures(fresh)) if fresh else True
    except Exception as err:
        log_warning(f"Vectorize: replacement insert failed ({err})")
        _drop_features_not_in(existing, set(old_ids))
        return False
    if not add_ok:
        _drop_features_not_in(existing, set(old_ids))
        return False
    if not old_ids:
        return True
    if _provider_call_ok(provider.deleteFeatures(old_ids)):
        return True


    _drop_features_not_in(existing, set(old_ids))
    return False


def _drop_features_not_in(layer: QgsVectorLayer, keep_ids: set) -> None:

    try:
        extra = [f.id() for f in layer.getFeatures() if f.id() not in keep_ids]
        if extra:
            layer.dataProvider().deleteFeatures(extra)
    except Exception as err:  # noqa: BLE001
        log_warning(f"Vectorize: could not roll back a partial transplant ({err})")


def set_layer_provenance(
    layer: QgsVectorLayer,
    source_raster_name: str,
    classes: list[dict],
) -> None:


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


    try:
        labels = [c["label"] for c in classes if c.get("label")]
        keywords = [k for k in (["AI Edit", "Vectorize"] + labels + [source_raster_name]) if k]
        md.addKeywords("AI Edit", keywords)
    except Exception:  # nosec B110
        pass
    layer.setMetadata(md)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _class_fill_symbol(rgb: tuple[int, int, int]) -> QgsFillSymbol:



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




    if len(classes) <= 1:


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
