from __future__ import annotations

from qgis.PyQt.QtGui import QImageWriter


def set_server_config(config: dict):


    from ..config_store import get_store
    store = get_store()
    if store is not None:
        store.accept_fetched_export_config(config)


def has_server_config() -> bool:

    from ..config_store import get_store
    store = get_store()
    return store is not None and store.has_server_export_config()


def has_tuned_config() -> bool:



    from ..config_store import is_account_export_config
    cfg = _get_server_config()
    return bool(cfg) and is_account_export_config(cfg)


def _get_server_config() -> dict | None:
    from ..config_store import get_store
    store = get_store()
    return store.get_server_export_config() if store is not None else None


def _pixel_count(value) -> int | None:









    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    return value if value > 0 else None


def _get_max_dimension() -> int | None:

    cfg = _get_server_config()
    return _pixel_count(cfg.get("max_dimension")) if cfg else None


def _get_align() -> int | None:

    cfg = _get_server_config()
    return _pixel_count(cfg.get("align")) if cfg else None






_supported_write_formats_cache: set[str] | None = None


def _supported_write_formats() -> set[str]:

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








    from ..config_store import require_dial, require_str
    pref = require_str("input_format").lower()
    quality = int(require_dial("input_quality", lo=1, hi=100))

    supported = _supported_write_formats()
    if pref == "webp" and "webp" in supported:
        return ("WEBP", "webp", quality)
    if pref == "png":
        return ("PNG", "png", quality)

    return ("JPEG", "jpeg", quality)
