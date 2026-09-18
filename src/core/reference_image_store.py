"""Session-only store for user-supplied reference images.

Each image is compressed at insertion time (longest side 1536 px, the model's
effective per-image input budget at default media resolution) and persisted in a
per-session temp directory under the system temp folder, created on first
use. Cleared on plugin unload - no persistence across QGIS restarts. Folders a
crashed session left behind are swept once they are a day old.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QImage, QImageReader

from . import qt_compat as QtC
from .config_store import get_export_copy, get_export_dial
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
_active_dirs: set[str] = set()
# A session folder untouched this long belongs to a QGIS that did not unload
# cleanly (crash, killed process); a live session writes to its own folder.
_STALE_TMP_AGE_S = 24 * 3600
_SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
# libjpeg is the only decoder Qt drives at a reduced size. The other handlers
# decode in full and scale afterwards, so asking them for a scaled read only
# adds the scale. Measured on the same 8000x6000 files, plain read against
# scaled read, best of 3:
#   png   181 -> 255 ms (Qt 5.15.2)   163 -> 178 ms (Qt 6.8.1)
#   webp  107 -> 132 ms               232 -> 264 ms
#   bmp    45 ->  52 ms (4000x3000)    47 ->  53 ms
# so they keep the plain decode.
_SCALED_DECODE_FORMATS = frozenset({b"jpeg", b"jpg"})


def sweep_stale_reference_dirs(now: float | None = None) -> int:
    """Delete this module's session folders older than a day; return how many.

    Only folders named with this module's exact prefix are touched."""
    now = time.time() if now is None else now
    removed = 0
    try:
        base = tempfile.gettempdir()
        names = os.listdir(base)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(_TMP_PREFIX):
            continue
        path = os.path.join(base, name)
        if path in _active_dirs:
            continue
        try:
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            if now - os.path.getmtime(path) < _STALE_TMP_AGE_S:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            removed += 1
    if removed:
        log_debug(f"Swept {removed} stale reference folder(s)")
    return removed


def max_references() -> int:
    """Hard per-session reference cap, server-tunable. Read at add time so a
    config refresh applies in-session."""
    return get_export_dial("reference_encode.max_references", MAX_REFERENCES)


def _longest_side_target(width: int, height: int) -> QSize | None:
    """Size that fits the (server-tunable) longest-side cap, or None when
    ``width``x``height`` already fits."""
    target = get_export_dial("reference_encode.longest_side_px", TARGET_LONGEST_SIDE_PX)
    if max(width, height) <= target:
        return None
    if width >= height:
        return QSize(target, max(1, round(height * target / width)))
    return QSize(max(1, round(width * target / height)), target)


def _fit_longest_side(image: QImage) -> QImage:
    """Downscale so the longest side is at most the (server-tunable) target."""
    size = _longest_side_target(image.width(), image.height())
    if size is None:
        return image
    return image.scaled(
        size,
        QtC.KeepAspectRatio,
        QtC.SmoothTransformation,
    )


def _decode_scaled_source(source_path: str) -> QImage:
    """Decode a source file, letting a JPEG decode straight to the target size.

    ``QImage(path)`` decodes at full resolution first, on the main thread.
    Handing the target size to QImageReader lets libjpeg do it at a reduced
    scale instead. Measured on a detail-rich 8000x6000 (48 Mpx) JPEG, best of
    three: 422 -> 181 ms on QGIS 3.22.0 / Qt 5.15.2, 274 -> 191 ms on QGIS
    4.0.0 / Qt 6.8.1. So roughly 1.4x to 2.3x, not the order of magnitude a
    flat synthetic image suggests (211 -> 41 ms on Qt5 for the same pixel
    count). Whatever the reader cannot size falls through to a plain full
    decode, and _fit_longest_side still trims whatever comes back.

    This buys no relief from Qt 6's 256 MB QImageReader.allocationLimit, which
    is checked before the handler scales anything. Measured on QGIS 4.0.0: a
    10000x8000 PNG, 0.27 MB on disk and far inside the 50 MB source cap, reads
    null ("Unable to read image data") with and without setScaledSize, and only
    a raised allocationLimit gets it through. That file loads fine on Qt 5.15.2
    (no limit in that build) and fine as a JPEG on both. A PNG, WEBP or BMP
    whose full decode is over 256 MB therefore still ends in "Failed to decode
    image" on QGIS 4.
    """
    reader = QImageReader(source_path)
    reader.setAutoTransform(True)
    if bytes(reader.format()).lower() not in _SCALED_DECODE_FORMATS:
        return reader.read()
    size = reader.size()
    scaled = (
        _longest_side_target(size.width(), size.height())
        if size.isValid() and size.width() > 0 and size.height() > 0
        else None
    )
    if scaled is None:
        return reader.read()
    reader.setScaledSize(scaled)
    image = reader.read()
    if image.isNull():
        # A handler that chokes on a scaled read must not cost the user an
        # image a plain decode would have loaded.
        plain_reader = QImageReader(source_path)
        plain_reader.setAutoTransform(True)
        image = plain_reader.read()
    return image


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


