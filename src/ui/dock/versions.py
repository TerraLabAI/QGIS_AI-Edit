from __future__ import annotations

from qgis.PyQt.QtCore import QTimer

from ...core.auth.activation_manager import get_subscribe_url
from ...core.config_store import get_export_copy
from ...core.entitlements import coerce_tier, is_tier_allowed
from ...core.i18n import tr
from ..reference_images_widget import free_tier_max_references
from .style import BRAND_BLUE


def _reference_cap_message(cap: int) -> str:
    """The free-plan reference-image nudge, in the right number.

    The cap is a dial and it ships at 3, so the singular sentence this used to
    show was already wrong ("limited to 3 reference image"). Both readings are
    sentences the plugin already ships, so both stay translated, and a served
    entry can replace either."""
    if cap == 1:
        shipped = tr("Free plan is limited to {n} reference image.").format(n=cap)
    else:
        shipped = tr("Maximum {n} reference images reached").format(n=cap)
    return get_export_copy("upsell.reference_cap", shipped, escape=True)


class DockVersionsMixin:
    """Version strip lineage, resolution pickers, and reference-image
    restore hooks for AIEditDockWidget."""

    def seed_version_strip(self, original_pixmap, prompt: str = "", meta: dict | None = None) -> None:
        """Seed the strip with the Original tile (selected). Called once per
        lineage when the clean base capture becomes available."""
        self._version_strip.reset(original_pixmap, prompt, meta)
        self._update_result_generate_label()

    def add_version_thumb(self, pixmap, prompt: str = "", meta: dict | None = None) -> int:
        """Append a generated version to the strip and auto-select it."""
        index = self._version_strip.add_version(pixmap, prompt, meta)
        self._update_result_generate_label()
        return index

    def reset_version_strip(self) -> None:
        """Clear and hide the strip (new zone breaks the lineage)."""
        self._version_strip.clear()

    def select_version(self, index: int) -> None:
        """Move the strip's selection ring without emitting version_selected."""
        self._version_strip.set_selected(index)

    def set_version_strip_locked(self, locked: bool) -> None:
        """Lock the strip while a restored version's layer downloads, so two
        materializations can never race. Generation runs lock it through
        set_generating, which owns the readonly state while active."""
        self._version_strip.set_readonly(locked)

    def set_result_prompt_text(self, text: str) -> None:
        """Mirror the selected version's prompt into the iterate box, so the
        user edits from what actually produced that version (Original clears
        the box, it has no prompt)."""
        self._result_prompt_input.blockSignals(True)
        self._result_prompt_input.setPlainText(text or "")
        self._result_prompt_input.blockSignals(False)
        # Blocked signals skip the star's textChanged debounce: re-sync it so
        # a mirrored version prompt shows its star like a typed one.
        self._result_prompt_container.refresh_favorite_star()

    def reveal_version_strip(self) -> None:
        """Keep the restored lineage in its iterate home (above the Generate
        row). Restoring already entered the iterate state; this just re-asserts
        the strip's placement once its thumbnails arrive."""
        self._place_version_strip("result")

    def get_cached_recent_jobs(self) -> list:
        """Session-cached past generations (newest first). Used to rebuild the
        iteration chain when the user reuses a generation from Recent."""
        return list(self._library_recent_cache or [])

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _refresh_resolution_triggers(self):
        """Push the current selection / costs / tier into both prompt containers.

        Also coerces the selection back to what the plan allows once a
        free-tier user is confirmed (downgrading the paid default that
        set_credits applies), so the Generate button never quotes a price the
        user can't actually pay. Same helper the submit path uses.
        """
        self._selected_resolution = coerce_tier(
            self._selected_resolution, self._is_free_tier
        )
        for container in (self._prompt_container, self._result_prompt_container):
            container.set_resolution_state(
                self._selected_resolution,
                self._resolution_credit_costs,
                self._is_free_tier,
            )

    def _show_subscribe_banner(self, message: str) -> None:
        """Show a 12 s warning banner with a Subscribe link appended.

        Shared by the free-tier resolution gate and the reference-image gate so
        the upsell copy/styling stays in one place.
        """
        subscribe_url = get_subscribe_url()
        link_style = f"color: {BRAND_BLUE}; font-weight: bold;"
        link = f'<a href="{subscribe_url}" style="{link_style}">{tr("Subscribe")}</a>'
        self._show_status_box(f"{message} {link}", "warning")
        # 12 s banner; parented timer dies with the dock.
        if self._status_hide_timer is not None:
            self._status_hide_timer.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._hide_status_box)
        timer.start(12000)
        self._status_hide_timer = timer

    def _show_reference_upsell(self) -> None:
        """Free-tier user hit the reference-image cap: nudge to subscribe
        instead of adding another one."""
        self._show_subscribe_banner(_reference_cap_message(free_tier_max_references()))

    def _on_resolution_selected(self, label: str):
        """Handle a click inside the resolution dropdown of either container."""
        if not is_tier_allowed(label, self._is_free_tier):
            shipped = tr("{} outputs are unlocked with a subscription.").format(label)
            served = get_export_copy("upsell.resolution", shipped, escape=True)
            # A served sentence can name the tier with {tier}. A plain replace,
            # never format(), so a stray brace cannot raise on the click path.
            self._show_subscribe_banner(served.replace("{tier}", label))
            return

        # Clear any existing status message if switching resolutions
        self._hide_status_box()

        self._selected_resolution = label
        # A manual pick is sticky: tier refreshes stop applying the paid
        # "2K" default over it (set_credits).
        self._resolution_user_choice = True
        self._refresh_resolution_triggers()
        self._update_generate_button_text()

    def _update_generate_button_text(self):
        """Keep the first Generate label stable. The result-state button reflects
        which version the next edit builds on (see _update_result_generate_label).

        Both halves early-out on an unchanged label. This sits on the keystroke
        path (through _update_generate_enabled), where it otherwise ran four
        tr() lookups and four setters per key to write back the same strings.
        """
        loading = bool(self._imagery_loading)
        if getattr(self, "_generate_label_loading", None) is not loading:
            self._generate_label_loading = loading
            if loading:
                self._generate_btn.setText(tr("Loading imagery..."))
                self._generate_btn.setToolTip(tr(
                    "Waiting for the example basemap to finish loading before you generate"
                ))
            else:
                self._generate_btn.setText(tr("Generate"))
                self._generate_btn.setToolTip(tr("Run the AI edit on your selected zone"))
        self._update_result_generate_label()

    def _update_result_generate_label(self):
        """Result button + prompt placeholder both name the selected base, so the
        user sees that what they type generates FROM the selected version
        ('Generate from Original' / 'Generate from V2').

        State: the base name the two strings were last written for. Nothing
        else writes either of them, so an unchanged base means unchanged text."""
        base = self._version_strip.label_for(self._version_strip.selected_index())
        if getattr(self, "_result_generate_base", None) == base:
            return
        self._result_generate_base = base
        # On the version this run just produced (the default), the prompt is
        # still in the field, so the button says what the click does. Picking an
        # older base is a different job and keeps naming it.
        strip = self._version_strip
        on_latest = strip.selected_index() >= strip.count() - 1
        self._result_regenerate_btn.setText(
            tr("Generate again") if on_latest
            else tr("Generate from {base}").format(base=base)
        )
        self._result_prompt_input.setPlaceholderText(
            tr("Type a prompt to edit {base}...").format(base=base)
        )

    def _on_version_selected(self, index: int):
        """A version tile was clicked: tell the plugin (canvas sync) and update
        the result button label + prompt placeholder. Never touches the text."""
        self.base_version_selected.emit(index)
        self._update_result_generate_label()

    def set_resolution_credit_costs(self, costs: dict[str, int]):
        """Update per-resolution credit costs (server config). Costs are
        displayed inside the resolution dropdown via the prompt containers."""
        if costs:
            self._resolution_credit_costs = costs
        self._refresh_resolution_triggers()

    def get_selected_resolution(self) -> str:
        """Return the user-selected resolution label."""
        return self._selected_resolution

    def clear_references(self) -> None:
        """Drop every reference image (store + strip). Used when reusing a past
        generation so its references replace, not stack onto, the current ones."""
        if self._reference_widget is not None:
            self._reference_widget.clear()

    def restore_reference_images(self, items: list) -> None:
        """Inject reloaded reference images (QImage, name) into the strip."""
        if self._reference_widget is not None:
            self._reference_widget.add_qimages(items)

    def clear_markup_reference(self) -> None:
        """Drop the Mark up reference (e.g. strokes cleared)."""
        if self._reference_widget is not None:
            self._reference_widget.clear_markup_image()
