







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
MAX_SOURCE_BYTES = 50 * 1024 * 1024



TARGET_LONGEST_SIDE_PX = 1024
JPEG_QUALITY = 85
WEBP_QUALITY = 85
_TMP_PREFIX = "qgis-ai-edit-refs-"
_active_dirs: set[str] = set()


_STALE_TMP_AGE_S = 24 * 3600
_SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}








_SCALED_DECODE_FORMATS = frozenset({b"jpeg", b"jpg"})


def sweep_stale_reference_dirs(now: float | None = None) -> int:



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


    return get_export_dial("reference_encode.max_references", MAX_REFERENCES)


def _longest_side_target(width: int, height: int) -> QSize | None:


    target = get_export_dial("reference_encode.longest_side_px", TARGET_LONGEST_SIDE_PX)
    if max(width, height) <= target:
        return None
    if width >= height:
        return QSize(target, max(1, round(height * target / width)))
    return QSize(max(1, round(width * target / height)), target)


def _fit_longest_side(image: QImage) -> QImage:

    size = _longest_side_target(image.width(), image.height())
    if size is None:
        return image
    return image.scaled(
        size,
        QtC.KeepAspectRatio,
        QtC.SmoothTransformation,
    )


def _decode_scaled_source(source_path: str) -> QImage:




















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


        plain_reader = QImageReader(source_path)
        plain_reader.setAutoTransform(True)
        image = plain_reader.read()
    return image


def encode_references_b64(paths: list[str]) -> list[str]:




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



    source_kind: str = "file"


    whole_layer: bool = False


class ReferenceImageStoreError(Exception):
    pass


class ReferenceImageStore:


    def __init__(self) -> None:




        self._tmp_dir: str | None = None


        self._pending_deletes: list[str] = []

        self._refs: dict[str, ReferenceImage] = {}


        self._notes: dict[str, str] = {}




        self._markup_id: str | None = None

    def _session_dir(self) -> str:

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

        try:
            if image.save(path, fmt, quality) and os.path.getsize(path) > 0:
                return True
        except OSError:
            pass  # nosec B110
        if not self._delete_file(path):
            self._pending_deletes.append(path)
        return False



    def add(self, source_path: str) -> ReferenceImage:




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

        self._markup_id = ref_id if isinstance(ref_id, str) and ref_id in self._refs else None

    def is_markup(self, ref_id: str) -> bool:


        return self._markup_id is not None and ref_id == self._markup_id

    def set_note(self, ref_id: str, note: str) -> None:


        if not isinstance(ref_id, str) or ref_id not in self._refs or not isinstance(note, str):
            return
        if note:
            self._notes[ref_id] = note
        else:
            self._notes.pop(ref_id, None)

    def get_note(self, ref_id: str) -> str:
        return self._notes.get(ref_id, "") if isinstance(ref_id, str) else ""

    def snapshot_notes(self, include_markup: bool = False) -> list[str]:



        return [
            self._notes.get(record.id, "").strip()
            for record in self._refs.values()
            if include_markup or record.id != self._markup_id
        ]

    def remove(self, ref_id: str) -> None:

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

        self._markup_id = None
        for ref_id in list(self._refs.keys()):
            self.remove(ref_id)

    def list(self) -> list[ReferenceImage]:

        return list(self._refs.values())

    def count(self) -> int:
        return len(self._refs)

    def snapshot_paths(self, include_markup: bool = False) -> list[str]:







        return [
            record.path
            for record in self._refs.values()
            if include_markup or record.id != self._markup_id
        ]

    def get_all_b64(self, include_markup: bool = False) -> list[str]:



        return encode_references_b64(self.snapshot_paths(include_markup=include_markup))

    def total_size_bytes(self) -> int:
        return sum(r.size_bytes for r in self._refs.values())

    def cleanup(self) -> None:

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


            log_warning("Reference tmp dir still in use; left for the next sweep")
        _active_dirs.discard(tmp_dir)
        self._pending_deletes = []
        self._tmp_dir = None
