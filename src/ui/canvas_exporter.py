"""Facade for the canvas export pipeline. Implementation lives in ``canvas_export/``.

Every name that was ever importable from this module stays importable here.
"""
from .canvas_export.context_metadata import (  # noqa: F401
    _BASEMAP_HOSTS,
    _basemap_label,
    _bbox_wgs84,
    _centroid_wgs84,
    _compute_ground_resolution_m,
    _detect_basemap,
    apply_export_context,
    estimate_native_ground_resolution_m,
)
from .canvas_export.export_config import (  # noqa: F401
    _DEFAULT_INPUT_FORMAT,
    _DEFAULT_INPUT_QUALITY,
    _get_align,
    _get_max_dimension,
    _get_server_config,
    _supported_write_formats,
    chosen_input_format,
    has_server_config,
    set_server_config,
)
from .canvas_export.native_resolution import (  # noqa: F401
    _WEBMERC_M_PX_Z0,
    QgsVectorTileLayer,
    _best_native_longest_px,
    _intersects_zone,
    _layer_units_to_meters_xy,
    _native_pixel_size_xy_m,
    _raster_native_mpp_xy,
    _vector_tile_native_mpp_xy,
    _webmerc_mpp_at_lat,
    _xyz_native_mpp_xy,
    _xyz_zmax,
    _zone_dims_meters,
)
from .canvas_export.render import (  # noqa: F401
    ExportPrep,
    _clone_map_settings,
    _encode_image,
    _render_markup_overlay,
    _render_settings_to_image,
    prepare_export,
    render_clean_base,
    render_export,
)
from .canvas_export.sizing import (  # noqa: F401
    _INPUT_BUDGET_HEADROOM,
    _RESOLUTION_TARGET_PX,
    _adjust_extent_to_aspect,
    _aspect_dims,
    _budget_dims,
    get_zone_pixel_size,
)
from .canvas_export.zone_validation import (  # noqa: F401
    _POLAR_ABS_LAT_DEG,
    validate_zone,
)
