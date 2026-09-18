
















from __future__ import annotations

PRIVACY_NOTICE_VERSION = 1




PRIVACY_NOTICE_KEY = "AIEdit/privacy_notice_accepted_version"





_accepted_memo: bool | None = None


def _read_version(settings) -> int:
    try:
        return int(settings.value(PRIVACY_NOTICE_KEY, 0, type=int))
    except Exception:  # nosec B110
        return 0


def has_accepted_privacy_notice(settings=None) -> bool:



    global _accepted_memo
    if settings is not None:
        return _read_version(settings) >= PRIVACY_NOTICE_VERSION
    if _accepted_memo is None:
        try:
            from qgis.PyQt.QtCore import QSettings
            _accepted_memo = _read_version(QSettings()) >= PRIVACY_NOTICE_VERSION
        except Exception:  # nosec B110
            return False
    return _accepted_memo


def save_privacy_notice_accepted(settings=None) -> None:

    global _accepted_memo
    if settings is not None:
        settings.setValue(PRIVACY_NOTICE_KEY, PRIVACY_NOTICE_VERSION)
        return
    from qgis.PyQt.QtCore import QSettings
    QSettings().setValue(PRIVACY_NOTICE_KEY, PRIVACY_NOTICE_VERSION)
    _accepted_memo = True


def reset_privacy_notice_memo() -> None:

    global _accepted_memo
    _accepted_memo = None
