











































from __future__ import annotations

import copy
import html
import math
import os
import re
import time
from typing import Any

from qgis.core import QgsProject, QgsRasterLayer

from .mcp_api_generation import GenerationMixin
from .mcp_api_guide import GUIDE, WORKFLOW
from .mcp_api_history import HistoryMixin
from .mcp_api_library import LibraryMixin
from .mcp_api_markup import MarkupMixin
from .mcp_api_references import ReferencesMixin
from .mcp_api_support import (
    AI_EDIT_KEYS,
    _find_plugin,
    _find_project_layer,
    _never_raises,
    _project_layer_names,
    _usage_fields,
    _whole_number,
    not_found_error,
)
from .mcp_api_zone import ZoneMixin

__all__ = [
    "AI_EDIT_KEYS",
    "API_VERSION",
    "EditMCPAPI",
    "PUBLIC_METHODS",
    "get_api",
    "not_found_error",
]









API_VERSION = 2


PUBLIC_METHODS = (
    "capabilities",
    "cancel",
    "generate",
    "generation_status",
    "get_credits",
    "get_presets",
    "get_resolutions",
    "get_status",
    "markup",
    "select_version",
    "vectorize",

    "add_favorite_prompt",
    "add_generation_to_map",
    "attach_reference",
    "clear_references",
    "clear_zone",
    "compare",
    "delete_session",
    "finish_session",
    "get_account",
    "get_generation",
    "get_preset",
    "get_preset_families",
    "get_preset_family",
    "get_prompt_guidance",
    "get_session",
    "get_top_picks",
    "get_zone",
    "guide",
    "list_favorite_prompts",
    "list_generations",
    "list_recent_prompts",
    "list_references",
    "list_sessions",
    "list_versions",
    "markup_status",
    "open_session",
    "remove_favorite_prompt",
    "remove_reference",
    "rename_session",
    "search_presets",
    "set_reference_note",
    "set_resolution",
    "set_zone",
)





_STATUS_MARKUP = re.compile(r"<[^>]+>")


def strip_status_markup(text: str) -> str:

    if not text:
        return ""
    return " ".join(html.unescape(_STATUS_MARKUP.sub(" ", text)).split())


def _image_layer_names() -> list[str]:

    return [
        layer.name()
        for layer in QgsProject.instance().mapLayers().values()
        if isinstance(layer, QgsRasterLayer)
    ]


