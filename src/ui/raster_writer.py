






from __future__ import annotations

import os
import re
import shutil
import time

from qgis.core import QgsProject, QgsRasterLayer

from ..core.i18n import tr
from ..core.logger import log_warning
from ..core.prompts.prompt_presets import lookup_template_by_prompt
from ..core.raster_writer import (
    _FALLBACK_EXT,
    _FOREIGN_PROJ_MARKERS,
    _GDAL_SAFE_FORMATS,
    _GTIFF_CREATION_OPTIONS,
    BEFORE_PATH_PROPERTY,
    OUTPUT_DIR_SETTING,
    _create_gtiff,
    _detect_image_format,
    _documents_default_dir,
    _image_dimensions,
    _output_file_base,
    _rescue_plain_image,
    _restore_qgis_proj_paths,
    _safe_projection_wkt,
    _unique_output_path,
    _write_geotiff_gdal,
    ascii_safe_dir,
    before_file_base,
    extent_and_crs_from_job,
    fallback_output_dir,
    get_output_dir,
    read_crop_polygon_wkt,
    replace_staged_file,
    set_output_dir,
    write_geotiff,
)
from ..core.slug import slugify as _slugify
from .layer_groups import add_layer_to_ai_edit_top


__all__ = [
    "_create_gtiff",
    "_detect_image_format",
    "_documents_default_dir",
    "_FALLBACK_EXT",
    "_FOREIGN_PROJ_MARKERS",
    "_GDAL_SAFE_FORMATS",
    "_GTIFF_CREATION_OPTIONS",
    "_image_dimensions",
    "_output_file_base",
    "_rescue_plain_image",
    "_restore_qgis_proj_paths",
    "_safe_projection_wkt",
    "_slugify",
    "_unique_output_path",
    "_write_geotiff_gdal",
    "add_geotiff_to_project",
    "ascii_safe_dir",
    "before_file_base",
    "BEFORE_PATH_PROPERTY",
    "extent_and_crs_from_job",
    "get_output_dir",
    "OUTPUT_DIR_SETTING",
    "read_crop_polygon_wkt",
    "replace_staged_file",
    "set_output_dir",
    "write_geotiff",
]


def _humanize_prompt(prompt: str, max_chars: int = 30) -> str:
    text = re.sub(r"\s+", " ", (prompt or "")).strip()
    if not text:
        return ""
    if len(text) > max_chars:
        cut = text[:max_chars].rsplit(" ", 1)[0] or text[:max_chars]
        text = cut.rstrip(" ,.;:-") + "..."
    return text[:1].upper() + text[1:]


def _build_layer_name(prompt: str) -> str:
    match = lookup_template_by_prompt(prompt) if prompt else None
    if match is not None:
        return match[1]
    humanized = _humanize_prompt(prompt)
    return humanized or "AI Edit result"


def _reload_from_ascii_copy(src_path: str, display_name: str) -> QgsRasterLayer | None:









    try:
        safe_dir = ascii_safe_dir(fallback_output_dir())



        stem, ext = os.path.splitext(os.path.basename(src_path))
        if not (stem + ext).isascii():
            stem, ext = f"ai_edit_{int(time.time())}", ".tif"
        safe_path = _unique_output_path(safe_dir, stem, ext.lstrip(".") or "tif")
        shutil.copyfile(src_path, safe_path)
        layer = QgsRasterLayer(safe_path, display_name)
        if layer.isValid():
            log_warning("raster recovered via ASCII-safe copy after invalid path")
            return layer
    except Exception as e:  # noqa: BLE001
        log_warning(f"ASCII-safe reload failed: {e}")
    return None


def add_geotiff_to_project(
    geotiff_path: str,
    prompt: str = "",
    crs_wkt: str = "",
    before_path: str = "",
) -> QgsRasterLayer:




    display_name = _build_layer_name(prompt)

    existing_names = {lyr.name() for lyr in QgsProject.instance().mapLayers().values()}
    if display_name in existing_names:
        counter = 2
        while f"{display_name} ({counter})" in existing_names:
            counter += 1
        display_name = f"{display_name} ({counter})"






    project = QgsProject.instance()
    layer = QgsRasterLayer(geotiff_path, display_name)
    if not layer.isValid():
        layer = _reload_from_ascii_copy(geotiff_path, display_name)
    if layer is None or not layer.isValid():
        exists = os.path.exists(geotiff_path)
        size = os.path.getsize(geotiff_path) if exists else -1
        msg = tr("Failed to create valid raster layer from {path}").format(path=geotiff_path)
        raise RuntimeError(f"{msg} (exists={exists}, size={size} bytes)")



    if crs_wkt and not layer.crs().isValid():
        try:
            from qgis.core import QgsCoordinateReferenceSystem

            crs = QgsCoordinateReferenceSystem.fromWkt(crs_wkt)
            if crs.isValid():
                layer.setCrs(crs)
                log_warning("layer CRS set from capture WKT (file had no projection)")
        except Exception as err:  # noqa: BLE001
            log_warning(f"layer CRS fallback failed: {err}")

    has_crop = read_crop_polygon_wkt(geotiff_path) is not None
    _apply_default_raster_style(layer, has_crop=has_crop)
    _set_raster_layer_metadata(layer, prompt, _read_model_tag(geotiff_path))
    if before_path and os.path.exists(before_path):
        layer.setCustomProperty(BEFORE_PATH_PROPERTY, before_path)

    project.addMapLayer(layer, False)
    node = add_layer_to_ai_edit_top(layer)
    if node is not None:
        node.setExpanded(False)

    return layer


def _read_model_tag(geotiff_path: str) -> str:


    try:
        from osgeo import gdal

        ds = gdal.Open(geotiff_path)
        if ds is not None:
            return ds.GetMetadataItem("AI_EDIT_MODEL") or ""
    except Exception:  # nosec B110
        pass
    return ""


def _set_raster_layer_metadata(
    layer: QgsRasterLayer, prompt: str, model_name: str = ""
) -> None:


    try:
        md = layer.metadata()
        md.setTitle(layer.name())
        prompt_part = f' Prompt: "{prompt}".' if prompt else ""
        model_part = f" Model: {model_name}." if model_name and model_name != "AI Edit" else ""
        md.setAbstract(
            f"AI-generated imagery created with AI Edit (TerraLab).{model_part}{prompt_part}"
            " Synthetic imagery, not survey data."
        )
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        md.setHistory([f"{created} generated by AI Edit"])
        layer.setMetadata(md)
    except Exception as err:  # nosec B110
        log_warning(f"raster layer metadata skipped: {err}")


def _apply_default_raster_style(layer: QgsRasterLayer, has_crop: bool = False) -> None:






    try:
        from qgis.core import QgsMultiBandColorRenderer

        provider = layer.dataProvider()
        if provider is None or provider.bandCount() < 3:
            return
        renderer = QgsMultiBandColorRenderer(provider, 1, 2, 3)
        if has_crop and provider.bandCount() >= 4:
            renderer.setAlphaBand(4)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
    except Exception as err:  # nosec B110
        log_warning(f"Default raster renderer skipped: {err}")
