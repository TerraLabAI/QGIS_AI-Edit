






from __future__ import annotations

import os
import tempfile
import time

from .i18n import tr
from .logger import log_warning

OUTPUT_DIR_SETTING = "AIEdit/output_dir"




_LOCK_RETRY_DELAYS_S = (0.05, 0.1, 0.2, 0.4, 0.8)


def ascii_safe_dir(directory: str) -> str:













    if os.name != "nt" or directory.isascii():
        return directory

    if not os.path.isdir(directory):




        log_warning("ascii_safe_dir ran before the directory existed; path unchanged")
        return directory

    try:
        import ctypes
        from ctypes import wintypes


        get_short = ctypes.WinDLL("kernel32").GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(4096)
        n = get_short(directory, buf, len(buf))
        if 0 < n < len(buf) and buf.value.isascii():
            return buf.value
    except Exception as e:  # noqa: BLE001
        log_warning(f"short-path conversion failed: {e}")





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

    for delay in (*_LOCK_RETRY_DELAYS_S, None):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def remove_with_retry(path: str) -> bool:

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

    try:
        from qgis.PyQt.QtCore import QStandardPaths

        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    except Exception:
        base = ""
    if not base:
        base = os.path.expanduser("~")
    return os.path.normpath(os.path.join(base, "AI Edit"))


def clean_output_dir_text(text: str) -> str:







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
