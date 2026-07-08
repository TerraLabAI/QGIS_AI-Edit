from __future__ import annotations

from qgis.PyQt.QtCore import QTimer

from ...core.config_store import get_export_copy, get_export_dial
from ...core.entitlements import coerce_tier, is_tier_allowed
from ...core.i18n import tr
from ..reference_images_widget import free_tier_max_references

_SUBSCRIBE_BANNER_MS = 12000


def _reference_cap_message(cap: int) -> str:








    if cap == 1:
        shipped = tr("The Free plan takes {n} reference.").format(n=cap)
    else:
        shipped = tr("The Free plan takes up to {n} references.").format(n=cap)
    return get_export_copy("upsell.reference_cap_v2", shipped, escape=True)


class DockVersionsMixin:



    def seed_version_strip(self, original_pixmap, prompt: str = "", meta: dict | None = None) -> None:




        self._saved_layer_id = ""
        self._version_strip.reset(original_pixmap, prompt, meta)



        if self._progress_widget.isVisibleTo(self):
            self._version_strip.setVisible(False)
        self._update_result_generate_label()

    def add_version_thumb(self, pixmap, prompt: str = "", meta: dict | None = None) -> int:

        index = self._version_strip.add_version(pixmap, prompt, meta)
        self._update_result_generate_label()
        return index

    def reset_version_strip(self) -> None:

        self._version_strip.clear()

    def select_version(self, index: int) -> None:

        self._version_strip.set_selected(index)

    def set_version_strip_locked(self, locked: bool) -> None:



        self._version_strip.set_readonly(locked)

    def set_result_prompt_text(self, text: str) -> None:











        del text

    def saved_layer_probe(self):







        layer_id = getattr(self, "_saved_layer_id", "")
        if not layer_id:
            return None
        try:
            from qgis.core import QgsProject

            layer = QgsProject.instance().mapLayer(layer_id)
        except Exception:  # noqa: BLE001
            return None
        if layer is None:
            return None
        return (layer.name(), lambda: self._on_layer_saved_link_clicked(""))

    def reveal_version_strip(self) -> None:



        self._place_version_strip("result")

    def get_cached_recent_jobs(self) -> list:


        return list(self._library_recent_cache or [])





    def _refresh_resolution_triggers(self):







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






        self._show_status_box(message, "warning")
        self.set_status_action(
            get_export_copy("upsell.upgrade_button", tr("Upgrade to Pro")),
            lambda: self._open_pro_from(surface),
        )
        self._track_upsell_view(surface)

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


        self._show_subscribe_banner(
            _reference_cap_message(free_tier_max_references()), "reference_limit")

    def _on_resolution_selected(self, label: str):

        if not is_tier_allowed(label, self._is_free_tier):
            shipped = tr("{} outputs are unlocked with a subscription.").format(label)
            served = get_export_copy("upsell.resolution", shipped, escape=True)


            self._show_subscribe_banner(served.replace("{tier}", label), "resolution_lock")
            return


        self._hide_status_box()

        self._selected_resolution = label


        self._resolution_user_choice = True
        self._refresh_resolution_triggers()
        self._update_generate_button_text()

    def _update_generate_button_text(self):







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










        base = self._version_strip.label_for(self._version_strip.selected_index())
        if getattr(self, "_result_generate_base", None) == base:
            return
        self._result_generate_base = base


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


        self.base_version_selected.emit(index)
        self._update_result_generate_label()

    def set_resolution_credit_costs(self, costs: dict[str, int]):


        if costs:
            self._resolution_credit_costs = costs
        self._refresh_resolution_triggers()

    def get_selected_resolution(self) -> str:

        return self._selected_resolution

    def clear_references(self) -> None:


        if self._reference_widget is not None:
            self._reference_widget.clear()

    def restore_reference_images(self, items: list) -> None:

        if self._reference_widget is not None:
            self._reference_widget.add_qimages(items)

    def set_reference_layers_above(self, layers: list) -> int:


        if self._reference_widget is None:
            return 0
        return self._reference_widget.set_layers_above(layers)

    def clear_markup_reference(self) -> None:

        if self._reference_widget is not None:
            self._reference_widget.clear_markup_image()
