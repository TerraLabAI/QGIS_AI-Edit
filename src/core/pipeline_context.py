"""Pipeline instrumentation for debugging the generation flow."""

import json
import os
import shutil
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple


@dataclass
class PipelineContext:
    """Accumulates metadata at each pipeline boundary for debugging.

    All fields are Optional because the context is populated incrementally:
    canvas_exporter -> generation_service -> raster_writer.
    """

    # Canvas Export (populated by canvas_exporter.py)
    extent: Optional[Dict] = None
    crs_wkt: Optional[str] = None
    export_width: Optional[int] = None
    export_height: Optional[int] = None
    aspect_ratio: Optional[str] = None
    image_size_bytes: Optional[int] = None

    # Submit (populated by generation_service.py)
    request_id: Optional[str] = None
    submitted_resolution: Optional[str] = None
    submitted_aspect_ratio: Optional[str] = None
    submit_timestamp: Optional[float] = None

    # Poll (populated by generation_service.py)
    poll_count: Optional[int] = None
    total_wait_seconds: Optional[float] = None
    final_status: Optional[str] = None

    # Download (populated by generation_service.py)
    received_image_width: Optional[int] = None
    received_image_height: Optional[int] = None
    received_size_bytes: Optional[int] = None

    # Write (populated by raster_writer.py)
    output_path: Optional[str] = None
    geotransform: Optional[Tuple] = None
    output_bands: Optional[int] = None
    output_dimensions: Optional[Tuple[int, int]] = None
    crop_offsets: Optional[Tuple[int, int, int, int]] = None  # x, y, w, h

    def validate(self) -> List[str]:
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
    sent_png: Optional[bytes],
    received_png: Optional[bytes],
    plugin_dir: str,
    max_runs: int = 20,
) -> Optional[str]:
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

    ctx_dict = {}
    for k, v in asdict(ctx).items():
        if v is not None:
            ctx_dict[k] = v
    with open(os.path.join(run_dir, "context.json"), "w") as f:
        json.dump(ctx_dict, f, indent=2, default=str)

    _cleanup_old_runs(debug_dir, max_runs)
    return run_dir


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
