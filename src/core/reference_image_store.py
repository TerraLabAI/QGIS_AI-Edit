"""Session-only store for user-supplied reference images.

Each image is compressed to 1K JPEG q98 at insertion time and persisted in a
per-session temp directory under the system temp folder. Cleared on plugin
unload - no persistence across QGIS restarts.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QImage

from . import qt_compat as QtC
from .i18n import tr
from .logger import log_debug, log_warning

MAX_REFERENCES = 12
MAX_SOURCE_BYTES = 50 * 1024 * 1024  # 50 MB on disk before compression
TARGET_LONGEST_SIDE_PX = 1024
JPEG_QUALITY = 98
_TMP_PREFIX = "qgis-ai-edit-refs-"
_SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass(frozen=True)
class ReferenceImage:
    id: str
    path: str
    source_filename: str
    size_bytes: int


class ReferenceImageStoreError(Exception):
    """Raised when an image cannot be added (validation, IO, decode)."""


class ReferenceImageStore:
    """Session-only ordered store of compressed reference images."""

    def __init__(self) -> None:
        # Per-session temp dir under the system temp folder - guaranteed
        # user-writable (the plugin install dir may be read-only on Windows).
        self._tmp_dir = tempfile.mkdtemp(prefix=_TMP_PREFIX)
        # Insertion order matters (Python 3.7+ dict preserves it).
        self._refs: dict[str, ReferenceImage] = {}

    # -- public API --------------------------------------------------------

    def add(self, source_path: str) -> ReferenceImage:
        """Compress and store a new reference image. Returns its record.

        Raises ReferenceImageStoreError if validation fails.
        """
        if len(self._refs) >= MAX_REFERENCES:
            raise ReferenceImageStoreError(
                tr("Maximum {n} reference images reached").format(n=MAX_REFERENCES)
            )

        if not os.path.isfile(source_path):
            raise ReferenceImageStoreError(tr("File does not exist"))

        ext = os.path.splitext(source_path)[1].lower()
        if ext not in _SUPPORTED_EXTS:
            raise ReferenceImageStoreError(
                tr("Unsupported format. Use PNG, JPG, WEBP or BMP, or drop a QGIS layer.")
            )

        try:
            src_size = os.path.getsize(source_path)
        except OSError as err:
            raise ReferenceImageStoreError(
                tr("Cannot read file: {err}").format(err=err)
            ) from err

        if src_size > MAX_SOURCE_BYTES:
            raise ReferenceImageStoreError(tr("Image too large (max 50 MB)"))

        image = QImage(source_path)
        if image.isNull():
            raise ReferenceImageStoreError(tr("Failed to decode image"))

        # Scale longest side down to target if needed.
        longest = max(image.width(), image.height())
        if longest > TARGET_LONGEST_SIDE_PX:
            if image.width() >= image.height():
                new_w = TARGET_LONGEST_SIDE_PX
                new_h = max(1, round(image.height() * TARGET_LONGEST_SIDE_PX / image.width()))
            else:
                new_h = TARGET_LONGEST_SIDE_PX
                new_w = max(1, round(image.width() * TARGET_LONGEST_SIDE_PX / image.height()))
            image = image.scaled(
                QSize(new_w, new_h),
                QtC.KeepAspectRatio,
                QtC.SmoothTransformation,
            )

        ref_id = uuid.uuid4().hex[:12]
        dest_path = os.path.join(self._tmp_dir, f"{ref_id}.jpg")

        if not image.save(dest_path, "JPEG", JPEG_QUALITY):
            raise ReferenceImageStoreError(tr("Failed to write compressed image"))

        try:
            final_size = os.path.getsize(dest_path)
        except OSError:
            final_size = 0

        record = ReferenceImage(
            id=ref_id,
            path=dest_path,
            source_filename=os.path.basename(source_path),
            size_bytes=final_size,
        )
        self._refs[ref_id] = record
        log_debug(
            f"Reference image added: id={ref_id}, "
            f"src_size={src_size}, final_size={final_size}, "
            f"count={len(self._refs)}"
        )
        return record

    def add_from_qimage(self, image: QImage, source_name: str) -> ReferenceImage:
        """Store a pre-rendered QImage as a lossless PNG reference.

        Used for QGIS-layer renders (hillshade, vector linework, contours)
        where JPEG ringing would visibly degrade synthetic detail. Shares the
        MAX_REFERENCES budget and the session temp dir with add().
        """
        if len(self._refs) >= MAX_REFERENCES:
            raise ReferenceImageStoreError(
                tr("Maximum {n} reference images reached").format(n=MAX_REFERENCES)
            )
        if image is None or image.isNull():
            raise ReferenceImageStoreError(tr("Failed to render layer"))

        longest = max(image.width(), image.height())
        if longest > TARGET_LONGEST_SIDE_PX:
            if image.width() >= image.height():
                new_w = TARGET_LONGEST_SIDE_PX
                new_h = max(1, round(image.height() * TARGET_LONGEST_SIDE_PX / image.width()))
            else:
                new_h = TARGET_LONGEST_SIDE_PX
                new_w = max(1, round(image.width() * TARGET_LONGEST_SIDE_PX / image.height()))
            image = image.scaled(
                QSize(new_w, new_h),
                QtC.KeepAspectRatio,
                QtC.SmoothTransformation,
            )

        ref_id = uuid.uuid4().hex[:12]
        dest_path = os.path.join(self._tmp_dir, f"{ref_id}.png")
        if not image.save(dest_path, "PNG"):
            raise ReferenceImageStoreError(tr("Failed to write rendered image"))

        try:
            final_size = os.path.getsize(dest_path)
        except OSError:
            final_size = 0

        record = ReferenceImage(
            id=ref_id,
            path=dest_path,
            source_filename=source_name,
            size_bytes=final_size,
        )
        self._refs[ref_id] = record
        log_debug(
            f"Reference image added from render: id={ref_id}, "
            f"final_size={final_size}, count={len(self._refs)}"
        )
        return record

    def remove(self, ref_id: str) -> None:
        """Remove a reference by id. No-op if missing."""
        record = self._refs.pop(ref_id, None)
        if record is None:
            return
        try:
            os.remove(record.path)
        except OSError as err:
            log_warning(f"Failed to delete reference image {ref_id}: {err}")

    def clear(self) -> None:
        """Remove all references."""
        for ref_id in list(self._refs.keys()):
            self.remove(ref_id)

    def list(self) -> list[ReferenceImage]:
        """Return references in insertion order."""
        return list(self._refs.values())

    def count(self) -> int:
        return len(self._refs)

    def get_all_b64(self) -> list[str]:
        """Return all stored references as base64 strings, in insertion order."""
        out: list[str] = []
        for record in self._refs.values():
            try:
                with open(record.path, "rb") as f:
                    out.append(base64.b64encode(f.read()).decode("ascii"))
            except OSError as err:
                log_warning(f"Failed to read reference image {record.id}: {err}")
        return out

    def total_size_bytes(self) -> int:
        return sum(r.size_bytes for r in self._refs.values())

    def cleanup(self) -> None:
        """Remove the temp directory entirely. Safe to call on unload()."""
        self._refs.clear()
        if os.path.isdir(self._tmp_dir):
            try:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
            except Exception as err:  # nosec B110
                log_warning(f"Failed to clean reference tmp dir: {err}")
