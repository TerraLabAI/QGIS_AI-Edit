"""Where output files go, and how they get there safely on Windows.

Output-directory resolution (settings override, project folder, Documents),
unique file naming, and the replace/remove helpers that ride out the short
locks Windows Defender, the search indexer and OneDrive put on a file that
was just written. ``src/core/raster_writer.py`` re-exports every name here.
"""
from __future__ import annotations

import os
import tempfile
import time

from .i18n import tr
from .logger import log_warning

OUTPUT_DIR_SETTING = "AIEdit/output_dir"

# Delays between attempts when Windows answers WinError 5 or 32 on a file a
# scanner holds for a moment. About 1.5 s in total: long enough for Defender
# to finish a GeoTIFF, short enough that a real lock fails fast.
_LOCK_RETRY_DELAYS_S = (0.05, 0.1, 0.2, 0.4, 0.8)


def ascii_safe_dir(directory: str) -> str:
    """Return a directory path both GDAL (write) and the QGIS GDAL provider
    (read-back) accept on Windows.

    Accented Windows usernames put non-ASCII characters in the output path.
    GDAL writes the GeoTIFF without error, but the QGIS raster provider then
    loads it as an invalid layer ("Failed to create valid raster layer").
    Converting the directory to its 8.3 short name yields a pure-ASCII path
    both accept. No-op on non-Windows, on already-ASCII paths, or when
    conversion is unavailable.

    Precondition: ``directory`` already exists (``os.makedirs`` it first). A
    path that is not there cannot be resolved, so it comes back untouched.
    """
    if os.name != "nt" or directory.isascii():
        return directory

    if not os.path.isdir(directory):
        # GetShortPathNameW answers 0 for a missing path, and 0 again on a
        # volume with 8.3 names off. Reading the first as the second sends a
        # caller that has not made its output directory yet to the shared
        # Public folder, and the user's file lands where they never chose.
        log_warning("ascii_safe_dir ran before the directory existed; path unchanged")
        return directory

    try:
        import ctypes
        from ctypes import wintypes

        # Private handle, so the argtypes below never leak to other plugins.
        get_short = ctypes.WinDLL("kernel32").GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(4096)
        n = get_short(directory, buf, len(buf))
        if 0 < n < len(buf) and buf.value.isascii():
            return buf.value
    except Exception as e:  # noqa: BLE001 - Windows-only, never block a write
        log_warning(f"short-path conversion failed: {e}")

    # 8.3 short names are disabled on this volume. C:\\Users\\Public is ASCII
    # and writable by every user; fall back to it so the layer still loads.
    # Output filenames are unique per generation (slug + timestamp), so a
    # single shared folder cannot collide.
    public = os.environ.get("PUBLIC")
    if public and public.isascii():
        safe = os.path.join(public, "terralab_ai_edit")
        try:
            os.makedirs(safe, exist_ok=True)
            return safe
        except OSError:
            pass
    return directory


def unique_output_path(directory: str, base: str, ext: str = "tif") -> str:
    """First free <base>.<ext> in directory; _2, _3... on same-second collisions.

    The name is reserved by creating an empty file with O_EXCL, so two
    threads writing the same prompt in the same second (a generation and a
    history "Add to map") never open one file twice. GDAL Create overwrites
    the placeholder. When the directory cannot take the placeholder, the
    first free name is returned unreserved and the caller's own write
    reports the real error.
    """
    for value in (base, ext):
        if (
            not isinstance(value, str) or not value or value in (".", "..")
            or any(char in value for char in ("/", "\\", "\x00"))
        ):
            raise ValueError("Output names must be single path components")
    counter = 1
    while True:
        name = f"{base}.{ext}" if counter == 1 else f"{base}_{counter}.{ext}"
        path = os.path.join(directory, name)
        counter += 1
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        except OSError:
            if os.path.exists(path):
                continue
            return path
        os.close(fd)
        return path


def replace_with_retry(source: str, destination: str) -> None:
    """os.replace that waits out a scanner's short lock on either file."""
    for delay in (*_LOCK_RETRY_DELAYS_S, None):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def remove_with_retry(path: str) -> bool:
    """Delete ``path``; True when it is gone. Never raises."""
    for delay in (*_LOCK_RETRY_DELAYS_S, None):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if delay is None:
                return False
            time.sleep(delay)
        except OSError:
            return False
    return False


def replace_staged_file(staged: str, destination: str) -> None:
    """Move a fully-written staging file onto its destination, or delete it.

    Windows refuses to overwrite a file another process holds open: a .tif
    already loaded as a layer, a .png open in the Photos app. Without the
    cleanup every failed attempt leaves a stray ``.part`` next to the user's
    file, and without ``os.replace`` the destination would have to be unlinked
    first, destroying the old copy when the new one cannot be put in place.
    """
    from .errors import AIEditError, ErrorCode

    try:
        replace_with_retry(staged, destination)
    except OSError as err:
        remove_with_retry(staged)
        raise AIEditError(
            ErrorCode.WRITE_ERROR,
            tr(
                "Could not write {name}. It may be open in QGIS or in another "
                "program. Close it, or pick a different name, and try again."
            ).format(name=os.path.basename(destination)),
            cause=err,
        ) from err


def documents_default_dir() -> str:
    """~/Documents/AI Edit via QStandardPaths so the OS picks the localized folder."""
    try:
        from qgis.PyQt.QtCore import QStandardPaths

        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    except Exception:
        base = ""
    if not base:
        base = os.path.expanduser("~")
    return os.path.normpath(os.path.join(base, "AI Edit"))


def clean_output_dir_text(text: str) -> str:
    """Folder text as a user pastes it, made usable, or "" to ignore it.

    Explorer's "Copy as path" wraps the path in double quotes, which Windows
    refuses in a path, so every generation used to drop to the temp folder.
    A relative folder resolves against the QGIS working directory, which is
    the read-only Program Files ``bin`` folder on Windows, so it is ignored.
    """
    if not isinstance(text, str) or "\x00" in text:
        return ""
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    if not value:
        return ""
    value = os.path.expandvars(os.path.expanduser(value))
    if not os.path.isabs(value):
        return ""
    return os.path.normpath(value)


def get_output_dir() -> str:
    """1) QSettings override, 2) <project_dir>/ai_edit_outputs/, 3) ~/Documents/AI Edit/."""
    from qgis.core import QgsProject, QgsSettings

    settings = QgsSettings()
    override = clean_output_dir_text(settings.value(OUTPUT_DIR_SETTING, "", type=str) or "")
    if override:
        return override

    project = QgsProject.instance()
    project_path = project.absoluteFilePath()
    if project_path:
        return os.path.join(os.path.dirname(os.path.normpath(project_path)), "ai_edit_outputs")

    return documents_default_dir()


def set_output_dir(path: str) -> None:
    from qgis.core import QgsSettings

    settings = QgsSettings()
    settings.setValue(OUTPUT_DIR_SETTING, clean_output_dir_text(path))
    try:
        settings.sync()
    except Exception:  # nosec B110
        pass


def fallback_output_dir() -> str:
    """A writable folder for a result the chosen folder refused.

    Documents first: the temp folder is emptied by Storage Sense and Disk
    Cleanup, and a saved project would later show the layer as broken.
    """
    candidate = documents_default_dir()
    try:
        os.makedirs(candidate, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".write_probe_", dir=candidate)
        try:
            os.close(fd)
        finally:
            os.remove(probe)
        return candidate
    except OSError as err:
        log_warning(f"Documents fallback not writable ({err}); using tempdir")
    return tempfile.mkdtemp(prefix="terralab_ai_edit_")
