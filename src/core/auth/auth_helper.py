
from __future__ import annotations

from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings

from ..logger import log_warning
from .activation_manager import SETTINGS_PREFIX

_AUTHCFG_KEY = f"{SETTINGS_PREFIX}authcfg_id"
_LEGACY_KEY = f"{SETTINGS_PREFIX}activation_key"
_MIGRATION_PENDING_KEY = f"{SETTINGS_PREFIX}auth_migration_pending"
_TIMESTAMP_KEY = f"{SETTINGS_PREFIX}activation_timestamp_unix"


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
    return s.value(_LEGACY_KEY, "", type=str)


def has_stored_activation(settings=None) -> bool:






    s = settings if settings is not None else QgsSettings()
    return bool(
        s.value(_AUTHCFG_KEY, "", type=str) or s.value(_LEGACY_KEY, "", type=str)
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
            s.setValue(_MIGRATION_PENDING_KEY, False)
            return

    s.setValue(_LEGACY_KEY, key)
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
        (_MIGRATION_PENDING_KEY, False),
        (_TIMESTAMP_KEY, ""),
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
