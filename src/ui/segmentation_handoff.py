








from __future__ import annotations

from ..core.config_store import get_export_copy
from ..core.i18n import tr
from .cross_plugin_discovery import (
    STATE_ENABLE,
    STATE_INSTALL,
    STATE_OPEN,
    STATE_RESTART,
    STATE_UPDATE,
    run_sibling_action,
    sibling_state,
)

PRODUCT_ID = "ai-segmentation"


def current_state() -> str:
    try:
        return sibling_state(PRODUCT_ID)
    except Exception:  # noqa: BLE001
        return STATE_INSTALL


def is_installed(state: str | None = None) -> bool:
    return (state or current_state()) != STATE_INSTALL


def sentence(kind: str, state: str | None = None) -> str:

    state = state or current_state()
    if kind == "objects":
        if state == STATE_INSTALL:
            return get_export_copy("handoff.objects_not_installed", tr(
                "Real outlines are traced in AI Segmentation, our free plugin "
                "for mapping what is already there."))
        return get_export_copy("handoff.objects_installed", tr(
            "To trace real outlines, use AI Segmentation."))
    if state == STATE_INSTALL:
        return get_export_copy("handoff.land_cover_not_installed", tr(
            "Classes and land cover are done in AI Segmentation, our free plugin "
            "for mapping what is already there."))
    return get_export_copy("handoff.land_cover_installed", tr(
        "Classes and land cover are in AI Segmentation."))


def action_label(state: str | None = None) -> str:

    state = state or current_state()
    if state == STATE_OPEN:
        return get_export_copy("handoff.action_open", tr("Open it with this zone"))
    if state == STATE_UPDATE:
        return get_export_copy("handoff.action_update", tr("Update it"))
    if state == STATE_ENABLE:
        return get_export_copy("handoff.action_enable", tr("Enable it"))
    if state == STATE_RESTART:
        return ""
    return get_export_copy("handoff.action_install", tr("Install it"))


def restart_note() -> str:
    return get_export_copy("handoff.restart", tr("Restart QGIS to use it."))


def rich_line(kind: str) -> str:

    state = current_state()
    text = sentence(kind, state)
    label = action_label(state)
    if not label:
        return f"{text} {restart_note()}"
    return f"{text} <a href='ai_seg'>{label}</a>"


def card_hint() -> str:

    if is_installed():
        return get_export_copy("dialogs.cards.handoff_hint", tr("Opens in AI Segmentation"))
    return get_export_copy(
        "dialogs.cards.handoff_hint_not_installed", tr("Needs AI Segmentation (free)"))


def run() -> str:

    return run_sibling_action(PRODUCT_ID)
