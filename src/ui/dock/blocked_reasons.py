"""Why a primary button is disabled, said on the panel.

Launch used to hide itself when a precondition was missing, and Generate went
grey behind a generic tooltip. Both now carry one short muted line naming the
blocker, and each reason is reported once per dock session so the funnel shows
where people stop.

The reason strings are the enums of the ``launch_blocked`` and
``generate_blocked`` events; the labels are translated at read time so a
language change reaches them without a rebuild.
"""
from __future__ import annotations

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from .design_tokens import HINT_QSS, INK_2

# Private names are listed too: other modules import them from here.
__all__ = [
    "_BLOCK_REASON_QSS",
    "BLOCK_REASON_INK",
    "DockBlockedReasonsMixin",
    "GENERATE_BLOCK_NO_ZONE",
    "GENERATE_BLOCK_PROMPT_EMPTY",
    "GENERATE_BLOCK_PROMPT_TOO_SHORT",
    "generate_block_text",
    "LAUNCH_BLOCK_NO_KEY",
    "LAUNCH_BLOCK_NO_RASTER",
    "launch_block_text",
    "LAUNCH_BLOCK_TILES_WARMING",
    "LAUNCH_BLOCK_WORKER_BUSY",
]

# Fallbacks for the server-tunable prompt-guard dials, mirrored from
# DockPromptMixin so the reason line quotes the minimum actually in force.
_MIN_PROMPT_CHARS = 10
_MIN_PROMPT_WORDS = 2

# launch_blocked reasons.
LAUNCH_BLOCK_NO_RASTER = "no_raster"
LAUNCH_BLOCK_NO_KEY = "no_key"
LAUNCH_BLOCK_TILES_WARMING = "tiles_warming"
LAUNCH_BLOCK_WORKER_BUSY = "worker_busy"

# generate_blocked reasons.
GENERATE_BLOCK_NO_ZONE = "no_zone"
GENERATE_BLOCK_PROMPT_EMPTY = "prompt_empty"
GENERATE_BLOCK_PROMPT_TOO_SHORT = "prompt_too_short"

# The reason line under a greyed primary: the hint role, second ink.
BLOCK_REASON_INK = INK_2
_BLOCK_REASON_QSS = HINT_QSS


def launch_block_text(reason: str) -> str:
    """One short line for a launch_blocked reason. Empty for an unknown one."""
    if reason == LAUNCH_BLOCK_NO_RASTER:
        return get_export_copy("dock.blocked_reasons.no_raster", tr("Add imagery first"))
    if reason == LAUNCH_BLOCK_NO_KEY:
        return get_export_copy("dock.blocked_reasons.no_key", tr("Checking your account..."))
    if reason == LAUNCH_BLOCK_TILES_WARMING:
        return get_export_copy("dock.blocked_reasons.tiles_warming", tr("Loading imagery..."))
    if reason == LAUNCH_BLOCK_WORKER_BUSY:
        return get_export_copy("dock.blocked_reasons.worker_busy", tr("An edit is already running"))
    return ""


def generate_block_text(reason: str) -> str:
    """One short line for a generate_blocked reason. Empty for an unknown one."""
    if reason == GENERATE_BLOCK_NO_ZONE:
        return get_export_copy("dock.blocked_reasons.no_zone", tr("Draw a zone on the map first"))
    if reason == GENERATE_BLOCK_PROMPT_EMPTY:
        # Nothing: an empty prompt box is already asking for the sentence, in
        # its placeholder, right above the button. A line repeating the ask
        # (Yvann, 2026-09-17) read as a complaint about a box the user had not
        # reached yet. The reason still reaches the generate_blocked funnel,
        # which is what the enum is for.
        return ""
    if reason == GENERATE_BLOCK_PROMPT_TOO_SHORT:
        return tr(
            "Say a bit more: at least {chars} characters and {words} words."
        ).format(
            chars=get_export_dial("limits.min_prompt_chars", _MIN_PROMPT_CHARS),
            words=get_export_dial("limits.min_prompt_words", _MIN_PROMPT_WORDS),
        )
    return ""


class DockBlockedReasonsMixin:
    """Writes the blocking reason under a primary button, and reports it.

    The setter takes ``None`` for "nothing blocks it", which hides the line.
    """

    def _track_block_reason(self, event: str, reason: str) -> None:
        """Send one blocked event per reason per dock session."""
        seen = getattr(self, "_block_reasons_emitted", None)
        if seen is None:
            seen = set()
            self._block_reasons_emitted = seen
        key = event + ":" + reason
        if key in seen:
            return
        seen.add(key)
        telemetry.track(event, {"reason": reason})

    def is_generation_in_flight(self) -> bool:
        """True while a run owns the panel. Read by the launch shortcut, which
        can fire from anywhere and would otherwise reset a running edit."""
        try:
            return bool(self._progress_widget.isVisible())
        except (RuntimeError, AttributeError):
            return False

    def reset_block_reason_memory(self) -> None:
        """New dock session: every reason may be reported once more."""
        self._block_reasons_emitted = set()

    def set_launch_block_reason(
        self, reason: str | None, has_own_card: bool = False
    ) -> None:
        """Keep Launch on screen, enabled only when nothing blocks it.

        ``has_own_card`` is for the one blocker that already owns the screen:
        the empty-canvas card says what is missing and carries the two ways
        out, so a greyed Launch above it repeats the card and offers a third.
        The whole row hides there, and the reason is still reported.
        """
        label = getattr(self, "_launch_reason_label", None)
        self._launch_btn.setVisible(not has_own_card)
        self._launch_btn.setEnabled(reason is None)
        if label is not None:
            text = "" if has_own_card else launch_block_text(reason or "")
            label.setText(text)
            label.setVisible(bool(text))
        if reason:
            self._track_block_reason(te.LAUNCH_BLOCKED, reason)

    def set_generate_block_reason(self, reason: str | None) -> None:
        """Write the live reason under Generate, or clear it with ``None``.

        The line belongs to the button: with Generate hidden (the entry screen,
        a result, a run in flight) it says nothing, whatever the state machine
        recomputes in the background.
        """
        label = getattr(self, "_generate_reason_label", None)
        if label is not None:
            # isHidden, not isVisible: the button's own flag. isVisible is
            # False whenever an ancestor is hidden (a tool panel over the
            # prompt, the dock itself closed), and the line then stayed blank
            # after the ancestor came back, under a grey Generate.
            shown = reason and not self._generate_btn.isHidden()
            text = generate_block_text(reason) if shown else ""
            label.setText(text)
            label.setVisible(bool(text))
        if reason:
            self._track_block_reason(te.GENERATE_BLOCKED, reason)
