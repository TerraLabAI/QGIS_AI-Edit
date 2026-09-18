"""The first-run privacy notice: has this profile accepted the current one?

The notice (ui/dialogs/privacy_notice_dialog.py) is shown once, before the
first network call that is not strictly needed to sign in, and it tells the
user what leaves the machine, where it goes and what they can switch off.
Until it is accepted the telemetry collector (core/telemetry.py) drops every
event, so no usage statistic linked to the account is sent to anyone who has
not read the notice. Existing installs never saw it, so they get it too.

The acceptance is stored with the VERSION of the notice that was accepted.
Bump `PRIVACY_NOTICE_VERSION` when the policy or the notice text changes in a
way the user must see again, and every profile gets the dialog once more.

No qgis import at module level: the headless test layer imports this file
without QGIS installed. A scratch `settings` object with the QSettings
value/setValue surface can stand in for the profile.
"""
from __future__ import annotations

PRIVACY_NOTICE_VERSION = 1

# Same prefix as core/auth/activation_manager.SETTINGS_PREFIX, repeated here so
# this module keeps zero imports (telemetry.py must stay importable from every
# layer and reads this on every event).
PRIVACY_NOTICE_KEY = "AIEdit/privacy_notice_accepted_version"

# Session memo, same discipline as the consent memo in activation_manager: it
# stands for the QGIS profile only. `save_privacy_notice_accepted()` with no
# settings is the only writer that refreshes it; `reset_privacy_notice_memo`
# is for anything that rewrites the profile behind our back.
_accepted_memo: bool | None = None


def _read_version(settings) -> int:
    try:
        return int(settings.value(PRIVACY_NOTICE_KEY, 0, type=int))
    except Exception:  # nosec B110 - a bad value counts as not accepted
        return 0


def has_accepted_privacy_notice(settings=None) -> bool:
    """True when the profile accepted the CURRENT notice version.

    Fails closed: when the profile cannot be read, nothing is sent."""
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
    """Record that the user ticked the box and pressed Continue."""
    global _accepted_memo
    if settings is not None:
        settings.setValue(PRIVACY_NOTICE_KEY, PRIVACY_NOTICE_VERSION)
        return
    from qgis.PyQt.QtCore import QSettings
    QSettings().setValue(PRIVACY_NOTICE_KEY, PRIVACY_NOTICE_VERSION)
    _accepted_memo = True


def reset_privacy_notice_memo() -> None:
    """Force the next `has_accepted_privacy_notice()` back to QSettings."""
    global _accepted_memo
    _accepted_memo = None
