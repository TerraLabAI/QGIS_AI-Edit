











from __future__ import annotations

from datetime import date

from qgis.PyQt.QtCore import QSettings, QSize, Qt
from qgis.PyQt.QtWidgets import QPushButton, QWidget

from ...core.auth.activation_manager import get_subscribe_url
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ..icons import icon_for
from . import design_tokens as tokens


PRO_LINK_HREF = "terralab:see-pro"

_AFTER_SUCCESS_KEY = "AI_Edit/pro_nudge_after_success_month"

_PILL_PX = 24
_PILL_GLYPH_PX = 14
_PILL_QSS = (
    "QPushButton#proPill {"
    f" background: transparent; color: {tokens.INK};"
    f" border: 1px solid {tokens.LINE_STRONG}; border-radius: {_PILL_PX // 2}px;"
    f" padding: 0 10px 0 8px; min-height: {_PILL_PX - 2}px; max-height: {_PILL_PX}px;"
    f" font-size: {tokens.FONT_BODY}px; font-weight: 600; }}"
    f"QPushButton#proPill:hover {{ background: {tokens.HOVER}; }}"
    f"QPushButton#proPill:pressed {{ background: {tokens.HOVER_ON}; }}"
    f"QPushButton#proPill:focus {{ border: 2px solid {tokens.ACCENT_BORDER}; padding: 0 9px 0 7px; }}"
)


def build_pro_pill(parent: QWidget) -> QPushButton:


    pill = QPushButton(get_export_copy("nudge.header_pill", tr("Get Pro")), parent)
    pill.setObjectName("proPill")
    pill.setStyleSheet(_PILL_QSS)

    pill.setIcon(icon_for(pill, "gem", _PILL_GLYPH_PX, tokens.qcolor(tokens.category_ink("amber"))))
    pill.setIconSize(QSize(_PILL_GLYPH_PX, _PILL_GLYPH_PX))
    pill.setFixedHeight(_PILL_PX)
    pill.setCursor(Qt.CursorShape.PointingHandCursor)
    pill.setAutoDefault(False)
    pill.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    tip = get_export_copy("nudge.header_pill_tooltip", tr("See what Pro unlocks"))
    pill.setToolTip(tip)
    pill.setAccessibleName(tip)
    pill.setVisible(False)
    return pill


def _this_month() -> str:
    return date.today().strftime("%Y-%m")


class DockProNudgesMixin:


    def _track_upsell_view(self, surface: str) -> None:

        sent = getattr(self, "_upsell_views_sent", None)
        if sent is None:
            sent = self._upsell_views_sent = set()
        if surface in sent:
            return
        sent.add(surface)
        from ...core import telemetry
        from ...core import telemetry_events as te

        telemetry.track(te.PRO_UPSELL_VIEWED, {"trigger": surface, "cta_source": surface})

    def _open_pro_from(self, surface: str) -> None:

        self._open_plans(surface, f"plugin_{surface}", get_subscribe_url())



    def _sync_pro_pill(self) -> None:
        pill = getattr(self, "_pro_pill", None)
        if pill is None:
            return


        show = (
            bool(getattr(self, "_activated", False))
            and bool(getattr(self, "_is_free_tier", False))
            and bool(getattr(self, "_tier_confirmed", False))
        )
        pill.setVisible(show)
        fit = getattr(pill.parentWidget(), "fit_to_width", None)
        if fit is not None:
            fit()
        if show:
            self._track_upsell_view("header_pill")

    def _on_pro_pill_clicked(self) -> None:
        self._open_pro_from("header_pill")



    def _maybe_show_after_success(self) -> None:


        label = getattr(self, "_after_success_label", None)
        if label is None or not getattr(self, "_is_free_tier", False):
            return




        if not getattr(self, "_tier_confirmed", False):
            return
        if self._is_free_tier_exhausted():
            return
        settings = QSettings()
        month = _this_month()
        if str(settings.value(_AFTER_SUCCESS_KEY, "") or "") == month:
            return
        settings.setValue(_AFTER_SUCCESS_KEY, month)

        lead = get_export_copy(
            "nudge.after_success_v2",
            tr("Like it? Pro adds Detailed and Maximum quality, and commercial use."),
            escape=True,
        )
        link = get_export_copy("nudge.after_success_link", tr("See Pro"), escape=True)
        label.setText(
            f'{lead} <a href="{PRO_LINK_HREF}" style="color: {tokens.LINK_INK};'
            f' text-decoration: none; font-weight: 600;">{link}</a>'
        )
        label.setVisible(True)
        self._track_upsell_view("after_success")

    def _hide_after_success(self) -> None:
        label = getattr(self, "_after_success_label", None)
        if label is not None:
            label.setVisible(False)

    def _on_after_success_link(self, href: str) -> None:
        if href == PRO_LINK_HREF:
            self._hide_after_success()
            self._open_pro_from("after_success")