class EditMCPAPI(
    ZoneMixin,
    GenerationMixin,
    LibraryMixin,
    ReferencesMixin,
    MarkupMixin,
    HistoryMixin,
):






    def __init__(self, plugin):
        self._plugin = plugin



    def _dock(self):

        return getattr(self._plugin, "_dock_widget", None)

    def _dock_open(self) -> bool:

        dock = self._dock()
        try:
            return bool(dock is not None and dock.isVisible())
        except Exception:
            return False

    def _open_dock(self):





        ensure = getattr(self._plugin, "_ensure_dock_widget", None)
        if callable(ensure):
            return ensure()
        return self._dock()

    def _is_free_tier(self) -> bool | None:

        dock = self._dock()
        if dock is None:
            return None
        value = getattr(dock, "_is_free_tier", None)
        return None if value is None else bool(value)

    def _busy(self) -> bool:

        plugin = self._plugin
        worker = getattr(plugin, "_worker", None)
        try:
            if worker is not None and worker.is_active():
                return True
        except Exception:  # nosec B110
            pass
        export_worker = getattr(plugin, "_export_worker", None)
        try:
            if export_worker is not None and export_worker.is_active():
                return True
        except Exception:  # nosec B110
            pass
        return (
            getattr(plugin, "_pending_generation", None) is not None
            or getattr(plugin, "_size_request_token", None) is not None
        )

    def _server_config_ok(self) -> bool:

        try:
            from .core.canvas_export.export_config import has_server_config, has_tuned_config
            return bool(has_server_config() and has_tuned_config())
        except Exception:
            return False

    def _can_vectorize(self) -> bool:







        try:
            from .core.generation import vectorization_service
            return vectorization_service.np is not None
        except Exception:
            return False

    def _dock_status(self) -> str:





        dock = self._dock()
        label = getattr(dock, "_status_label", None) if dock is not None else None
        try:
            return strip_status_markup(label.text()) if label is not None else ""
        except Exception:
            return ""

    def _selected_resolution(self) -> str | None:
        dock = self._dock()
        if dock is None:
            return None
        try:
            return dock.get_selected_resolution()
        except Exception:
            return None



    @_never_raises
    def get_status(self) -> dict:















        plugin = self._plugin
        dock = self._dock()
        has_key = False
        auth = getattr(plugin, "_auth_manager", None)
        if auth is not None:
            try:
                has_key = bool(auth.has_activation_key())
            except Exception:
                has_key = False
        free_tier = self._is_free_tier()
        plan = "" if free_tier is None else ("free" if free_tier else "pro")
        ready = has_key and dock is not None
        state = "READY"
        if dock is None:
            state = "NO_PANEL"
        elif not has_key:
            state = "NEEDS_ACTIVATION"
        status: dict[str, Any] = {
            "installed": True,
            "api_version": API_VERSION,
            "ready": ready,
            "state": state,
            "has_key": has_key,
            "plan": plan,
            "free_tier": free_tier,

            "is_free_tier": free_tier,
            "dock_open": self._dock_open(),
            "busy": self._busy(),
            "server_config_ok": self._server_config_ok(),
            "can_vectorize": self._can_vectorize(),
            "resolution": self._selected_resolution(),
            "has_zone": bool(getattr(plugin, "_selected_extent", None)),
        }
        if not ready:
            status["action_required"] = (
                "Open the AI Edit panel from the TerraLab toolbar."
                if dock is None
                else "Sign in from the AI Edit panel to activate the plugin."
            )
        return status

    @_never_raises
    def capabilities(self) -> dict:













        return {
            "api_version": API_VERSION,
            "methods": [name for name in PUBLIC_METHODS if hasattr(self, name)],
            "workflow": copy.deepcopy(WORKFLOW),
            "guide": "Call guide() for prose advice on getting good results.",
        }

    @_never_raises
    def guide(self) -> dict:










        return {
            "api_version": API_VERSION,
            "text": GUIDE,
            "workflow": copy.deepcopy(WORKFLOW),
        }

    @_never_raises
    def get_credits(self) -> dict:






        auth = getattr(self._plugin, "_auth_manager", None)
        if auth is None:
            return {"_error": "The account manager is not available."}
        return _usage_fields(auth.get_usage_info())

    @_never_raises
    def get_account(self) -> dict:















        from .core.entitlements import (
            default_tier_for,
            free_tier_allowed_tiers,
        )
        from .core.paywall_state import classify_paywall_state
        from .core.resolution_labels import resolution_tiers

        auth = getattr(self._plugin, "_auth_manager", None)
        signed_in = False
        if auth is not None:
            try:
                signed_in = bool(auth.has_activation_key())
            except Exception:
                signed_in = False
        free_tier = self._is_free_tier()
        usage = _usage_fields(auth.get_usage_info()) if auth is not None else {}

        remaining = None
        used, limit = usage.get("used"), usage.get("limit")
        if isinstance(used, (int, float)) and isinstance(limit, (int, float)):
            remaining = max(0, int(limit) - int(used))
        paywall = None
        if remaining is not None:
            costs = getattr(self._dock(), "_resolution_credit_costs", None)
            current = self._selected_resolution()
            unit = None
            if isinstance(costs, dict) and current:
                unit = costs.get(current)
            if isinstance(unit, (int, float)) and unit > 0:
                paywall = classify_paywall_state(remaining, int(unit))

        return {
            "signed_in": signed_in,
            "plan": "" if free_tier is None else ("free" if free_tier else "pro"),
            "free_tier": free_tier,
            "usage": usage,
            "remaining": remaining,


            "allowed_resolutions": None if free_tier is None else (
                list(free_tier_allowed_tiers()) if free_tier else list(resolution_tiers())
            ),
            "default_resolution": None if free_tier is None else default_tier_for(free_tier),
            "paywall_state": paywall,
        }

    @_never_raises
    def get_resolutions(self) -> dict:






        from .core.entitlements import free_tier_allowed_tiers
        from .core.resolution_labels import (
            DEFAULT_RESOLUTION_CREDIT_COSTS,
            resolution_tiers,
        )

        dock = self._dock()
        costs = getattr(dock, "_resolution_credit_costs", None) if dock is not None else None
        free_tier = self._is_free_tier()
        tiers = list(resolution_tiers())
        allowed = list(free_tier_allowed_tiers()) if free_tier else tiers
        return {
            "resolutions": tiers,
            "credit_costs": dict(costs) if isinstance(costs, dict)
            else dict(DEFAULT_RESOLUTION_CREDIT_COSTS),
            "allowed": allowed,
            "current": self._selected_resolution(),
            "free_tier": free_tier,
        }



    @_never_raises
    def vectorize(
        self,
        target_rgb: list | None,
        layer_name: str | None = None,
        tolerance: int | None = None,
        simplify_factor: float | None = None,
        class_label: str = "",
        layer_id: str | None = None,
    ) -> dict:























        from .core.generation.vectorization_service import vectorize_by_color
        from .core.generation.vectorize_layer import (
            AI_EDIT_GPKG_FILENAME,
            friendly_vector_layer_name,
            persist_layer_to_gpkg,
        )
        from .core.slug import slugify
        from .ui.layer_groups import (
            add_layer_to_ai_edit_top,
            most_recent_ai_edit_output,
            promote_layer_to_own_subgroup,
        )
        from .ui.raster_writer import get_output_dir

        if not (isinstance(target_rgb, (list, tuple)) and len(target_rgb) == 3):
            return {"_error": "target_rgb must be [r, g, b] with values from 0 to 255."}
        try:
            rgb = tuple(_whole_number(channel) for channel in target_rgb)
            if any(channel < 0 or channel > 255 for channel in rgb):
                raise ValueError("RGB channel out of range")
        except (TypeError, ValueError, OverflowError):
            return {"_error": "target_rgb values must be whole numbers from 0 to 255."}

        source_means = (
            "layer_name is the image layer to trace, not a name for the polygons."
        )
        layer_id = (layer_id or "").strip()
        if layer_id:
            raster = QgsProject.instance().mapLayer(layer_id)
            if raster is None:
                return not_found_error(
                    "project layer id",
                    layer_id,
                    list(QgsProject.instance().mapLayers()),
                    means=(
                        "layer_id is the QGIS id of the image layer to trace. It has to be "
                        "a layer already in the project."
                    ),
                )
        elif layer_name:
            raster = _find_project_layer(layer_name)
            if raster is None:
                return not_found_error(
                    "project layer", layer_name, _project_layer_names(), means=source_means
                )
        else:
            raster = most_recent_ai_edit_output(lambda lyr: isinstance(lyr, QgsRasterLayer))
            if raster is None:
                return not_found_error(
                    "AI Edit result",
                    "the newest one",
                    _image_layer_names(),
                    means="Pass layer_name to trace an image layer already in the project.",
                )
        if not isinstance(raster, QgsRasterLayer):
            return not_found_error(
                "image layer", raster.name(), _image_layer_names(), means=source_means
            )

        options: dict[str, Any] = {}
        if tolerance is not None:
            try:
                options["tolerance"] = _whole_number(tolerance)
                if not 0 <= options["tolerance"] <= 255:
                    return {"_error": "tolerance must be between 0 and 255."}
            except (TypeError, ValueError, OverflowError):
                return {"_error": "tolerance must be a whole number."}
        if simplify_factor is not None:
            try:
                options["simplify_factor"] = float(simplify_factor)
                if not math.isfinite(options["simplify_factor"]) or options["simplify_factor"] < 0:
                    return {"_error": "simplify_factor must be a finite non-negative number."}
            except (TypeError, ValueError, OverflowError):
                return {"_error": "simplify_factor must be a number."}

        label = str(class_label or "")
        try:
            layer = vectorize_by_color(
                raster,
                rgb,
                layer_name=friendly_vector_layer_name(label, raster.name()),
                class_label=label,
                **options,
            )
        except Exception as err:  # noqa: BLE001
            message = getattr(err, "message", None) or str(err)
            return {
                "_error": f"Vectorize failed: {message}",
                "_suggestion": "Try a wider tolerance or a different target_rgb.",
            }
        if layer is None or not layer.isValid():
            return {"_error": "Vectorize produced no usable layer."}



        classes = [{"rgb": rgb, "label": label}]
        base = slugify(label or raster.name())[:40] or "result"
        persisted, _reason = persist_layer_to_gpkg(
            layer,
            os.path.join(get_output_dir(), AI_EDIT_GPKG_FILENAME),
            f"vectorize_{base}_{time.strftime('%Y%m%d_%H%M%S')}",
            classes,
            raster.name(),
        )
        if persisted is not None:
            layer = persisted

        subgroup = promote_layer_to_own_subgroup(raster.id())
        QgsProject.instance().addMapLayer(layer, False)
        if subgroup is not None:
            subgroup.insertLayer(0, layer)
        else:
            add_layer_to_ai_edit_top(layer)
        return {
            "ok": True,
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "feature_count": layer.featureCount(),
            "source_raster": raster.name(),
            "target_rgb": list(rgb),
            "saved_to_file": persisted is not None,
        }


def get_api():





    plugin = _find_plugin()
    return getattr(plugin, "mcp_api", None) if plugin is not None else None
