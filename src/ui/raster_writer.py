"""Layer-side half of the raster output path: naming, styling, metadata and
insertion into the AI-Edit layer group.

The GeoTIFF write pipeline itself lives in ``src/core/raster_writer.py`` (no UI
dependency, safe for workers). Every name that was ever importable from this
module stays importable here.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time

from osgeo import gdal
from qgis.core import QgsProject, QgsRasterLayer

from ..core.i18n import tr
from ..core.logger import log_warning
from ..core.prompts.prompt_presets import lookup_template_by_prompt
from ..core.raster_writer import (  # noqa: F401
    _FALLBACK_EXT,
    _FOREIGN_PROJ_MARKERS,
    _GDAL_SAFE_FORMATS,
    _GTIFF_CREATION_OPTIONS,
    OUTPUT_DIR_SETTING,
    _ascii_safe_dir,
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
    extent_and_crs_from_job,
    get_output_dir,
    set_output_dir,
    write_geotiff,
)
from ..core.slug import slugify as _slugify  # noqa: F401
from .layer_groups import add_layer_to_ai_edit_top


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
    """Last-ditch recovery when a GeoTIFF loads as an invalid layer.

    Copies the file to an ASCII-safe temp path under a plain ASCII filename and
    retries the load. This rescues files written to an accented directory
    (older builds, or volumes where 8.3 short names are disabled), where the
    QGIS GDAL provider refuses the original path.
    """
    try:
        base = tempfile.mkdtemp(prefix="terralab_ai_edit_")
        safe_dir = _ascii_safe_dir(base)
        # Keep the original (ASCII, _slugify-guaranteed) name so two recoveries
        # don't collide; only synthesize one if the basename isn't ASCII.
        name = os.path.basename(src_path)
        if not name.isascii():
            name = f"ai_edit_{int(time.time())}.tif"
        safe_path = os.path.join(safe_dir, name)
        shutil.copyfile(src_path, safe_path)
        layer = QgsRasterLayer(safe_path, display_name)
        if layer.isValid():
            log_warning("raster recovered via ASCII-safe copy after invalid path")
            return layer
    except Exception as e:  # noqa: BLE001 - best-effort recovery
        log_warning(f"ASCII-safe reload failed: {e}")
    return None


def add_geotiff_to_project(
    geotiff_path: str,
    prompt: str = "",
    crs_wkt: str = "",
) -> QgsRasterLayer:
    """Add GeoTIFF as a flat child of AI-Edit. Sub-group is created lazily on first vectorize."""
    display_name = _build_layer_name(prompt)

    existing_names = {lyr.name() for lyr in QgsProject.instance().mapLayers().values()}
    if display_name in existing_names:
        counter = 2
        while f"{display_name} ({counter})" in existing_names:
            counter += 1
        display_name = f"{display_name} ({counter})"

    # Build the layer from the absolute path so it is always valid. QGIS stores
    # it relative to the project on save (the default), so the .qgz stays
    # portable. Passing a project-relative path here instead would make
    # QgsRasterLayer resolve it against the current working directory, not the
    # project, and the layer would fail to load whenever they differ.
    project = QgsProject.instance()
    layer = QgsRasterLayer(geotiff_path, display_name)
    if not layer.isValid():
        layer = _reload_from_ascii_copy(geotiff_path, display_name)
    if layer is None or not layer.isValid():
        exists = os.path.exists(geotiff_path)
        size = os.path.getsize(geotiff_path) if exists else -1
        msg = tr("Failed to create valid raster layer from {path}").format(path=geotiff_path)
        raise RuntimeError(f"{msg} (exists={exists}, size={size} bytes)")

    # Files written without an embedded projection (CRS embedding failed, or
    # the plain-image rescue path) get their CRS back from the capture WKT.
    if crs_wkt and not layer.crs().isValid():
        try:
            from qgis.core import QgsCoordinateReferenceSystem

            crs = QgsCoordinateReferenceSystem.fromWkt(crs_wkt)
            if crs.isValid():
                layer.setCrs(crs)
                log_warning("layer CRS set from capture WKT (file had no projection)")
        except Exception as err:  # noqa: BLE001 - layer is still usable
            log_warning(f"layer CRS fallback failed: {err}")

    _apply_default_raster_style(layer)
    _set_raster_layer_metadata(layer, prompt, _read_model_tag(geotiff_path))

    project.addMapLayer(layer, False)
    node = add_layer_to_ai_edit_top(layer)
    if node is not None:
        node.setExpanded(False)

    return layer


def _read_model_tag(geotiff_path: str) -> str:
    """Read the AI_EDIT_MODEL tag back from the written GeoTIFF (the single
    source of truth). Empty string when absent or unreadable."""
    try:
        ds = gdal.Open(geotiff_path)
        if ds is not None:
            return ds.GetMetadataItem("AI_EDIT_MODEL") or ""
    except Exception:  # nosec B110 - metadata is cosmetic
        pass
    return ""


def _set_raster_layer_metadata(
    layer: QgsRasterLayer, prompt: str, model_name: str = ""
) -> None:
    """Mirror the GeoTIFF provenance tags into Layer Properties > Metadata,
    where QGIS users look first."""
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
    except Exception as err:  # nosec B110 - cosmetic only
        log_warning(f"raster layer metadata skipped: {err}")


def _apply_default_raster_style(layer: QgsRasterLayer) -> None:
    """Pin the 3-band RGB renderer; the style travels with the project file."""
    try:
        from qgis.core import QgsMultiBandColorRenderer

        provider = layer.dataProvider()
        if provider is None or provider.bandCount() < 3:
            return
        renderer = QgsMultiBandColorRenderer(provider, 1, 2, 3)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
    except Exception as err:  # nosec B110
        log_warning(f"Default raster renderer skipped: {err}")
