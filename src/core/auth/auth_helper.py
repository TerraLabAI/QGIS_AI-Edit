
from __future__ import annotations

import base64
import sys

from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings

from ..logger import log_warning
from .activation_manager import ACTIVATION_TIMESTAMP_KEY, SETTINGS_PREFIX

_AUTHCFG_KEY = f"{SETTINGS_PREFIX}authcfg_id"
_LEGACY_KEY = f"{SETTINGS_PREFIX}activation_key"
_MIGRATION_PENDING_KEY = f"{SETTINGS_PREFIX}auth_migration_pending"









_SEALED_KEY = f"{SETTINGS_PREFIX}activation_key_dpapi"
_DPAPI_PREFIX = "dpapi1:"
_DPAPI_ENTROPY = b"TerraLab AI Agent activation key"


def _dpapi(data: bytes, seal: bool) -> bytes | None:

    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        def _blob(raw: bytes):
            buffer = ctypes.create_string_buffer(raw, len(raw))
            return _Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        call = crypt32.CryptProtectData if seal else crypt32.CryptUnprotectData
        call.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        call.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        source, _keep = _blob(data)
        entropy, _keep_entropy = _blob(_DPAPI_ENTROPY)
        out = _Blob()

        if not call(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1,
                    ctypes.byref(out)):
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
    except Exception:  # noqa: BLE001
        return None


def _seal(key: str) -> str:

    sealed = _dpapi(key.encode("utf-8"), seal=True)
    return _DPAPI_PREFIX + base64.b64encode(sealed).decode("ascii") if sealed else ""


def _unseal(value: str) -> str:

    if not value or not value.startswith(_DPAPI_PREFIX):
        return ""
    try:
        opened = _dpapi(base64.b64decode(value[len(_DPAPI_PREFIX):]), seal=False)
        return opened.decode("utf-8") if opened else ""
    except (ValueError, UnicodeDecodeError):
        return ""


def _get_auth_manager():
    try:
        return QgsApplication.authManager()
    except Exception:
        return None


def _can_use_auth_manager() -> bool:

    am = _get_auth_manager()
    if am is None:
        return False
    try:
        return bool(am.masterPasswordIsSet())
    except Exception:
        return False


def _read_from_auth_manager(authcfg_id: str) -> str:
    am = _get_auth_manager()
    if am is None or not authcfg_id:
        return ""
    try:
        cfg = QgsAuthMethodConfig()
        ok = am.loadAuthenticationConfig(authcfg_id, cfg, True)
        if not ok:
            return ""
        return cfg.config("password", "") or ""
    except Exception:
        return ""


def _store_to_auth_manager(key: str) -> str:
    am = _get_auth_manager()
    if am is None:
        return ""
    try:
        cfg = QgsAuthMethodConfig()
        cfg.setName("AI Edit activation key")
        cfg.setMethod("Basic")
        cfg.setConfig("password", key)
        ok = am.storeAuthenticationConfig(cfg)
        if not ok:
            return ""
        return cfg.id() or ""
    except Exception:
        return ""


def _set_if_changed(s, key: str, value) -> bool:


    try:
        current = s.value(key, None)
    except Exception:
        current = None
    same_value = current == value or (
        isinstance(value, bool) and str(current).lower() == str(value).lower()
    )
    if current is not None and same_value:
        return False
    if current is None and value in ("", False):
        return False
    s.setValue(key, value)
    return True


def get_activation_key(settings=None) -> str:
    s = settings if settings is not None else QgsSettings()
    authcfg_id = s.value(_AUTHCFG_KEY, "", type=str)
    if authcfg_id:
        key = _read_from_auth_manager(authcfg_id)
        if key:
            return key
    key = _unseal(s.value(_SEALED_KEY, "", type=str))
    if key:
        return key
    key = s.value(_LEGACY_KEY, "", type=str)
    if key and not authcfg_id and not s.value(_SEALED_KEY, "", type=str):


        sealed = _seal(key)
        if sealed:
            s.setValue(_SEALED_KEY, sealed)
    return key


def has_stored_activation(settings=None) -> bool:






    s = settings if settings is not None else QgsSettings()
    return bool(
        s.value(_AUTHCFG_KEY, "", type=str)
        or s.value(_SEALED_KEY, "", type=str)
        or s.value(_LEGACY_KEY, "", type=str)
    )


def save_activation(key: str, settings=None) -> None:
    key = (key or "").strip()
    s = settings if settings is not None else QgsSettings()
    if not key:
        clear_activation(s)
        return

    if _can_use_auth_manager():
        authcfg_id = _store_to_auth_manager(key)
        if authcfg_id:
            s.setValue(_AUTHCFG_KEY, authcfg_id)
            s.setValue(_LEGACY_KEY, "")
            _set_if_changed(s, _SEALED_KEY, "")
            s.setValue(_MIGRATION_PENDING_KEY, False)
            return

    s.setValue(_LEGACY_KEY, key)
    _set_if_changed(s, _SEALED_KEY, _seal(key))
    _set_if_changed(s, _AUTHCFG_KEY, "")
    _set_if_changed(s, _MIGRATION_PENDING_KEY, True)


def clear_activation(settings=None) -> None:
    s = settings if settings is not None else QgsSettings()
    authcfg_id = s.value(_AUTHCFG_KEY, "", type=str)
    if authcfg_id:
        am = _get_auth_manager()
        if am is not None:
            try:
                am.removeAuthenticationConfig(authcfg_id)
            except Exception:  # nosec B110
                pass


    changed = False
    for key, empty in (
        (_AUTHCFG_KEY, ""),
        (_LEGACY_KEY, ""),
        (_SEALED_KEY, ""),
        (_MIGRATION_PENDING_KEY, False),
        (ACTIVATION_TIMESTAMP_KEY, ""),
    ):
        changed = _set_if_changed(s, key, empty) or changed
    if not changed:
        return
    try:
        s.sync()
    except Exception:  # nosec B110
        pass


def migrate_legacy_key(settings=None) -> bool:

    s = settings if settings is not None else QgsSettings()
    legacy = s.value(_LEGACY_KEY, "", type=str)
    authcfg = s.value(_AUTHCFG_KEY, "", type=str)

    if not legacy:
        return True
    if authcfg and _read_from_auth_manager(authcfg) == legacy:
        s.setValue(_LEGACY_KEY, "")
        s.setValue(_MIGRATION_PENDING_KEY, False)
        try:
            s.sync()
        except Exception:  # nosec B110
            pass
        return True

    if not _can_use_auth_manager():



        if not authcfg and not s.value(_SEALED_KEY, "", type=str):
            _set_if_changed(s, _SEALED_KEY, _seal(legacy))
        _set_if_changed(s, _MIGRATION_PENDING_KEY, True)
        return False

    new_id = _store_to_auth_manager(legacy)
    if not new_id:
        _set_if_changed(s, _MIGRATION_PENDING_KEY, True)
        log_warning("Auth migration failed: storeAuthenticationConfig returned empty id")
        return False

    s.setValue(_AUTHCFG_KEY, new_id)
    s.setValue(_LEGACY_KEY, "")
    s.setValue(_MIGRATION_PENDING_KEY, False)
    try:
        s.sync()
    except Exception:  # nosec B110
        pass
    return True
