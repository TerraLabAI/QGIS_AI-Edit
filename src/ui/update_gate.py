"""The update offer: when to show it, how hard to push it, and the card that
replaces the dock while an update is required.

Ported from AI Agent (``src/plugin.py`` ``_offer_upgradeable_update`` and
``src/ui/update_gate.py``). Three served numbers decide it, all editable in
``/api/plugin/config`` without a release:

* ``latest_version`` says a newer build is out;
* ``min_supported_version`` says the installed one is too old for the servers,
  which makes the update required whatever the policy says;
* ``update_policy`` says whether an installable update is the soft banner with
  Later (``"recommend"``) or the card that replaces the dock (``"require"``).
  AI Edit's default, when the server sends nothing, is ``"recommend"``.

The offer is only ever made for a version QGIS's installer lists as
upgradeable: a wall the user cannot get past in one click is not a gate, it is
a broken plugin. A newer served version QGIS has not listed yet asks for one
quiet repository refresh per session and looks again when it answers.

The dock owns the telemetry (``PLUGIN_UPDATE_PROMPT_*``); nothing here tracks.
"""
from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core.config_store import get_export_copy
from ..core.i18n import tr
from .cross_plugin_discovery import installer_upgradeable_version
from .dock.design_tokens import (
    BODY_QSS,
    BTN_PRIMARY_WIDE_PX,
    BTN_PRIMARY_WIDE_QSS,
    FONT_BASE,
    FONT_HINT,
    HINT_QSS,
    INK,
    LINE,
    RADIUS_BOX,
    SURFACE,
    category_ink,
    category_line,
    category_tint,
)
from .icons import logo_pixmap

POLICY_REQUIRE = "require"
POLICY_RECOMMEND = "recommend"
DEFAULT_POLICY = POLICY_RECOMMEND

STATE_NONE = "none"             # nothing to offer
STATE_OFFER = "offer"           # QGIS can install ``version`` now
STATE_REFRESH = "refresh"       # served newer, not listed yet: refresh the repository
STATE_FLOOR = "floor_blocked"   # listed version is still below the supported floor

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- versions ------------------------------------------------------------------


def read_update_version(text) -> tuple | None:
    """``"1.7.5"`` as ``(1, 7, 5)``; None for anything that is not dotted ints.

    A pre-release or build tag (``1.8.0-dev``, ``1.8.0+3``) is dropped, and
    ``v1.8`` is read as 1.8. Anything else is unreadable, and an unreadable
    version never claims to be newer.
    """
    if not isinstance(text, str):
        return None
    value = text.strip().lstrip("vV")
    for sep in ("-", "+"):
        value = value.split(sep, 1)[0]
    parts = value.split(".")
    if not parts or len(parts) > 4 or not all(p.isdigit() and len(p) <= 6 for p in parts):
        return None
    numbers = [int(p) for p in parts] + [0] * (4 - len(parts))
    return tuple(numbers)


def is_version_newer(candidate, installed) -> bool:
    """True when ``candidate`` reads strictly above ``installed``; False on garbage."""
    left, right = read_update_version(candidate), read_update_version(installed)
    if left is None or right is None:
        return False
    return left > right


