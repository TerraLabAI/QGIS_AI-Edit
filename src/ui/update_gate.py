




















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
from .dock.style import _PLUGIN_ROOT
from .icons import logo_pixmap

POLICY_REQUIRE = "require"
POLICY_RECOMMEND = "recommend"
DEFAULT_POLICY = POLICY_RECOMMEND

STATE_NONE = "none"
STATE_OFFER = "offer"
STATE_REFRESH = "refresh"
STATE_FLOOR = "floor_blocked"





def read_update_version(text) -> tuple | None:






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

    left, right = read_update_version(candidate), read_update_version(installed)
    if left is None or right is None:
        return False
    return left > right


def installed_plugin_version() -> str:

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

    return os.path.basename(_PLUGIN_ROOT)





def _served(key: str) -> str:
    try:
        from ..core.auth.activation_manager import get_server_config

        value = get_server_config().get(key)
    except Exception:  # noqa: BLE001
        return ""
    return value.strip() if isinstance(value, str) else ""


def served_update_policy() -> str:

    value = _served("update_policy").lower()
    return value if value in (POLICY_REQUIRE, POLICY_RECOMMEND) else DEFAULT_POLICY


def served_update_note() -> str:

    try:
        from ..core.auth.activation_manager import served_release_notes_line, served_update_message

        return served_release_notes_line() or served_update_message() or ""
    except Exception:  # noqa: BLE001
        return ""





def evaluate_update(key: str | None = None) -> dict:










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






_LATER_VERSIONS: set = set()


def dismiss_update_for_session(version: str) -> None:

    if version:
        _LATER_VERSIONS.add(str(version))


def is_update_dismissed_for_session(version: str) -> bool:


    return bool(version) and str(version) in _LATER_VERSIONS




_REFRESH = {"requested": False, "callbacks": []}


def request_repository_refresh(on_done) -> str:









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

    try:
        from pyplugin_installer.installer_data import plugins, repositories

        try:
            repositories.checkingDone.disconnect(_on_repository_checked)
        except (TypeError, RuntimeError):
            pass
        plugins.rebuild()
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    callbacks, _REFRESH["callbacks"] = _REFRESH["callbacks"], []
    for callback in callbacks:
        try:
            callback()
        except Exception:  # noqa: BLE001
            continue  # nosec B112


def disconnect_repository_refresh(on_done=None) -> None:




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





def open_update(key: str | None = None) -> str:






    from ..core.auth.activation_manager import MARKETPLACE_URL, served_marketplace_url

    try:
        url = served_marketplace_url() or MARKETPLACE_URL
    except Exception:  # noqa: BLE001
        url = MARKETPLACE_URL
    if not installer_upgradeable_version(key or plugin_installer_key()):
        from .external_url import open_external

        open_external(url)
        return "marketplace_page"
    from .terralab_menu import open_plugin_manager_updates

    return open_plugin_manager_updates(url)




_CARD_MAX_W = 380
_LOGO_PX = 44


_BADGE_PX = 22


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

        self.version = str(version or "")
        self._title.setText(tr("AI Edit {version} is out").format(version=self.version))
        self._note.setText(str(note or ""))
        self._note.setVisible(bool(note))
        self._hint.setText(tr("You have {installed}.").format(installed=installed) if installed else "")
        self._hint.setVisible(bool(installed))
        self.setVisible(bool(self.version))
        if self.version and self._button.hasFocus():
            self._button.clearFocus()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        margins = self.layout().contentsMargins()
        room = max(200, event.size().width() - margins.left() - margins.right())
        self._card.setFixedWidth(min(_CARD_MAX_W, room))

    def _on_update(self) -> None:
        action = open_update()
        self.update_clicked.emit(self.version, action)
