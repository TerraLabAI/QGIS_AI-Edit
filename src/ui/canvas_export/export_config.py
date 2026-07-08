from __future__ import annotations

from qgis.PyQt.QtGui import QImageWriter


def set_server_config(config: dict):
    """Set server export config fetched at plugin startup."""
    from ...core.config_store import get_store
    store = get_store()
    if store is not None:
        store.set_server_export_config(config)


def has_server_config() -> bool:
    """Check if server config has been loaded."""
    from ...core.config_store import get_store
    store = get_store()
    return store is not None and store.has_server_export_config()


def _get_server_config() -> dict | None:
    from ...core.config_store import get_store
    store = get_store()
    return store.get_server_export_config() if store is not None else None


def _get_max_dimension() -> int | None:
    """Get max dimension from server config. Returns None if unavailable."""
    cfg = _get_server_config()
    return cfg.get("max_dimension") if cfg else None


def _get_align() -> int | None:
    """Get pixel alignment from server config. Returns None if unavailable."""
    cfg = _get_server_config()
    return cfg.get("align") if cfg else None


# Input image encoding. The canvas render is photographic/satellite content;
# encoding it lossless runs tens of MB at 4K and inflates a further ~33% as
# base64, which slows uploads for users and runs up egress on our side. We
# encode a high-quality lossy format instead: the input is only a reference the
# model re-renders, so quality 90 is visually indistinguishable while ~15-25x
# smaller. Format and quality come from server config so they stay tunable
# without a plugin re-release; WebP is preferred (smaller than JPEG at equal
# quality) with a JPEG fallback when the Qt WebP codec is absent (it is not
# bundled on every platform).
_DEFAULT_INPUT_FORMAT = "webp"
_DEFAULT_INPUT_QUALITY = 90
_supported_write_formats_cache: set[str] | None = None


def _supported_write_formats() -> set[str]:
    """Lowercased set of image formats this Qt build can write. Cached."""
    global _supported_write_formats_cache
    if _supported_write_formats_cache is None:
        try:
            _supported_write_formats_cache = {
                bytes(f).decode("ascii", "ignore").lower()
                for f in QImageWriter.supportedImageFormats()
            }
        except Exception:
            _supported_write_formats_cache = set()
    return _supported_write_formats_cache


def chosen_input_format() -> tuple[str, str, int]:
    """Pick the encode format for the canvas input as ``(qt_name, token, quality)``.

    ``token`` is the wire identifier ('webp' | 'jpeg' | 'png') the server uses
    to sign the upload with a matching content-type. Reads server config
    ``input_format`` / ``input_quality`` with safe defaults so an older config
    (or none) never breaks a generation. Falls back webp -> jpeg when the WebP
    codec is unavailable in this Qt build (JPEG is always present in Qt).
    """
    cfg = _get_server_config() or {}
    pref = str(cfg.get("input_format") or _DEFAULT_INPUT_FORMAT).lower()
    try:
        quality = int(cfg.get("input_quality") or _DEFAULT_INPUT_QUALITY)
    except (TypeError, ValueError):
        quality = _DEFAULT_INPUT_QUALITY
    quality = max(1, min(100, quality))

    supported = _supported_write_formats()
    if pref == "webp" and "webp" in supported:
        return ("WEBP", "webp", quality)
    if pref == "png":
        return ("PNG", "png", quality)
    # 'jpeg'/'jpg', or 'webp' requested without the codec, or an unknown token.
    return ("JPEG", "jpeg", quality)