def encode_references_with_notes(
    paths: list[str], notes: list[str]
) -> tuple[list[str], list[str]]:
    """Base64-encode reference files keeping the per-image notes aligned.

    Same skip rule as encode_references_b64 (an unreadable file must never
    fail the generation), but when a file is dropped its note is dropped with
    it, so ``context_image_notes`` stays index-for-index with
    ``context_images``. ``notes`` shorter than ``paths`` is padded with ""."""
    out_images: list[str] = []
    out_notes: list[str] = []
    for idx, path in enumerate(paths):
        try:
            with open(path, "rb") as f:
                out_images.append(base64.b64encode(f.read()).decode("ascii"))
        except OSError as err:
            log_debug(f"Reference image skipped (unreadable): {path} ({err})")
            continue
        out_notes.append(notes[idx] if idx < len(notes) else "")
    return out_images, out_notes


@dataclass(frozen=True)
class ReferenceImage:
    id: str
    path: str
    source_filename: str
    size_bytes: int
    # "file" (imported from disk), "layer" (rendered from a QGIS layer) or
    # "map" (a rectangle captured on the canvas).
    # Default keeps older constructions valid; only the store builds records.
    source_kind: str = "file"
    # A layer that does not touch the zone is rendered at its own extent and
    # sent whole, not aligned; the strip badges it so the user knows.
    whole_layer: bool = False


class ReferenceImageStoreError(Exception):
    """Raised when an image cannot be added (validation, IO, decode)."""


