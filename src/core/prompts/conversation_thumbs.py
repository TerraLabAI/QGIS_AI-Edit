"""Local thumbnails for conversation rows, written at generation time.

Server thumb URLs are signed and expire within the hour, so history rows
restored from the disk cache cannot fetch them reliably. Instead the plugin
saves a small square JPEG the moment a generation lands (the pixmap is
already in hand) and the rows read it back from disk, no network involved.
Generations older than this feature simply have no thumb and render as
text-only rows.

Stored under the QGIS profile dir (never the plugin folder, which updates
wipe), capped and pruned oldest-first.
"""
from __future__ import annotations

import os
import tempfile

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap

from ..config_store import get_export_dial
from ..logger import log_warning
from ..output_paths import remove_with_retry, replace_with_retry

_THUMB_DIR_NAME = "ai_edit_conversation_thumbs"
# Square edge of the stored file, 2x what the row paints, for hi-DPI.
_THUMB_EDGE_PX = 96
_THUMB_JPEG_QUALITY = 85
# Room for the history-cache window twice over: every conversation stores its
# cover output AND its session Original, plus member outputs for the strip.
_THUMB_CAP = 160


def _thumb_dir(create: bool = True) -> str | None:
    base = QgsApplication.qgisSettingsDirPath() or ""
    if not base:
        return None
    path = os.path.join(base, "TerraLab", _THUMB_DIR_NAME)
    if not create:
        return path
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as err:
        log_warning(f"conversation thumb dir unavailable: {err}")
        return None
    return path


def _thumb_path(request_id: str, create: bool = False) -> str | None:
    rid = request_id.strip() if isinstance(request_id, str) else ""
    if not rid or not all(c.isalnum() or c in "-_" for c in rid):
        return None
    folder = _thumb_dir(create=create)
    if folder is None:
        return None
    return os.path.join(folder, rid + ".jpg")


def save_thumb(request_id: str, pixmap: QPixmap | None, prune: bool = True) -> None:
    """Center-crop ``pixmap`` to a small square and persist it. Best-effort:
    a failure only costs the row its picture. A batch passes ``prune=False``
    and calls ``prune_thumbs()`` once at the end."""
    if pixmap is None or pixmap.isNull():
        return
    path = _thumb_path(request_id, create=True)
    if path is None:
        return
    scaled = pixmap.scaled(
        _THUMB_EDGE_PX,
        _THUMB_EDGE_PX,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - _THUMB_EDGE_PX) // 2)
    y = max(0, (scaled.height() - _THUMB_EDGE_PX) // 2)
    square = scaled.copy(x, y, _THUMB_EDGE_PX, _THUMB_EDGE_PX)
    quality = min(100, get_export_dial("pipeline.conversation_thumbs.jpeg_quality", _THUMB_JPEG_QUALITY))
    staged = None
    try:
        fd, staged = tempfile.mkstemp(prefix=".thumb-", suffix=".jpg", dir=os.path.dirname(path))
        os.close(fd)
        if not square.save(staged, "JPG", quality):
            log_warning("conversation thumb save failed")
            return
        replace_with_retry(staged, path)
    except OSError:
        log_warning("conversation thumb save failed")
        return
    finally:
        if staged is not None:
            remove_with_retry(staged)
    if prune:
        _prune()


def has_thumb(request_id: str) -> bool:
    """Whether a thumb is saved, without decoding the image."""
    path = _thumb_path(request_id)
    return path is not None and os.path.isfile(path)


def prune_thumbs() -> None:
    """Drop the oldest thumbs beyond the cap, once after a batch of saves."""
    _prune()


def load_thumb(request_id: str) -> QPixmap | None:
    """The locally saved thumb, or None (older generation, pruned, failed)."""
    path = _thumb_path(request_id)
    if path is None or not os.path.isfile(path):
        return None
    pixmap = QPixmap(path)
    return None if pixmap.isNull() else pixmap


def delete_thumb(request_id: str) -> None:
    """Drop one saved thumb (conversation deleted). Missing file is fine."""
    path = _thumb_path(request_id)
    if path is None:
        return
    try:
        os.remove(path)
    except OSError:  # nosec B110 - already gone is the goal.
        pass


def clear_thumbs() -> None:
    """Drop every saved thumb (history wiped from Account Settings)."""
    folder = _thumb_dir(create=False)
    if folder is None:
        return
    try:
        with os.scandir(folder) as scan:
            entries = list(scan)
    except OSError:
        return
    for entry in entries:
        try:
            if entry.name.endswith(".jpg") and not entry.name.startswith(".") and entry.is_file(follow_symlinks=False):
                os.remove(entry.path)
        except OSError:  # nosec B110
            pass


def _prune() -> None:
    """Drop the oldest files beyond the cap. Errors are non-fatal."""
    folder = _thumb_dir(create=False)
    if folder is None:
        return
    entries = []
    try:
        with os.scandir(folder) as scan:
            for entry in scan:
                try:
                    if (
                        entry.name.endswith(".jpg") and not entry.name.startswith(".")
                        and entry.is_file(follow_symlinks=False)
                    ):
                        entries.append((entry.stat(follow_symlinks=False).st_mtime, entry.path))
                except OSError:
                    continue
    except OSError:
        return
    entries.sort()
    cap = get_export_dial("pipeline.conversation_thumbs.thumb_cap", _THUMB_CAP)
    for _mtime, path in entries[: max(0, len(entries) - cap)]:
        try:
            os.remove(path)
        except OSError:  # nosec B110 - pruning is best-effort.
            pass
