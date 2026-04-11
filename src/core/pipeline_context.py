"""Pipeline instrumentation for debugging the generation flow."""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass


@dataclass
class PipelineContext:
    """Accumulates metadata at each pipeline boundary for debugging.

    All fields are Optional because the context is populated incrementally:
    canvas_exporter -> generation_service -> raster_writer.
    """

    # Canvas Export (populated by canvas_exporter.py)
    extent: dict | None = None
    crs_wkt: str | None = None
    export_width: int | None = None
    export_height: int | None = None
    aspect_ratio: str | None = None
    image_size_bytes: int | None = None

    # Submit (populated by generation_service.py)
    request_id: str | None = None
    submitted_resolution: str | None = None
    submitted_aspect_ratio: str | None = None
    submit_timestamp: float | None = None

    # Poll (populated by generation_service.py)
    poll_count: int | None = None
    total_wait_seconds: float | None = None
    final_status: str | None = None

    # Download (populated by generation_service.py)
    received_image_width: int | None = None
    received_image_height: int | None = None
    received_size_bytes: int | None = None

    # Write (populated by raster_writer.py)
    output_path: str | None = None
    geotransform: tuple | None = None
    output_bands: int | None = None
    output_dimensions: tuple[int, int] | None = None
    crop_offsets: tuple[int, int, int, int] | None = None  # x, y, w, h

    def validate(self) -> list[str]:
        """Check boundary consistency. Returns list of warning strings."""
        warnings = []
        if (
            self.aspect_ratio
            and self.submitted_aspect_ratio  # noqa: W503
            and self.aspect_ratio != self.submitted_aspect_ratio  # noqa: W503
        ):
            warnings.append(
                f"Aspect ratio mismatch: export={self.aspect_ratio}, "
                f"submitted={self.submitted_aspect_ratio}"
            )
        if (
            self.export_width
            and self.received_image_width  # noqa: W503
            and self.export_width != self.received_image_width  # noqa: W503
        ):
            warnings.append(
                f"Width mismatch: sent={self.export_width}, "
                f"received={self.received_image_width}"
            )
        if (
            self.export_height
            and self.received_image_height  # noqa: W503
            and self.export_height != self.received_image_height  # noqa: W503
        ):
            warnings.append(
                f"Height mismatch: sent={self.export_height}, "
                f"received={self.received_image_height}"
            )
        return warnings

    def safe_log_summary(self) -> str:
        """Production-safe log summary. No URLs, API keys, or model names."""
        parts = []
        if self.export_width and self.export_height:
            parts.append(f"Export: {self.export_width}x{self.export_height}px")
        if self.aspect_ratio:
            parts.append(f"ratio={self.aspect_ratio}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        if self.submitted_resolution:
            parts.append(f"resolution={self.submitted_resolution}")
        if self.received_image_width and self.received_image_height:
            parts.append(
                f"Result: {self.received_image_width}x{self.received_image_height}px"
            )
        if self.received_size_bytes:
            parts.append(f"{self.received_size_bytes // 1024}KB")
        if self.output_path:
            parts.append(f"Output: {self.output_path}")
        return " | ".join(parts)


def save_debug_artifacts(
    ctx: PipelineContext,
    sent_png: bytes | None,
    received_png: bytes | None,
    plugin_dir: str,
    max_runs: int = 20,
) -> str | None:
    """Save debug artifacts to .debug/{timestamp}/. Returns path or None."""
    debug_dir = os.path.join(plugin_dir, ".debug")
    run_dir = os.path.join(debug_dir, str(int(time.time())))
    os.makedirs(run_dir, exist_ok=True)

    if sent_png:
        with open(os.path.join(run_dir, "sent.png"), "wb") as f:
            f.write(sent_png)
    if received_png:
        with open(os.path.join(run_dir, "received.png"), "wb") as f:
            f.write(received_png)

    # Save both sent and received as GeoTIFFs for visual alignment check.
    # Load sent.tif + received.tif in QGIS to see which one is offset.
    if ctx.extent and ctx.crs_wkt:
        _save_debug_geotiff(
            run_dir, "sent.tif", sent_png, ctx.extent, ctx.crs_wkt
        )
        _save_debug_geotiff(
            run_dir, "received.tif", received_png, ctx.extent, ctx.crs_wkt
        )

    ctx_dict = {}
    for k, v in asdict(ctx).items():
        if v is not None:
            ctx_dict[k] = v
    with open(os.path.join(run_dir, "context.json"), "w", encoding="utf-8") as f:
        json.dump(ctx_dict, f, indent=2, default=str)

    _cleanup_old_runs(debug_dir, max_runs)
    return run_dir


def _save_debug_geotiff(
    run_dir: str,
    filename: str,
    image_bytes: bytes | None,
    extent: dict,
    crs_wkt: str,
):
    """Write a debug GeoTIFF from raw PNG bytes + extent. Fails silently."""
    if not image_bytes:
        return
    try:
        from osgeo import gdal, osr

        temp_png = os.path.join(run_dir, f"_tmp_{filename}.png")
        tif_path = os.path.join(run_dir, filename)
        with open(temp_png, "wb") as f:
            f.write(image_bytes)
        src = gdal.Open(temp_png)
        if src is None:
            return
        w, h, bands = src.RasterXSize, src.RasterYSize, min(src.RasterCount, 3)
        ext_w = extent["xmax"] - extent["xmin"]
        ext_h = extent["ymax"] - extent["ymin"]
        drv = gdal.GetDriverByName("GTiff")
        dst = drv.Create(tif_path, w, h, bands, gdal.GDT_Byte)
        dst.SetGeoTransform((
            extent["xmin"], ext_w / w, 0,
            extent["ymax"], 0, -(ext_h / h),
        ))
        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs_wkt)
        dst.SetProjection(srs.ExportToWkt())
        for i in range(1, bands + 1):
            dst.GetRasterBand(i).WriteArray(src.GetRasterBand(i).ReadAsArray())
        dst.FlushCache()
        dst = None
        src = None
        os.remove(temp_png)
    except Exception:
        pass


def _cleanup_old_runs(debug_dir: str, max_runs: int):
    """Delete oldest debug runs beyond max_runs."""
    if not os.path.isdir(debug_dir):
        return
    runs = sorted(
        [d for d in os.listdir(debug_dir) if os.path.isdir(os.path.join(debug_dir, d))],
        reverse=True,
    )
    for old_run in runs[max_runs:]:
        shutil.rmtree(os.path.join(debug_dir, old_run), ignore_errors=True)
