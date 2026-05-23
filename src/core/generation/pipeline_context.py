
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass


@dataclass
class PipelineContext:







    extent: dict | None = None
    crs_wkt: str | None = None
    crs_authid: str | None = None



    bbox_wgs84: dict | None = None


    centroid_lat: float | None = None
    centroid_lon: float | None = None


    ground_resolution_m: float | None = None
    export_width: int | None = None
    export_height: int | None = None



    basemap: str | None = None
    aspect_ratio: str | None = None
    image_size_bytes: int | None = None


    input_format: str | None = None









    zone_polygon_wkt: str | None = None




    parent_request_id: str | None = None





    session_id: str | None = None




    template_id: str | None = None
    template_name: str | None = None





    vector_color: str | None = None
    vector_classes: list[dict] | None = None



    seg_intent: bool = False




    flat_classes: list | None = None
    flat_foreground: str | None = None


    request_id: str | None = None




    model_name: str | None = None
    submitted_resolution: str | None = None
    submitted_aspect_ratio: str | None = None
    submit_timestamp: float | None = None
    credit_cost: int | None = None
    estimated_time_seconds: float | None = None
    max_wait_seconds: float | None = None


    poll_count: int | None = None
    total_wait_seconds: float | None = None
    final_status: str | None = None


    received_image_width: int | None = None
    received_image_height: int | None = None
    received_size_bytes: int | None = None


    output_path: str | None = None
    output_rescued: bool = False
    geotransform: tuple | None = None
    output_bands: int | None = None
    output_dimensions: tuple[int, int] | None = None
    crop_offsets: tuple[int, int, int, int] | None = None





    refund_emitted: bool = False

    def validate(self) -> list[str]:

        warnings = []
        if self.aspect_ratio and self.submitted_aspect_ratio and self.aspect_ratio != self.submitted_aspect_ratio:
            warnings.append(
                f"Aspect ratio mismatch: export={self.aspect_ratio}, "
                f"submitted={self.submitted_aspect_ratio}"
            )
        if self.export_width and self.received_image_width and self.export_width != self.received_image_width:
            warnings.append(
                f"Width mismatch: sent={self.export_width}, "
                f"received={self.received_image_width}"
            )
        if self.export_height and self.received_image_height and self.export_height != self.received_image_height:
            warnings.append(
                f"Height mismatch: sent={self.export_height}, "
                f"received={self.received_image_height}"
            )
        return warnings

    def safe_log_summary(self) -> str:

        parts = []
        if self.export_width and self.export_height:
            parts.append(f"Export: {self.export_width}x{self.export_height}px")
        if self.aspect_ratio:
            parts.append(f"ratio={self.aspect_ratio}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        if self.submitted_resolution:
            parts.append(f"resolution={self.submitted_resolution}")
        if self.credit_cost is not None:
            parts.append(f"credits={self.credit_cost}")
        if self.max_wait_seconds:
            parts.append(f"max_wait={int(self.max_wait_seconds)}s")
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
    context_images: list[bytes] | None = None,
    guidance_png: bytes | None = None,
    guidance_format: str | None = None,
) -> str | None:

    debug_dir = os.path.join(plugin_dir, ".debug")

    run_name = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    run_dir = os.path.join(debug_dir, run_name)
    os.makedirs(run_dir, exist_ok=True)

    if sent_png:


        sent_ext = {"webp": "webp", "jpeg": "jpg"}.get(ctx.input_format or "", "png")
        with open(os.path.join(run_dir, f"sent.{sent_ext}"), "wb") as f:
            f.write(sent_png)
    if received_png:
        with open(os.path.join(run_dir, "received.png"), "wb") as f:
            f.write(received_png)




    if guidance_png:
        guidance_ext = {"webp": "webp", "jpeg": "jpg"}.get(guidance_format or "", "png")
        with open(os.path.join(run_dir, f"guidance.{guidance_ext}"), "wb") as f:
            f.write(guidance_png)

    if context_images:
        for idx, data in enumerate(context_images, start=1):
            with open(os.path.join(run_dir, f"context_{idx}.jpg"), "wb") as f:
                f.write(data)



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





    if not image_bytes:
        return
    mem_path = f"/vsimem/ai_edit_debug_{uuid.uuid4().hex}"
    src = None
    dst = None
    try:
        from osgeo import gdal, osr

        gdal.FileFromMemBuffer(mem_path, image_bytes)
        src = gdal.Open(mem_path)
        if src is None:
            return
        w, h, bands = src.RasterXSize, src.RasterYSize, min(src.RasterCount, 3)
        ext_w = extent["xmax"] - extent["xmin"]
        ext_h = extent["ymax"] - extent["ymin"]
        drv = gdal.GetDriverByName("GTiff")
        dst = drv.Create(os.path.join(run_dir, filename), w, h, bands, gdal.GDT_Byte)
        dst.SetGeoTransform((
            extent["xmin"], ext_w / w, 0,
            extent["ymax"], 0, -(ext_h / h),
        ))
        srs = osr.SpatialReference()
        if srs.ImportFromWkt(crs_wkt) == 0:
            dst.SetProjection(srs.ExportToWkt())
        for i in range(1, bands + 1):
            data = src.GetRasterBand(i).ReadRaster(0, 0, w, h, buf_type=gdal.GDT_Byte)
            dst.GetRasterBand(i).WriteRaster(0, 0, w, h, data, buf_type=gdal.GDT_Byte)
        dst.FlushCache()
    except Exception:
        pass  # nosec B110
    finally:
        dst = None
        src = None
        try:
            from osgeo import gdal

            gdal.Unlink(mem_path)
        except Exception:
            pass  # nosec B110


def _cleanup_old_runs(debug_dir: str, max_runs: int):

    if not os.path.isdir(debug_dir):
        return
    runs = sorted(
        [d for d in os.listdir(debug_dir) if os.path.isdir(os.path.join(debug_dir, d))],
        reverse=True,
    )
    for old_run in runs[max_runs:]:
        shutil.rmtree(os.path.join(debug_dir, old_run), ignore_errors=True)
