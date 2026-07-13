from __future__ import annotations

from qgis.PyQt.QtCore import QTimer

from ...core.auth.activation_manager import get_subscribe_url
from ...core.i18n import tr
from ..reference_images_widget import FREE_TIER_MAX_REFERENCES
from .style import BRAND_BLUE


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

    def reveal_version_strip(self) -> None:
        """Keep the restored lineage in its iterate home (above the Generate
        row). Restoring already entered the iterate state; this just re-asserts
        the strip's placement once its thumbnails arrive."""
        self._place_version_strip("result")

    def get_cached_recent_jobs(self) -> list:
        """Session-cached past generations (newest first). Used to rebuild the
        iteration chain when the user reuses a generation from Recent."""
        return list(self._library_recent_cache or [])

    def set_version_strip_readonly(self, readonly: bool) -> None:
        self._version_strip.set_readonly(readonly)

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _refresh_resolution_triggers(self):
        """Push the current selection / costs / tier into both prompt containers.

        Also coerces the selection to "1K" when a free-tier user is ever
        confirmed (downgrades the "2K" default that set_credits applies to
        paid accounts), so the Generate button never quotes a price the user
        can't actually pay.
        """
        if self._is_free_tier and self._selected_resolution != "1K":
            self._selected_resolution = "1K"
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
        """Free-tier user tried to add a second reference image: nudge to
        subscribe instead of adding it."""
        self._show_subscribe_banner(
            tr("Free plan is limited to {n} reference image.").format(
                n=FREE_TIER_MAX_REFERENCES
            )
        )

    def _on_resolution_selected(self, label: str):
        """Handle a click inside the resolution dropdown of either container."""
        if self._is_free_tier and label != "1K":
            self._show_subscribe_banner(
                tr("{} outputs are unlocked with a subscription.").format(label)
            )
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
        """
        if self._imagery_loading:
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
        ('Generate from Original' / 'Generate from V2')."""
        base = self._version_strip.label_for(self._version_strip.selected_index())
        self._result_regenerate_btn.setText(tr("Generate from {base}").format(base=base))
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

    def get_base_version_index(self) -> int:
        """Strip index the next edit builds on (0 = Original)."""
        return self._version_strip.selected_index()

    def clear_references(self) -> None:
        """Drop every reference image (store + strip). Used when reusing a past
        generation so its references replace, not stack onto, the current ones."""
        if self._reference_widget is not None:
            self._reference_widget.clear()

    def restore_reference_images(self, items: list) -> None:
        """Inject reloaded reference images (QImage, name) into the strip."""
        if self._reference_widget is not None:
            self._reference_widget.add_qimages(items)

    def set_markup_reference(self, image) -> None:
        """Show the rendered zone+marks as the (single) Mark up reference in the
        strip. Replaces any previous one."""
        if self._reference_widget is not None:
            self._reference_widget.set_markup_image(image)

    def clear_markup_reference(self) -> None:
        """Drop the Mark up reference (e.g. strokes cleared)."""
        if self._reference_widget is not None:
            self._reference_widget.clear_markup_image()