def installed_plugin_version() -> str:
    """``version=`` from this plugin's metadata.txt, "" when unreadable."""
    try:
        with open(os.path.join(_PLUGIN_ROOT, "metadata.txt"), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def plugin_installer_key() -> str:
    """The folder QGIS's installer files this plugin under."""
    return os.path.basename(_PLUGIN_ROOT)


# --- served values ---------------------------------------------------------------


def _served(key: str) -> str:
    try:
        from ..core.auth.activation_manager import get_server_config

        value = get_server_config().get(key)
    except Exception:  # noqa: BLE001 - no config yet is no offer
        return ""
    return value.strip() if isinstance(value, str) else ""


def served_update_policy() -> str:
    """``require`` or ``recommend``; the plugin default for anything else."""
    value = _served("update_policy").lower()
    return value if value in (POLICY_REQUIRE, POLICY_RECOMMEND) else DEFAULT_POLICY


def served_update_note() -> str:
    """The one served sentence under the offer: release note, then update message."""
    try:
        from ..core.auth.activation_manager import served_release_notes_line, served_update_message

        return served_release_notes_line() or served_update_message() or ""
    except Exception:  # noqa: BLE001
        return ""


# --- the decision ----------------------------------------------------------------


def evaluate_update(key: str | None = None) -> dict:
    """What the dock should do about updates right now.

    Returns ``{"state", "version", "installed", "served", "note", "required"}``.
    ``state`` is one of ``STATE_*``. Only ``STATE_OFFER`` shows anything:
    ``required`` then says the card replaces the dock (``UpdateGateCard``)
    rather than the soft banner. ``STATE_REFRESH`` asks the caller for
    ``request_repository_refresh``; ``STATE_FLOOR`` is logged, not shown: the
    repository only has a version that would still be refused, and offering it
    as the fix would be a promise the update cannot keep.
    """
    installed = installed_plugin_version()
    served = _served("latest_version")
    floor = _served("min_supported_version")
    out = {"state": STATE_NONE, "version": "", "installed": installed,
           "served": served, "note": "", "required": False}
    too_old = is_version_newer(floor, installed)
    if not installed or (not is_version_newer(served, installed) and not too_old):
        return out
    available = installer_upgradeable_version(key or plugin_installer_key())
    if not available:
        out["state"] = STATE_REFRESH
        return out
    required = too_old or served_update_policy() == POLICY_REQUIRE
    if too_old and is_version_newer(floor, available):
        out["state"] = STATE_FLOOR
        out["version"] = available
        return out
    out.update(state=STATE_OFFER, version=available, note=served_update_note(), required=required)
    return out


# --- Later ---------------------------------------------------------------------

# Versions the user put off with Later. For this QGIS session only, as in AI
# Agent and AI Segmentation: the next session offers the update again.
_LATER_VERSIONS: set = set()


def dismiss_update_for_session(version: str) -> None:
    """Later: keep ``version``'s soft banner down until QGIS restarts."""
    if version:
        _LATER_VERSIONS.add(str(version))


def is_update_dismissed_for_session(version: str) -> bool:
    """True when Later was pressed for ``version`` in this session. A
    required update ignores it: it has no Later."""
    return bool(version) and str(version) in _LATER_VERSIONS


# --- the repository refresh ---------------------------------------------------------

_REFRESH = {"requested": False, "callbacks": []}


def request_repository_refresh(on_done) -> str:
    """One quiet repository refresh per QGIS session; ``on_done()`` when it answers.

    ``fetchAvailablePlugins()`` opens a modal dialog and exec()s it, which
    freezes QGIS; ``requestFetching`` does the same work quietly and
    ``checkingDone`` says when the list is fresh. Returns "" when a fetch is
    under way, else the reason nothing was asked for, for
    ``PLUGIN_UPDATE_PROMPT_SUPPRESSED``: ``already_requested``,
    ``installer_unavailable``, ``no_repository`` or ``refresh_failed``.
    """
    if _REFRESH["requested"]:
        return "already_requested"
    _REFRESH["requested"] = True
    try:
        from pyplugin_installer.installer_data import repositories
    except Exception:  # noqa: BLE001
        return "installer_unavailable"
    try:
        enabled = list(repositories.allEnabled())
        if not enabled:
            return "no_repository"
        _REFRESH["callbacks"].append(on_done)
        repositories.checkingDone.connect(_on_repository_checked)
        for repo in enabled:
            repositories.requestFetching(repo, force_reload=True)
    except Exception:  # noqa: BLE001
        return "refresh_failed"
    return ""


def _on_repository_checked() -> None:
    """The repository answered: rebuild the installer's list, then call back."""
    try:
        from pyplugin_installer.installer_data import plugins, repositories

        try:
            repositories.checkingDone.disconnect(_on_repository_checked)
        except (TypeError, RuntimeError):
            pass
        plugins.rebuild()
    except Exception:  # noqa: BLE001
        pass  # nosec B110 - the callbacks look again and find nothing
    callbacks, _REFRESH["callbacks"] = _REFRESH["callbacks"], []
    for callback in callbacks:
        try:
            callback()
        except Exception:  # noqa: BLE001 - a callback into a dead dock
            continue  # nosec B112


def disconnect_repository_refresh(on_done=None) -> None:
    """Drop a pending callback on unload: the answer must not reach a dead dock.

    The per-session flag stays set: a reloaded plugin does not fetch again.
    """
    if on_done is None:
        _REFRESH["callbacks"] = []
    else:
        _REFRESH["callbacks"] = [c for c in _REFRESH["callbacks"] if c != on_done]
    if not _REFRESH["callbacks"]:
        try:
            from pyplugin_installer.installer_data import repositories

            repositories.checkingDone.disconnect(_on_repository_checked)
        except Exception:  # noqa: BLE001
            pass  # nosec B110


# --- the action ------------------------------------------------------------------


def open_update(key: str | None = None) -> str:
    """Take the user to the update. Returns the action for the telemetry.

    Listed by the installer: the Plugin Manager on its Upgradeable tab, on
    this plugin's row (``plugin_manager``). Not listed: the marketplace page,
    the only place the click can still lead somewhere (``marketplace_page``).
    """
    from ..core.auth.activation_manager import MARKETPLACE_URL, served_marketplace_url

    try:
        url = served_marketplace_url() or MARKETPLACE_URL
    except Exception:  # noqa: BLE001 - a bad served value keeps the shipped page
        url = MARKETPLACE_URL
    if not installer_upgradeable_version(key or plugin_installer_key()):
        from .external_url import open_external

        open_external(url)
        return "marketplace_page"
    from .terralab_menu import open_plugin_manager_updates

    return open_plugin_manager_updates(url)


# --- the card ------------------------------------------------------------------

_CARD_MAX_W = 380
_LOGO_PX = 44
# The badge is a pill: Qt drops a radius larger than half the height and paints
# square corners, so the height is fixed and the radius is exactly half of it.
_BADGE_PX = 22
# Amber, the warning hue (ui.md): blue is kept for focus and links, and a
# required update is a warning, not a link.
_BADGE_QSS = (
    f"QFrame#updateGateBadge {{ background: {category_tint('amber')};"
    f" border: 1px solid {category_line('amber')};"
    f" border-radius: {_BADGE_PX // 2}px; }}"
)

_QSS = (
    f"QFrame#updateGateCard {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_BOX}px; }}"
    f"QLabel#updateGateBadgeText {{ font-size: {FONT_HINT}px; font-weight: 600;"
    f" color: {category_ink('amber')}; background: transparent; border: none; }}"
    f"QLabel#updateGateTitle {{ font-size: {FONT_BASE + 3}px; font-weight: 600; color: {INK};"
    " background: transparent; border: none; }"
)


class UpdateGateCard(QWidget):
    """The required-update card, centred in the dock in place of its body.

    ``offer(version, note, installed)`` fills and shows it. The button opens
    the update itself and emits ``update_clicked(version, action)`` so the dock
    can track it. QGIS reloads the plugin once the update lands, so the card
    goes away by itself.
    """

    update_clicked = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.version = ""
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setStyleSheet(_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 24, 16, 24)
        outer.setSpacing(0)
        outer.addStretch(1)

        self._card = QFrame(self)
        self._card.setObjectName("updateGateCard")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # A fixed width, kept in step by resizeEvent: a wrapped label inside a
        # card that only has a maximum reports one long line and clips.
        self._card.setFixedWidth(_CARD_MAX_W)
        col = QVBoxLayout(self._card)
        col.setContentsMargins(22, 22, 22, 22)
        col.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(12)
        mark = QLabel(self._card)
        mark.setFixedSize(_LOGO_PX, _LOGO_PX)
        mark.setPixmap(logo_pixmap(mark, _LOGO_PX))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        badge = QFrame(self._card)
        badge.setObjectName("updateGateBadge")
        badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        badge.setStyleSheet(_BADGE_QSS)
        badge.setFixedHeight(_BADGE_PX)
        badge_row = QHBoxLayout(badge)
        badge_row.setContentsMargins(10, 0, 10, 0)
        badge_text = QLabel(get_export_copy("widgets.update_gate.badge", tr("Update required")), badge)
        badge_text.setObjectName("updateGateBadgeText")
        badge_row.addWidget(badge_text)
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        col.addLayout(head)

        self._title = QLabel("", self._card)
        self._title.setObjectName("updateGateTitle")
        self._title.setWordWrap(True)
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(self._title)

        # The served line about the release comes first: it is the reason to
        # press the button.
        self._note = QLabel("", self._card)
        self._note.setStyleSheet(BODY_QSS)
        self._note.setWordWrap(True)
        self._note.setTextFormat(Qt.TextFormat.PlainText)
        self._note.hide()
        col.addWidget(self._note)

        body = QLabel(get_export_copy(
            "widgets.update_gate.body",
            tr("Update to keep using AI Edit. It takes one click in the QGIS Plugin Manager, "
               "and the plugin reloads on its own.")), self._card)
        body.setStyleSheet(HINT_QSS)
        body.setWordWrap(True)
        col.addWidget(body)

        col.addSpacing(4)
        self._button = QPushButton(get_export_copy("widgets.update_gate.update_now", tr("Update now")),
                                   self._card)
        self._button.setObjectName("updateGateButton")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setStyleSheet(BTN_PRIMARY_WIDE_QSS)
        self._button.setFixedHeight(BTN_PRIMARY_WIDE_PX)
        self._button.setAutoDefault(False)
        # Tab still reaches it; showing the card must not hand it the focus
        # ring, which read as a second, white outline around the pill.
        self._button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._button.clicked.connect(self._on_update)
        col.addWidget(self._button)

        self._hint = QLabel("", self._card)
        self._hint.setStyleSheet(HINT_QSS)
        self._hint.setWordWrap(True)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self._hint)

        outer.addWidget(self._card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)
        self.hide()

    def offer(self, version: str, note: str = "", installed: str = "") -> None:
        """Require one version, with the served line about what it brings."""
        self.version = str(version or "")
        self._title.setText(tr("AI Edit {version} is out").format(version=self.version))
        self._note.setText(str(note or ""))
        self._note.setVisible(bool(note))
        self._hint.setText(tr("You have {installed}.").format(installed=installed) if installed else "")
        self._hint.setVisible(bool(installed))
        self.setVisible(bool(self.version))
        if self.version and self._button.hasFocus():
            self._button.clearFocus()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        margins = self.layout().contentsMargins()
        room = max(200, event.size().width() - margins.left() - margins.right())
        self._card.setFixedWidth(min(_CARD_MAX_W, room))

    def _on_update(self) -> None:
        action = open_update()
        self.update_clicked.emit(self.version, action)
