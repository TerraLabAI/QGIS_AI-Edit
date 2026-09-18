"""The update offer on the dock: a soft card with Later, or the required gate.

Moved out of ``chrome.py`` (2026-09-17) and put on AI Agent's rule
(``src/ui/update_gate.py`` holds the decision): the offer is made only for a
version QGIS's installer lists as upgradeable; the server says whether it is
a recommendation (this card, closable per version) or a requirement (the
gate card replaces the dock body until the update lands and QGIS reloads the
plugin). A served version QGIS has not listed yet asks for one quiet
repository refresh, then looks again.

The telemetry is the dock's: ``plugin_update_prompt_shown`` once per version
per install, ``_clicked`` with the action (Later closes a version for the QGIS
session only), ``_suppressed`` once per served version per session, as in
AI Agent and AI Segmentation.
"""
from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ..icons import logo_pixmap, logo_size
from .design_tokens import (
    BTN_PRIMARY_WIDE_PX,
    BTN_PRIMARY_WIDE_QSS,
    BTN_QUIET_QSS,
    CARD_QSS,
    HINT_QSS,
    SPACE_CARD,
    TITLE_QSS,
)

# The version last reported as shown, so the event fires once per version
# per install rather than once per session.
_UPDATE_ANNOUNCED_KEY = "AIEdit/update_notice/announced_version"
_MARK_PX = 28


