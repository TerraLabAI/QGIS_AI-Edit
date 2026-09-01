"""Public API for driving AI Edit without going through its user interface.

This module is the supported way for another plugin, a script, or an external
agent to control AI Edit. Reach it from a live QGIS session with one line:

    import qgis.utils
    qgis.utils.plugins["AI_Edit"].mcp_api.get_status()

The contract a caller may rely on:

* Every public method returns a plain dict that ``json.dumps`` can serialise.
* No public method raises. A failure comes back as ``{"_error": "..."}``, so a
  caller tests for that key instead of catching exceptions. That message is
  never blank, and a lookup that missed carries ``_suggestions``, the nearest
  names to the one you gave.
* A result may carry ``hint``, one sentence naming the call that usually comes
  next. It is advice, never a rule, and never something you have to parse.
* The shape is additive forever. A key is never removed, renamed, or given a
  new type. A newer build may only add keys and methods. ``API_VERSION`` names
  the generation of this contract, and ``capabilities()`` lists the methods
  this build carries, so nothing has to be guessed from the plugin version.
* Nothing here opens a dialog, moves the mouse, or waits for a human. A method
  that needs the AI Edit panel opens it first.
* Nothing here signs a user in, writes an activation key, or accepts terms.
  Those are the user's own actions. ``get_status()`` reports what they have to
  do by hand and that is where this API stops.

Generation is single flight: AI Edit holds one selected zone and one worker, so
a second ``generate()`` while one runs comes back as
``{"busy": True, "_error": ...}`` rather than queueing.

Two calls orient a caller that has never seen this plugin. ``capabilities()``
gives the machine-readable shape: what to call first, the usual order, what
costs credits, what is slow. ``guide()`` gives the same ground in prose, aimed
at good results rather than merely valid ones.

A lookup that misses always answers the same way, through ``not_found_error``:
what was asked for, the nearest names to it, and what the argument was for.

The methods live in sibling ``mcp_api_*`` modules, one group each, which this
class composes. ``EditMCPAPI``, ``API_VERSION``, ``AI_EDIT_KEYS``,
``PUBLIC_METHODS``, ``not_found_error``, ``_find_plugin`` and ``get_api`` stay
importable from here.
"""
from __future__ import annotations

import copy
import html
import re
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

# Bumped when this file gains methods or a wider contract. Reported by
# get_status() as "api_version" and by capabilities().
#
# 1: the first surface (status, presets, resolutions, generate, markup,
#    vectorize, versions).
# 2: free-shape zones, the prompt library and favourites, sessions and history,
#    explicit references, mark up parity, a read-only account view, and the two
#    self-describing calls capabilities() and guide().
API_VERSION = 2

# Every method a caller may rely on. Append only, never reorder for meaning.
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
    # Added in API_VERSION 2.
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


# The panel writes an HTML anchor into its status line on a failure a user can
# report. A caller polling this API gets the line verbatim, so a tag would land
# in its payload. Tags out, entities back to their characters, spacing tidied.
_STATUS_MARKUP = re.compile(r"<[^>]+>")


def strip_status_markup(text: str) -> str:
    """The status line as plain words, with any HTML taken out of it."""
    if not text:
        return ""
    return " ".join(html.unescape(_STATUS_MARKUP.sub(" ", text)).split())


