"""Session-only store for user-supplied reference images.

Each image is compressed at insertion time (longest side 1536 px, the model's
effective per-image input budget at default media resolution) and persisted in a
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
TARGET_LONGEST_SIDE_PX = 1536
# A reference is side context, not the edited image, so near-lossless quality is
# wasteful: q90 looks the same to the model but keeps the upload small, which
# matters on slow uplinks and against the request-body size cap.
JPEG_QUALITY = 90
# webp quality for layer renders. 92 is visually near-lossless on map content
# yet ~4-5x smaller than PNG, which keeps the submit body under the cap.
WEBP_QUALITY = 92
_TMP_PREFIX = "qgis-ai-edit-refs-"
_SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def encode_references_b64(paths: list[str]) -> list[str]:
    """Base64-encode reference files. Runs in the generation worker so up to
    12 file reads never stall the UI thread at dispatch. A file gone between
    snapshot and run is skipped (removed reference or cleaned temp dir); a
    missing side-context image must never fail the generation."""
    out: list[str] = []
    for path in paths:
        try:
            with open(path, "rb") as f:
                out.append(base64.b64encode(f.read()).decode("ascii"))
        except OSError as err:
            log_debug(f"Reference image skipped (unreadable): {path} ({err})")
    return out


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
        # Id of the Mark up composite, if any. It is shown in the strip but sent
        # through the separate guidance channel (so the model gets the "these are
        # pointers, do not reproduce the marks" treatment), NOT as a context
        # image, so it is excluded from get_all_b64 by default.
        self._markup_id: str | None = None

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
        """Store a pre-rendered QImage as a high-quality webp reference.

        Used for QGIS-layer renders (basemaps, hillshade, vector linework,
        contours). webp keeps the render sharp (far less ringing than JPEG on
        synthetic edges) while staying ~4-5x smaller than lossless PNG, so a
        full-resolution reference fits in the submit body instead of tripping
        the serverless payload cap. Falls back to PNG if the webp encoder is
        missing. Shares the MAX_REFERENCES budget and the session temp dir
        with add().
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
        dest_path = os.path.join(self._tmp_dir, f"{ref_id}.webp")
        if not image.save(dest_path, "WEBP", WEBP_QUALITY):
            # webp encoder unavailable on this Qt build: fall back to PNG so the
            # reference still works, just larger.
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

    def mark_as_markup(self, ref_id: str | None) -> None:
        """Flag ``ref_id`` as the Mark up composite (replaces any prior flag)."""
        self._markup_id = ref_id

    def remove(self, ref_id: str) -> None:
        """Remove a reference by id. No-op if missing."""
        record = self._refs.pop(ref_id, None)
        if record is None:
            return
        if ref_id == self._markup_id:
            self._markup_id = None
        try:
            os.remove(record.path)
        except OSError as err:
            log_warning(f"Failed to delete reference image {ref_id}: {err}")

    def clear(self) -> None:
        """Remove all references."""
        self._markup_id = None
        for ref_id in list(self._refs.keys()):
            self.remove(ref_id)

    def list(self) -> list[ReferenceImage]:
        """Return references in insertion order."""
        return list(self._refs.values())

    def count(self) -> int:
        return len(self._refs)

    def snapshot_paths(self, include_markup: bool = False) -> list[str]:
        """Return the context-image file paths, in insertion order. Cheap
        main-thread snapshot handed to the generation worker, which encodes
        via encode_references_b64() off-thread.

        The Mark up composite is excluded by default (it ships through the
        guidance channel, so including it here would send the marks twice). Pass
        ``include_markup=True`` to get truly everything."""
        return [
            record.path
            for record in self._refs.values()
            if include_markup or record.id != self._markup_id
        ]

    def get_all_b64(self, include_markup: bool = False) -> list[str]:
        """Return the context-image references as base64, in insertion order.
        Reads every file inline; prefer snapshot_paths() + off-thread encoding
        on any UI-thread path."""
        return encode_references_b64(self.snapshot_paths(include_markup=include_markup))

    def get_markup_b64(self) -> tuple[str | None, str | None]:
        """Return ``(base64, format)`` of the Mark up composite, or (None, None)
        when there is none. Format is the on-disk encoding (webp/png), suitable
        for the guidance_format field."""
        if self._markup_id is None:
            return None, None
        record = self._refs.get(self._markup_id)
        if record is None:
            return None, None
        try:
            with open(record.path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError as err:
            log_warning(f"Failed to read markup image {self._markup_id}: {err}")
            return None, None
        fmt = os.path.splitext(record.path)[1].lstrip(".").lower() or "webp"
        return b64, fmt

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
