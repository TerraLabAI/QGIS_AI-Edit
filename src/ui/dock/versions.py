from __future__ import annotations

from qgis.PyQt.QtCore import QTimer

from ...core.config_store import get_export_copy, get_export_dial
from ...core.entitlements import coerce_tier, is_tier_allowed
from ...core.i18n import tr
from ..reference_images_widget import free_tier_max_references

_SUBSCRIBE_BANNER_MS = 12000


def _reference_cap_message(cap: int) -> str:
    """The free-plan reference-image nudge, in the right number.

    The cap is a dial and it ships at 3, so the singular sentence this used to
    show was already wrong ("limited to 3 reference image"). Both readings are
    sentences the plugin already ships, so both stay translated, and a served
    entry can replace either."""
    # "References", the word of the chip and the panel, in one sentence
    # shape for both numbers (the plural used to read as a bare error).
    if cap == 1:
        shipped = tr("The Free plan takes {n} reference.").format(n=cap)
    else:
        shipped = tr("The Free plan takes up to {n} references.").format(n=cap)
    return get_export_copy("upsell.reference_cap_v2", shipped, escape=True)


class DockVersionsMixin:
    """Version strip lineage, resolution pickers, and reference-image
    restore hooks for AIEditDockWidget."""

    def seed_version_strip(self, original_pixmap, prompt: str = "", meta: dict | None = None) -> None:
        """Seed the strip with the Original tile (selected). Called once per
        lineage when the clean base capture becomes available."""
        # A new lineage has saved nothing yet, so the details card must not
        # name the layer an earlier lineage (or a restored session) wrote.
        self._saved_layer_id = ""
        self._version_strip.reset(original_pixmap, prompt, meta)
        # Seeded mid-run (the first export of a zone): a row holding the
        # Original alone under the progress card offers nothing to pick. It
        # shows with the first result, next to what that result came from.
        if self._progress_widget.isVisibleTo(self):
            self._version_strip.setVisible(False)
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
        """No longer mirrors the version's prompt into the iterate box.

        The box used to be refilled with the prompt that produced the picked
        version, which read as "press Generate to make the same thing again"
        (Yvann, 2026-09-17). The next change is a new sentence, so the box
        stays as the user left it: empty after a generation, or holding what
        they have started typing. The prompt that produced a version is one
        click away, in that version's details card, with Copy prompt.

        ``text`` is kept in the signature because the plugin still calls this
        on every version pick; the prompt it passes is what the card shows."""
        del text

    def saved_layer_probe(self):
        """``(layer name, reveal callback)`` for the layer the last generation
        wrote, or None when there is none in the project any more.

        The result screen used to spend a whole row on "Saved as <layer>",
        which Yvann read as noise (2026-09-17). The fact now shows inside the
        newest version's details card, and its name still selects the layer in
        the QGIS Layers panel."""
        layer_id = getattr(self, "_saved_layer_id", "")
        if not layer_id:
            return None
        try:
            from qgis.core import QgsProject

            layer = QgsProject.instance().mapLayer(layer_id)
        except Exception:  # noqa: BLE001 - no project reads as no saved layer
            return None
        if layer is None:
            return None
        return (layer.name(), lambda: self._on_layer_saved_link_clicked(""))

    def reveal_version_strip(self) -> None:
        """Keep the restored lineage in its iterate home (under the Generate
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

    def _show_subscribe_banner(self, message: str, surface: str) -> None:
        """Show a 12 s warning banner with an "Upgrade to Pro" action.

        Shared by the free-tier resolution gate and the reference-image gate so
        the upsell copy/styling stays in one place. The action goes through
        the signed-in door, named after ``surface``, and the view is counted.
        """
        self._show_status_box(message, "warning")
        self.set_status_action(
            get_export_copy("upsell.upgrade_button", tr("Upgrade to Pro")),
            lambda: self._open_pro_from(surface),
        )
        self._track_upsell_view(surface)
        # 12 s banner; parented timer dies with the dock.
        if self._status_hide_timer is not None:
            self._status_hide_timer.stop()

        def _hide_unchanged_banner(expected: str = message) -> None:
            if self._status_label.text() == expected:
                self._hide_status_box()

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(_hide_unchanged_banner)
        timer.start(get_export_dial("dock.versions.subscribe_banner_ms", _SUBSCRIBE_BANNER_MS))
        self._status_hide_timer = timer

    def _show_reference_upsell(self) -> None:
        """Free-tier user hit the reference-image cap: nudge to subscribe
        instead of adding another one."""
        self._show_subscribe_banner(
            _reference_cap_message(free_tier_max_references()), "reference_limit")

    def _on_resolution_selected(self, label: str):
        """Handle a click inside the resolution dropdown of either container."""
        if not is_tier_allowed(label, self._is_free_tier):
            shipped = tr("{} outputs are unlocked with a subscription.").format(label)
            served = get_export_copy("upsell.resolution", shipped, escape=True)
            # A served sentence can name the tier with {tier}. A plain replace,
            # never format(), so a stray brace cannot raise on the click path.
            self._show_subscribe_banner(served.replace("{tier}", label), "resolution_lock")
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
                self._generate_btn.setText(
                    get_export_copy("dock.versions.imagery_loading", tr("Loading imagery..."))
                )
                self._generate_btn.setToolTip(get_export_copy(
                    "dock.versions.imagery_loading_tooltip",
                    tr("Waiting for the example basemap to finish loading before you generate"),
                ))
            else:
                self._generate_btn.setText(get_export_copy("dock.versions.generate", tr("Generate")))
                self._generate_btn.setToolTip(
                    get_export_copy("dock.versions.generate_zone_tooltip", tr("Generate the edit on your zone"))
                )
        self._update_result_generate_label()

    def _update_result_generate_label(self):
        """Button and placeholder both name the picked version, so the screen
        says what the next run does: it edits THAT version.

        One wording for every base ('Generate from V2', 'Generate from
        Original'). "Edit this result" was true only on the newest version and
        never said which one, so the two halves of the screen disagreed about
        what the button would run on (Yvann, 2026-09-17).

        State: the base name the two strings were last written for. Nothing
        else writes either of them, so an unchanged base means unchanged text."""
        base = self._version_strip.label_for(self._version_strip.selected_index())
        if getattr(self, "_result_generate_base", None) == base:
            return
        self._result_generate_base = base
        # A served sentence can reuse {base}: a plain replace, never format(),
        # so a stray brace in served copy cannot raise on this path.
        self._result_regenerate_btn.setText(
            get_export_copy(
                "dock.versions.generate_from_base", tr("Generate from {base}")
            ).replace("{base}", base)
        )
        self._result_prompt_input.setPlaceholderText(
            get_export_copy(
                "dock.versions.next_change_placeholder",
                tr("Describe the next change to {base}"),
            ).replace("{base}", base)
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

    def set_reference_layers_above(self, layers: list) -> int:
        """Attach the layers drawn above the picked layer as references,
        replacing the ones attached for the previous zone. Returns the count."""
        if self._reference_widget is None:
            return 0
        return self._reference_widget.set_layers_above(layers)

    def clear_markup_reference(self) -> None:
        """Drop the Mark up reference (e.g. strokes cleared)."""
        if self._reference_widget is not None:
            self._reference_widget.clear_markup_image()