class ReferenceImageStore:
    """Session-only ordered store of compressed reference images."""

    def __init__(self) -> None:
        # Per-session temp dir under the system temp folder - guaranteed
        # user-writable (the plugin install dir may be read-only on Windows).
        # Made on first add, so a QGIS start that never uses AI Edit leaves
        # nothing behind.
        self._tmp_dir: str | None = None
        # Files Windows refused to delete (a worker was still reading them);
        # retried on the next removal and at cleanup.
        self._pending_deletes: list[str] = []
        # Insertion order matters (Python 3.7+ dict preserves it).
        self._refs: dict[str, ReferenceImage] = {}
        # Per-image user note ("what should the AI take from this image"),
        # keyed by ref id. Session-only, like the images themselves.
        self._notes: dict[str, str] = {}
        # Id of the Mark up composite, if any. It is shown in the strip but sent
        # through the separate guidance channel (so the model gets the "these are
        # pointers, do not reproduce the marks" treatment), NOT as a context
        # image, so it is excluded from get_all_b64 by default.
        self._markup_id: str | None = None

    def _session_dir(self) -> str:
        """The session temp dir, created (and stale ones swept) on first use."""
        if self._tmp_dir is None or not os.path.isdir(self._tmp_dir):
            if self._tmp_dir is None:
                sweep_stale_reference_dirs()
            if self._tmp_dir is not None:
                _active_dirs.discard(self._tmp_dir)
            self._tmp_dir = tempfile.mkdtemp(prefix=_TMP_PREFIX)
            _active_dirs.add(self._tmp_dir)
        return self._tmp_dir

    def _delete_file(self, path: str) -> bool:
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _retry_pending_deletes(self) -> None:
        self._pending_deletes = [
            path for path in self._pending_deletes if not self._delete_file(path)
        ]

    def _save_image(self, image: QImage, path: str, fmt: str, quality: int = -1) -> bool:
        """Register only complete files; remove partial encoder output on failure."""
        try:
            if image.save(path, fmt, quality) and os.path.getsize(path) > 0:
                return True
        except OSError:
            pass  # nosec B110 - report through the caller's existing error.
        if not self._delete_file(path):
            self._pending_deletes.append(path)
        return False

    # -- public API --------------------------------------------------------

    def add(self, source_path: str) -> ReferenceImage:
        """Compress and store a new reference image. Returns its record.

        Raises ReferenceImageStoreError if validation fails.
        """
        cap = max_references()
        if len(self._refs) >= cap:
            raise ReferenceImageStoreError(
                tr("Maximum {n} reference images reached").format(n=cap)
            )

        if not os.path.isfile(source_path):
            raise ReferenceImageStoreError(
                get_export_copy("pipeline.reference_image_store.file_missing", tr("File does not exist"))
            )

        ext = os.path.splitext(source_path)[1].lower()
        if ext not in _SUPPORTED_EXTS:
            raise ReferenceImageStoreError(
                get_export_copy(
                    "pipeline.reference_image_store.unsupported_format",
                    tr("Unsupported format. Use PNG, JPG, WEBP or BMP, or drop a QGIS layer."),
                )
            )

        try:
            src_size = os.path.getsize(source_path)
        except OSError as err:
            raise ReferenceImageStoreError(
                tr("Cannot read file: {err}").format(err=err)
            ) from err

        if src_size > get_export_dial("reference_encode.max_source_bytes", MAX_SOURCE_BYTES):
            raise ReferenceImageStoreError(
                get_export_copy("pipeline.reference_image_store.too_large", tr("Image too large (max 50 MB)"))
            )

        image = _decode_scaled_source(source_path)
        if image.isNull():
            raise ReferenceImageStoreError(
                get_export_copy("pipeline.reference_image_store.decode_failed", tr("Failed to decode image"))
            )

        image = _fit_longest_side(image)

        ref_id = uuid.uuid4().hex[:12]
        dest_path = os.path.join(self._session_dir(), f"{ref_id}.jpg")

        quality = min(100, get_export_dial("reference_encode.jpeg_quality", JPEG_QUALITY))
        if not self._save_image(image, dest_path, "JPEG", quality):
            raise ReferenceImageStoreError(
                get_export_copy(
                    "pipeline.reference_image_store.write_compressed_failed",
                    tr("Failed to write compressed image"),
                )
            )

        try:
            final_size = os.path.getsize(dest_path)
        except OSError:
            final_size = 0

        record = ReferenceImage(
            id=ref_id,
            path=dest_path,
            source_filename=os.path.basename(source_path),
            size_bytes=final_size,
            source_kind="file",
        )
        self._refs[ref_id] = record
        log_debug(
            f"Reference image added: id={ref_id}, "
            f"src_size={src_size}, final_size={final_size}, "
            f"count={len(self._refs)}"
        )
        return record

    def add_from_qimage(
        self,
        image: QImage,
        source_name: str,
        source_kind: str = "layer",
        whole_layer: bool = False,
    ) -> ReferenceImage:
        """Store a pre-rendered QImage as a high-quality webp reference.

        Used for QGIS-layer renders (basemaps, hillshade, vector linework,
        contours). webp keeps the render sharp (far less ringing than JPEG on
        synthetic edges) while staying ~4-5x smaller than lossless PNG, so a
        full-resolution reference fits in the submit body instead of tripping
        the serverless payload cap. Falls back to PNG if the webp encoder is
        missing. Shares the MAX_REFERENCES budget and the session temp dir
        with add().
        """
        cap = max_references()
        if len(self._refs) >= cap:
            raise ReferenceImageStoreError(
                tr("Maximum {n} reference images reached").format(n=cap)
            )
        if image is None or image.isNull():
            raise ReferenceImageStoreError(
                get_export_copy("pipeline.reference_image_store.render_failed", tr("Failed to render layer"))
            )

        image = _fit_longest_side(image)

        ref_id = uuid.uuid4().hex[:12]
        dest_path = os.path.join(self._session_dir(), f"{ref_id}.webp")
        quality = min(100, get_export_dial("reference_encode.webp_quality", WEBP_QUALITY))
        if not self._save_image(image, dest_path, "WEBP", quality):
            # webp encoder unavailable on this Qt build: fall back to PNG so the
            # reference still works, just larger.
            dest_path = os.path.join(self._session_dir(), f"{ref_id}.png")
            if not self._save_image(image, dest_path, "PNG"):
                raise ReferenceImageStoreError(
                    get_export_copy(
                        "pipeline.reference_image_store.write_rendered_failed",
                        tr("Failed to write rendered image"),
                    )
                )

        try:
            final_size = os.path.getsize(dest_path)
        except OSError:
            final_size = 0

        record = ReferenceImage(
            id=ref_id,
            path=dest_path,
            source_filename=source_name,
            size_bytes=final_size,
            source_kind=source_kind,
            whole_layer=bool(whole_layer),
        )
        self._refs[ref_id] = record
        log_debug(
            f"Reference image added from render: id={ref_id}, "
            f"final_size={final_size}, count={len(self._refs)}"
        )
        return record

    def mark_as_markup(self, ref_id: str | None) -> None:
        """Flag ``ref_id`` as the Mark up composite (replaces any prior flag)."""
        self._markup_id = ref_id if isinstance(ref_id, str) and ref_id in self._refs else None

    def is_markup(self, ref_id: str) -> bool:
        """True when ``ref_id`` is the Mark up composite (it ships through the
        guidance channel, so a per-image note on it would never be sent)."""
        return self._markup_id is not None and ref_id == self._markup_id

    def set_note(self, ref_id: str, note: str) -> None:
        """Store the user's per-image instruction. No-op for unknown ids; an
        empty note removes the entry so snapshots stay lean."""
        if not isinstance(ref_id, str) or ref_id not in self._refs or not isinstance(note, str):
            return
        if note:
            self._notes[ref_id] = note
        else:
            self._notes.pop(ref_id, None)

    def get_note(self, ref_id: str) -> str:
        return self._notes.get(ref_id, "") if isinstance(ref_id, str) else ""

    def snapshot_notes(self, include_markup: bool = False) -> list[str]:
        """Per-image notes aligned index-for-index with snapshot_paths(),
        whitespace-stripped, "" for images without a note. Same markup filter
        as snapshot_paths so the two lists always describe the same images."""
        return [
            self._notes.get(record.id, "").strip()
            for record in self._refs.values()
            if include_markup or record.id != self._markup_id
        ]

    def remove(self, ref_id: str) -> None:
        """Remove a reference by id. No-op if missing."""
        if not isinstance(ref_id, str):
            return
        record = self._refs.pop(ref_id, None)
        self._notes.pop(ref_id, None)
        if record is None:
            return
        if ref_id == self._markup_id:
            self._markup_id = None
        self._retry_pending_deletes()
        if not self._delete_file(record.path):
            log_warning(f"Reference image {ref_id} is in use; deletion deferred")
            self._pending_deletes.append(record.path)

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

    def total_size_bytes(self) -> int:
        return sum(r.size_bytes for r in self._refs.values())

    def cleanup(self) -> None:
        """Remove the temp directory entirely. Safe to call on unload()."""
        self._refs.clear()
        self._notes.clear()
        self._markup_id = None
        self._retry_pending_deletes()
        tmp_dir = self._tmp_dir
        if tmp_dir is None:
            return
        if os.path.isdir(tmp_dir):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception as err:  # nosec B110
                log_warning(f"Failed to clean reference tmp dir: {err}")
        if os.path.isdir(tmp_dir):
            # Something still holds a file. The next start sweeps the folder
            # once it is a day old.
            log_warning("Reference tmp dir still in use; left for the next sweep")
        _active_dirs.discard(tmp_dir)
        self._pending_deletes = []
        self._tmp_dir = None