class DockUpdateBannerMixin:
    """Update card, required-update gate and their telemetry for AIEditDockWidget."""

    def _setup_update_notification(self, parent_layout: QVBoxLayout) -> None:
        """Build the soft update card, hidden until check_for_updates finds one.

        A card at the top of the dock, in every state but a running
        generation: most users never open the Plugin Manager, so this is how
        they learn a newer version exists.
        """
        self._update_notif_container = QWidget()
        self._update_notif_container.setObjectName("card")
        self._update_notif_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._update_notif_container.setStyleSheet(CARD_QSS)
        card = QVBoxLayout(self._update_notif_container)
        card.setContentsMargins(14, 12, 14, 10)
        card.setSpacing(SPACE_CARD + 2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        mark = QLabel()
        mark.setFixedSize(logo_size(_MARK_PX))
        mark.setPixmap(logo_pixmap(mark, _MARK_PX))
        head.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        self._update_title_label = QLabel("")
        self._update_title_label.setWordWrap(True)
        self._update_title_label.setTextFormat(Qt.TextFormat.PlainText)
        self._update_title_label.setStyleSheet(TITLE_QSS)
        words.addWidget(self._update_title_label)
        # One line, the served release note: the reason to press the button.
        self._update_notification_label = QLabel("")
        self._update_notification_label.setWordWrap(True)
        self._update_notification_label.setTextFormat(Qt.TextFormat.PlainText)
        self._update_notification_label.setStyleSheet(HINT_QSS)
        words.addWidget(self._update_notification_label)
        head.addLayout(words, 1)
        card.addLayout(head)

        self._update_now_btn = QPushButton(get_export_copy("dock.chrome.update_now_btn", tr("Update now")))
        self._update_now_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_now_btn.setStyleSheet(BTN_PRIMARY_WIDE_QSS)
        self._update_now_btn.setFixedHeight(BTN_PRIMARY_WIDE_PX)
        self._update_now_btn.setAutoDefault(False)
        self._update_now_btn.clicked.connect(self._on_open_plugin_manager)
        card.addWidget(self._update_now_btn)

        self._offered_update_version = ""
        self._update_banner_armed = False
        self._update_required = False
        later_btn = QPushButton(get_export_copy("dock.chrome.later_btn", tr("Later")))
        later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        later_btn.setStyleSheet(BTN_QUIET_QSS)
        later_btn.setAutoDefault(False)
        later_btn.clicked.connect(self._on_dismiss_update_banner)
        card.addWidget(later_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        self._update_notif_container.setVisible(False)
        parent_layout.addWidget(self._update_notif_container)

    def _build_update_gate(self) -> QWidget:
        """The required-update card, a sibling of the scroll area (see
        ``_wrap_in_scroll_area``). None of the dock body shows while it does."""
        from ..update_gate import UpdateGateCard

        self._update_gate = UpdateGateCard()
        self._update_gate.update_clicked.connect(self._on_update_gate_clicked)
        self._update_gate.setVisible(False)
        return self._update_gate

    def sync_update_banner(self) -> None:
        """Show the pending update, except while a generation is running.

        A run owns the panel and its progress; neither card may offer to swap
        the plugin out mid-flight. A required update waits for the run too.
        """
        container = getattr(self, "_update_notif_container", None)
        if container is None:
            return
        busy = False
        try:
            busy = bool(self._progress_widget.isVisible())
        except (RuntimeError, AttributeError):  # progress widget deleted or not built: treat as idle
            pass
        armed = bool(self._update_banner_armed)
        required = armed and bool(getattr(self, "_update_required", False))
        container.setVisible(armed and not required and not busy)
        self._set_update_gate_shown(required and not busy)

    def _set_update_gate_shown(self, shown: bool) -> None:
        gate = getattr(self, "_update_gate", None)
        body = getattr(self, "_scroll_area", None)
        if gate is None or body is None:
            return
        try:
            gate.setVisible(shown)
            body.setVisible(not shown)
            bar = getattr(self, "_tool_bar", None)
            if bar is not None and shown:
                bar.setVisible(False)
            elif bar is not None:
                chips = (self._vectorize_btn, self._swipe_btn)
                bar.setVisible(any(not chip.isHidden() for chip in chips))
        except RuntimeError:
            pass  # the dock is being torn down

    def check_for_updates(self) -> bool:
        """Offer the update only when QGIS can actually install it.

        The installer's own verdict decides (status "upgradeable"); the
        served version is only the hint that a repository refresh is worth
        making. Returns True once an offer is up, so the caller stops polling.
        Safe to call again: the startup poll and the served-config arrival do,
        and a served answer that no longer asks for an update takes the card
        (or the gate) down, as in AI Agent.
        """
        from ...core.logger import log_warning
        from ..update_gate import STATE_FLOOR, STATE_NONE, STATE_OFFER, STATE_REFRESH, evaluate_update

        verdict = evaluate_update(self._plugin_installer_key())
        state = verdict.get("state")
        if state == STATE_OFFER:
            self._show_update_banner(verdict, "served_latest_version")
            return True
        if state in (STATE_NONE, STATE_FLOOR):
            self._clear_update_offer()
        if state == STATE_FLOOR:
            log_warning(
                f"The plugin repository only offers {verdict.get('version')}, below the "
                "version the service requires; no update is offered."
            )
        elif state == STATE_REFRESH:
            self._maybe_refresh_plugin_repository(str(verdict.get("served") or ""))
        return False

    def _show_update_banner(self, verdict: dict, trigger: str) -> None:
        """Offer one version, with the served line about what it brings.

        A recommended update the user already closed stays closed; a required
        one does not ask.
        """
        version = str(verdict.get("version") or "")
        required = bool(verdict.get("required"))
        if not version or (not required and self._is_update_dismissed(version)):
            # A gate left from an earlier required answer must not outlive it.
            self._clear_update_offer()
            return
        note = str(verdict.get("note") or "")
        self._update_title_label.setText(tr("AI Edit {version} is out").format(version=version))
        self._update_notification_label.setText(note)
        self._update_notification_label.setVisible(bool(note))
        self._offered_update_version = version
        self._update_required = required
        gate = getattr(self, "_update_gate", None)
        if required and gate is not None:
            gate.offer(version, note, str(verdict.get("installed") or ""))
        self._update_banner_armed = True
        self.sync_update_banner()
        self._track_update_prompt_shown(version, trigger)

    def _clear_update_offer(self) -> None:
        """Nothing to offer any more: the card and the gate go, the body comes back."""
        if not getattr(self, "_update_banner_armed", False):
            return
        self._update_banner_armed = False
        self._update_required = False
        self.sync_update_banner()

    def _plugin_installer_key(self) -> str:
        """The key pyplugin_installer files this install under: the folder
        name. A dev clone whose folder differs from the published id is never
        listed, which is why a dev install sees no offer."""
        return os.path.basename(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        )

    def _maybe_refresh_plugin_repository(self, served: str) -> None:
        """One quiet repository refresh per dock session, when the server says
        a newer version exists and QGIS has not caught up."""
        if getattr(self, "_update_refresh_requested", False) or not served:
            return
        self._update_refresh_requested = True
        from ..update_gate import request_repository_refresh

        reason = request_repository_refresh(self._on_plugin_repository_checked)
        if reason:
            # Another dock of this QGIS session already asked: the list is as
            # fresh as it gets, and the version is still not in it.
            self._track_update_prompt_suppressed(
                served, "not_listed" if reason == "already_requested" else reason
            )

    def _on_plugin_repository_checked(self) -> None:
        """The repository answered: look again."""
        from ..update_gate import STATE_OFFER, evaluate_update

        try:
            verdict = evaluate_update(self._plugin_installer_key())
        except RuntimeError:
            return  # the dock died while QGIS was fetching
        if verdict.get("state") == STATE_OFFER:
            self._show_update_banner(verdict, "served_latest_version")
        elif verdict.get("served"):
            self._track_update_prompt_suppressed(str(verdict.get("served")), "not_listed")

    def disconnect_update_refresh(self) -> None:
        """Unload: a repository answer must not reach a dead dock."""
        try:
            from ..update_gate import disconnect_repository_refresh

            disconnect_repository_refresh(self._on_plugin_repository_checked)
        except Exception:  # noqa: BLE001
            pass  # nosec B110 - unload never fails on this

    def _track_update_prompt_suppressed(self, served_version: str, reason: str) -> None:
        """Report an offer we chose not to make, once per served version per session."""
        seen = getattr(self, "_update_suppressed_reported", None)
        if seen is None:
            seen = set()
            self._update_suppressed_reported = seen
        if not served_version or served_version in seen:
            return
        seen.add(served_version)
        try:
            from ...core import telemetry
            from ...core import telemetry_events as te

            telemetry.track(
                te.PLUGIN_UPDATE_PROMPT_SUPPRESSED,
                {"served_version": served_version, "reason": reason},
            )
        except Exception:
            pass  # nosec B110  Telemetry must never break the dock.

    @staticmethod
    def _is_update_dismissed(version: str) -> bool:
        """Later hides a version for this QGIS session only (AI Segmentation's
        rule): a notice closed for good is a fix nobody installs."""
        from ..update_gate import is_update_dismissed_for_session

        return is_update_dismissed_for_session(version)

    def _on_dismiss_update_banner(self) -> None:
        from ..update_gate import dismiss_update_for_session

        version = getattr(self, "_offered_update_version", "")
        self._update_banner_armed = False
        self._update_notif_container.setVisible(False)
        dismiss_update_for_session(version)
        self._track_update_prompt_clicked(version, "dismissed")

    def _track_update_prompt_shown(self, version: str, trigger: str) -> None:
        """Once per version per install, not once per session."""
        try:
            from qgis.PyQt.QtCore import QSettings

            settings = QSettings()
            if str(settings.value(_UPDATE_ANNOUNCED_KEY, "", type=str)) == version:
                return
            settings.setValue(_UPDATE_ANNOUNCED_KEY, version)
        except Exception:
            pass  # nosec B110  Settings unreadable: still report it once.
        self._track_update_event(True, version, trigger)

    def _track_update_prompt_clicked(self, version: str, action: str) -> None:
        self._track_update_event(False, version, action)

    @staticmethod
    def _track_update_event(shown: bool, version: str, kind: str) -> None:
        try:
            from ...core import telemetry
            from ...core import telemetry_events as te

            if shown:
                telemetry.track(te.PLUGIN_UPDATE_PROMPT_SHOWN, {
                    "offered_version": version, "trigger": kind,
                })
            else:
                telemetry.track(te.PLUGIN_UPDATE_PROMPT_CLICKED, {
                    "offered_version": version, "action": kind,
                })
        except Exception:
            pass  # nosec B110  Telemetry never breaks the dock.

    @staticmethod
    def _installed_plugin_version() -> str:
        """This plugin's metadata version ('' when unreadable)."""
        from ..update_gate import installed_plugin_version

        return installed_plugin_version()

    def _on_open_plugin_manager(self, _link: str = "") -> None:
        """Plugin Manager on the Upgradeable tab, or the marketplace page when
        QGIS does not list the version. Same doors as the TerraLab menu."""
        action = "plugin_manager"
        try:
            from ..update_gate import open_update

            action = open_update(self._plugin_installer_key())
        except Exception:
            pass  # nosec B110
        self._track_update_prompt_clicked(getattr(self, "_offered_update_version", ""), action)

    def _on_update_gate_clicked(self, version: str, action: str) -> None:
        self._track_update_prompt_clicked(version, action)