def _image_layer_names() -> list[str]:
    """Every image layer in the project, the only kind vectorize() can read."""
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
    """Programmatic control of AI Edit. One instance lives on the plugin.

    Hold it as ``plugin.mcp_api``. Every method is safe to call at any time and
    in any order: readiness is reported, never assumed.
    """

    def __init__(self, plugin):
        self._plugin = plugin

    # --- internals --------------------------------------------------------

    def _dock(self):
        """The panel widget as it stands, without opening it. May be None."""
        return getattr(self._plugin, "_dock_widget", None)

    def _dock_open(self) -> bool:
        """Whether the panel is on screen. False when it is gone or closed."""
        dock = self._dock()
        try:
            return bool(dock is not None and dock.isVisible())
        except Exception:
            return False

    def _open_dock(self):
        """The panel widget, opened first when it is closed.

        An open panel is left exactly as it is, in whatever state the user or a
        previous call left behind.
        """
        ensure = getattr(self._plugin, "_ensure_dock_widget", None)
        if callable(ensure):
            return ensure()
        return self._dock()

    def _is_free_tier(self) -> bool | None:
        """True on the free plan, False on a paid one, None when unknown."""
        dock = self._dock()
        if dock is None:
            return None
        value = getattr(dock, "_is_free_tier", None)
        return None if value is None else bool(value)

    def _busy(self) -> bool:
        """True while a generation holds the plugin (export, upload, or run)."""
        plugin = self._plugin
        worker = getattr(plugin, "_worker", None)
        try:
            if worker is not None and worker.is_active():
                return True
        except Exception:  # nosec B110 - a dead worker is not a busy one.
            pass
        export_worker = getattr(plugin, "_export_worker", None)
        try:
            if export_worker is not None and export_worker.is_active():
                return True
        except Exception:  # nosec B110
            pass
        return getattr(plugin, "_pending_generation", None) is not None

    def _server_config_ok(self) -> bool:
        """Whether the settings a generation needs have arrived from the server."""
        try:
            from .core.canvas_export.export_config import has_server_config
            return bool(has_server_config())
        except Exception:
            return False

    def _can_vectorize(self) -> bool:
        """Whether tracing a colour into polygons can run right now.

        Deliberately independent of the account: tracing reads a raster already
        on this machine with GDAL, sends nothing anywhere and spends no credit,
        so it works signed out and offline. The only thing that can stop it is a
        broken numpy, which the tracing module guards its import against.
        """
        try:
            from .core.generation import vectorization_service
            return vectorization_service.np is not None
        except Exception:
            return False

    def _dock_status(self) -> str:
        """The one line the panel shows the user right now, empty when silent.

        Plain words: the panel writes an anchor into this label on a reportable
        failure, and a tag in a machine payload is noise a caller cannot use.
        """
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

    # --- readiness --------------------------------------------------------

    @_never_raises
    def get_status(self) -> dict:
        """Report whether AI Edit can run right now, without any network call.

        Returns ``installed``, ``ready``, ``state`` (READY, NEEDS_ACTIVATION or
        NO_PANEL), ``api_version``, ``has_key``, ``plan`` ("free", "pro" or ""),
        ``free_tier``, ``dock_open``, ``busy``, ``server_config_ok`` and
        ``can_vectorize``. When the plugin cannot run yet, ``action_required``
        says in one sentence what the user has to do; hand that line to them,
        because nothing in this API can sign anyone in on their behalf.

        ``can_vectorize`` is deliberately separate from ``ready``: tracing a
        colour into polygons runs on this machine, needs no account and no
        connection, and is true even when ``state`` is NEEDS_ACTIVATION.

        Credit balances need a network call and live in ``get_credits()``.
        """
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
            # Kept for callers written against the older reflection layer.
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
        """List what this build supports, so nothing has to be inferred.

        Returns ``api_version``, ``methods`` (the names of every method this
        object carries, so a caller checks membership before using one it only
        needs on newer builds), and ``workflow``, a short machine-readable
        description of how to drive the thing: ``start_here``,
        ``typical_order``, ``costs_credits``, ``slow_poll_required``,
        ``needs_a_zone``, ``makes_a_network_call``, ``permanent``,
        ``free_and_local``, ``read_only``, and two plain lines,
        ``one_at_a_time`` and ``never_available_here``.

        Call ``guide()`` for the same ground written out for a reader.
        """
        return {
            "api_version": API_VERSION,
            "methods": [name for name in PUBLIC_METHODS if hasattr(self, name)],
            "workflow": copy.deepcopy(WORKFLOW),
            "guide": "Call guide() for prose advice on getting good results.",
        }

    @_never_raises
    def guide(self) -> dict:
        """Plain text telling an agent how to get GOOD results, not just valid ones.

        Covers what AI Edit is for and what it is not, the first five calls,
        how to choose and shape a zone, how to write a prompt that works, when
        a reference picture or a drawn mark beats a longer prompt, how long a
        run takes at each output size and why you must never resubmit, how to
        iterate on a result, and when to trace one into polygons.

        Returns ``api_version``, ``text`` and ``workflow``. Costs nothing.
        """
        return {
            "api_version": API_VERSION,
            "text": GUIDE,
            "workflow": copy.deepcopy(WORKFLOW),
        }

    @_never_raises
    def get_credits(self) -> dict:
        """Read the account balance from the server. Makes a network call.

        Returns ``used``, ``limit`` and ``is_free``. A server or connection
        problem comes back in ``error`` and ``code`` with the counts left empty.
        No key, token, or account identifier is ever returned.
        """
        auth = getattr(self._plugin, "_auth_manager", None)
        if auth is None:
            return {"_error": "The account manager is not available."}
        return _usage_fields(auth.get_usage_info())

    @_never_raises
    def get_account(self) -> dict:
        """Read what the account may do. Read only, and makes a network call.

        Returns ``plan`` ("free", "pro" or "" when not known yet),
        ``free_tier``, ``signed_in``, the ``usage`` counts ``get_credits()``
        reports, ``remaining``, ``allowed_resolutions`` for this plan,
        ``default_resolution`` and ``paywall_state`` ("normal", "prewall" or
        "wall"), which says whether the next edit would go through, is one of
        the last few, or would be refused for want of credit. A field comes
        back None when the answer is not known yet rather than guessed.

        Nothing here signs anyone in or changes anything on the account, and no
        key, token, email or account identifier is ever returned. When the user
        is not signed in, hand them ``get_status()["action_required"]``: only
        they can do it.
        """
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
            # None means the plan has not been read back yet, so neither answer
            # would be true. Say so rather than guessing the generous one.
            "allowed_resolutions": None if free_tier is None else (
                list(free_tier_allowed_tiers()) if free_tier else list(resolution_tiers())
            ),
            "default_resolution": None if free_tier is None else default_tier_for(free_tier),
            "paywall_state": paywall,
        }

    @_never_raises
    def get_resolutions(self) -> dict:
        """List the output resolutions this account may pick, and their price.

        Returns ``resolutions`` in the order the panel offers them,
        ``credit_costs`` per resolution, ``allowed`` for this plan, ``current``
        for the one a run would use now, and ``free_tier``.
        """
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

    # --- turning a result into data ---------------------------------------

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
        """Trace one flat colour of a result image into editable polygons.

        Runs on this machine with GDAL. Nothing is sent anywhere, no account is
        needed, and no credit is spent, so this works signed out and offline.
        ``get_status()["can_vectorize"]`` says whether it can run.

        ``target_rgb`` is ``[r, g, b]``, each 0 to 255. ``layer_id`` names the
        image layer to READ by its QGIS layer id, which is the exact way to say
        which one you mean: two results of the same prompt carry the same
        friendly name. ``layer_name`` does the same by name, matching on part of
        it, and is only read when no ``layer_id`` is given. Either way the layer
        must already be in the project, and neither is the name given to the
        polygons, which is built from the source. Leave both out to trace the
        newest AI Edit result. ``tolerance`` widens the colour match,
        ``simplify_factor`` smooths the outlines, and ``class_label`` is written
        on every polygon.

        The new layer is added to the project beside its source. Returns ``ok``,
        ``layer_name``, ``layer_id``, ``feature_count`` and ``source_raster``.
        """
        from .core.generation.vectorization_service import vectorize_by_color
        from .ui.layer_groups import (
            add_layer_to_ai_edit_top,
            most_recent_ai_edit_output,
            promote_layer_to_own_subgroup,
        )

        if not (isinstance(target_rgb, (list, tuple)) and len(target_rgb) == 3):
            return {"_error": "target_rgb must be [r, g, b] with values from 0 to 255."}
        try:
            rgb = tuple(max(0, min(255, int(channel))) for channel in target_rgb)
        except (TypeError, ValueError):
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
                options["tolerance"] = int(tolerance)
            except (TypeError, ValueError):
                return {"_error": "tolerance must be a whole number."}
        if simplify_factor is not None:
            try:
                options["simplify_factor"] = float(simplify_factor)
            except (TypeError, ValueError):
                return {"_error": "simplify_factor must be a number."}

        try:
            layer = vectorize_by_color(
                raster,
                rgb,
                layer_name=f"{raster.name()} vectorized",
                class_label=str(class_label or ""),
                **options,
            )
        except Exception as err:  # noqa: BLE001 - reported, never raised.
            message = getattr(err, "message", None) or str(err)
            return {
                "_error": f"Vectorize failed: {message}",
                "_suggestion": "Try a wider tolerance or a different target_rgb.",
            }
        if layer is None or not layer.isValid():
            return {"_error": "Vectorize produced no usable layer."}

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
        }


def get_api():
    """The API object of the loaded plugin, or None when AI Edit is not loaded.

    A convenience for callers that would rather not walk ``qgis.utils.plugins``
    themselves.
    """
    plugin = _find_plugin()
    return getattr(plugin, "mcp_api", None) if plugin is not None else None
