from __future__ import annotations

import os
import time
import urllib.parse
import uuid

from qgis.core import Qgis, QgsApplication, QgsPointXY, QgsRectangle
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import QEvent, QObject, QSettings, Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPixmap
from qgis.PyQt.QtWidgets import QAction, QShortcut

from ..api.terralab_client import TerraLabClient
from ..core import qt_compat as QtC
from ..core import telemetry
from ..core import telemetry_events as te
from ..core.auth.activation_manager import (
    clear_activation,
    clear_config_cache,
    get_activation_key,
    get_dashboard_url,
    get_server_config,
    has_consent,
    migrate_legacy_key,
    save_activation,
    save_consent,
    validate_key_with_server,
)
from ..core.auth.auth_manager import AuthManager
from ..core.config_store import ConfigStore, set_store
from ..core.errors import NETWORK_ERROR_CODES
from ..core.generation.generation_service import GenerationService
from ..core.generation.pipeline_context import PipelineContext
from ..core.i18n import tr
from ..core.logger import log, log_debug, log_warning
from ..core.prompts import prompt_history
from ..core.prompts.prompt_presets import (
    detect_freeform_vector_intent,
    get_preset_by_id,
    get_vector_hints,
    lookup_template_by_prompt,
)
from ..core.prompts.session_grouping import session_jobs_for
from ..core.reference_image_store import ReferenceImageStore
from ..workers.export_worker import ExportWorker
from ..workers.generation_worker import GenerationWorker
from ..workers.generic_request_task import GenericRequestTask
from ..workers.pairing_poll_task import PairingPollTask
from .canvas_exporter import (
    apply_export_context,
    has_server_config,
    prepare_export,
    set_server_config,
)
from .dialogs.error_report_dialog import REPORT_PROBLEM_HREF
from .dock_widget import AIEditDockWidget
from .raster_writer import (
    add_geotiff_to_project,
    extent_and_crs_from_job,
    get_output_dir,
)
from .tools.markup_tools import (
    ArrowMapTool,
    CircleMapTool,
    MarkupLayerManager,
    PencilMapTool,
)
from .tools.selection_map_tool import RectangleSelectionTool

# --- Onboarding basemaps (empty-canvas "Try it on an example") ----------------
# Esri World Imagery: the key-free, ToS-clean global backdrop QGIS and
# QuickMapServices ship. zmax=21 unlocks Esri's native sub-metre tiles in metro
# areas, so a tight zone stays crisp instead of upsampling a z19 tile.
_ESRI_WORLD_IMAGERY_URI = (
    "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D&zmax=21&zmin=0"
)
# IGN Géoplateforme orthophotos: key-free since the 2021 open-data switch,
# Licence Ouverte Etalab 2.0 (commercial reuse + derivatives OK with
# attribution), ~20cm over metropolitan France. Used only for the France demo
# scene because coverage is France-only (blank tiles elsewhere); the WMTS-KVP
# endpoint is consumed as XYZ with the standard PM (web-mercator) tile matrix.
# HR.* is the current canonical layer id (verified serving image/jpeg tiles).
_IGN_ORTHO_TILE_URL = (
    "https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile"
    "&LAYER=HR.ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM"
    "&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
)
# Encode only what would break the outer XYZ URI's own '&'/'=' separators (the
# query's '&','=' and the {z}/{x}/{y} braces); keep '://', '/', '?' literal to
# match the proven Esri form above, which is how QGIS's XYZ provider expects it.
_IGN_ORTHO_URI = (
    "type=xyz&url=" + urllib.parse.quote(_IGN_ORTHO_TILE_URL, safe=":/?") + "&zmax=21&zmin=0"
)
# Eiffel Tower + the Seine (tower ~48.8584, 2.2945), WGS84. The example zone is
# framed and drawn here: Paris's most recognisable landmark with the river in
# frame, so the pre-filled sea-level-rise prompt has real water to grow from and
# the first result lands as a striking "flood around the Eiffel Tower".
_DEMO_ZONE_WGS84 = {"xmin": 2.2895, "ymin": 48.8552, "xmax": 2.2990, "ymax": 48.8616}
# Preset id (mirrored with the website catalog) pre-filled into the prompt:
# photorealistic sea-level rise, a top-pick climate scenario that reads as an
# instant "wow" on a waterfront scene. A no-op if the catalog isn't cached yet
# (first run offline), so the zone is still drawn.
_DEMO_PRESET_ID = "simulate_sea_level"


class _MarkupUndoFilter(QObject):
    """Main-window event filter that intercepts Cmd/Ctrl+Z during Mark up.

    Installed via mainWindow.installEventFilter() so the keypress fires
    before QGIS's own Undo action regardless of which widget has focus.
    """

    def __init__(self, on_undo) -> None:
        super().__init__()
        self._on_undo = on_undo

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() != QEvent.Type.KeyPress:
            return False
        if event.matches(QKeySequence.StandardKey.Undo):
            self._on_undo()
            return True
        return False


# Qt maps Ctrl -> Cmd on macOS automatically.
LAUNCH_SHORTCUT = "Ctrl+Alt+E"


DASHBOARD_ERROR_URL = (
    "https://terra-lab.ai/dashboard"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dashboard_error"
)
SUBSCRIBE_ERROR_URL = (
    "https://terra-lab.ai/dashboard/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=subscribe"
)


def _key_validation_request(client, key):
    """Run validate_key_with_server and reshape its tuple into the dict shape
    GenericRequestTask expects (the /usage payload on success, {"error": msg,
    "code": code} on failure). Passing the payload through lets the caller
    reuse it for the credits display instead of fetching /usage twice."""
    success, message, code, usage = validate_key_with_server(client, key)
    if success:
        return usage if isinstance(usage, dict) else {}
    return {"error": message, "code": code}


def _server_catalog_request(client, force_refresh: bool):
    """Fetch server preset catalog. Returns the dict on success or a sentinel
    error dict so GenericRequestTask routes via ``failed``."""
    from ..core.prompts.prompt_presets_client import fetch_server_catalog
    catalog = fetch_server_catalog(client, force_refresh=force_refresh)
    if catalog is None:
        return {"error": "Catalog unavailable", "code": "UNAVAILABLE"}
    return catalog


def _localize_server_error(error: str, code: str) -> str:
    """Replace an English server error string with its localized equivalent
    when the server's `code` is one we know about. Keeps the original English
    text for unknown codes so users still see something actionable.

    The server stays i18n-agnostic: it sends stable codes; the plugin owns the
    user-facing copy. Adding a new server error code = add one branch here and
    one tr() string in the .ts files.
    """
    if not code:
        return error
    mapping = {
        # Connectivity codes (client-generated by terralab_client). The base
        # sentence stays short here; _enrich_error_message appends the matching
        # actionable hint so the two never duplicate "check your connection".
        "NO_NETWORK": tr("No internet connection."),
        "DNS_ERROR": tr("Cannot reach the server."),
        "TIMEOUT": tr("The request timed out."),
        "SSL_ERROR": tr("Secure connection failed."),
        "PROXY_ERROR": tr("Proxy connection failed."),
        "CONNECTION_REFUSED": tr("Could not connect to the service."),
        "AUTH_ERROR": tr("Authentication failed. Check your activation key."),
        # Account / key codes (server-generated). Translated here so a French
        # user never sees the raw English server string.
        "NO_KEY": tr("No activation key. Enter your key to use AI Edit."),
        "INVALID_KEY": tr("Invalid activation key."),
        "KEY_REVOKED": tr("This activation key has been revoked."),
        "SUBSCRIPTION_EXPIRED": tr("Your subscription has expired."),
        "SUBSCRIPTION_INACTIVE": tr("Your subscription is inactive."),
        "FREE_TIER_EXPIRED": tr("Your free trial has ended."),
        "DEVICE_LIMIT_EXCEEDED": tr(
            "This license is already in use on the maximum number of computers."
            " Free one in your account, or wait for an inactive one to expire."
        ),
        "RATE_LIMITED": tr("Too many requests, please wait a moment."),
        "RATE_LIMITER_DOWN": tr("Service temporarily unavailable, please retry shortly."),
        "STORAGE_UNAVAILABLE": tr("Storage temporarily unavailable, please retry shortly."),
        "SIGN_FAILED": tr("Could not prepare upload, please retry shortly."),
        "UPLOAD_TOKEN_INVALID": tr("Upload session expired, please retry."),
        "UPLOAD_TOKEN_MISMATCH": tr("Upload session does not match your account."),
        "WRONG_PRODUCT": tr("This activation key is for a different product."),
        "WRONG_REQUEST": tr("Unknown or unauthorized request."),
        "AUTH_MIGRATION_REQUIRED": tr("Account migration required, please re-login from the website."),
        "NOT_READY": tr("Result not ready yet."),
        "NOT_AVAILABLE": tr("Result not available."),
        "UPSTREAM_UNAVAILABLE": tr("Result temporarily unavailable, please retry shortly."),
        "UPSTREAM_EMPTY": tr("Result temporarily unavailable, please retry shortly."),
        "FAL_BAD_RESPONSE": tr("The generation service returned an unexpected response, please retry."),
        "FAL_ERROR": tr("Generation failed, please try again."),
        "MISCONFIGURED": tr("Service not configured. Please contact support."),
        "SERVER_ERROR": tr("Service temporarily unavailable, please retry shortly."),
        "DB_ERROR": tr("Database error, please retry shortly."),
        "BAD_REQUEST": tr("Invalid request. Check your prompt and the selected area, then try again."),
        "BAD_INPUT": tr("Invalid input. Check your prompt and the selected area."),
        "INVALID_INPUT": tr("Invalid input. Try a different image or selection."),
        "PAYLOAD_TOO_LARGE": tr("Image too large. Try selecting a smaller area or lowering the resolution."),
        "RESOLUTION_NOT_ALLOWED": tr(
            "This detail level is not available on your plan."
            " Upgrade to unlock more detail."
        ),
        "NOT_FOUND": tr("Resource not found."),
        "NOT_SEEDED": tr("Catalog not yet available, please retry shortly."),
        "DEMO_FETCH_FAILED": tr("Could not load the demo preview."),
        "UNKNOWN_TEMPLATE": tr("Unknown template."),
    }
    return mapping.get(code, error)


# Three-tier failure-report policy, decided by error code:
#
#  Tier 1 (_USER_FIXABLE_CODES)  -> plain inline message, no log prompt.
#     The user owns the fix (their link, key, zone, plan). Not our bug.
#  Tier 2 (_TRANSIENT_CODES)     -> inline message + an optional "Report a
#     problem" link. Retryable on our side (rate limit, busy server, timeout);
#     the user can send a log if it persists, but we never force a modal.
#  Tier 3 (everything else)       -> auto-open the log-report dialog. A likely
#     genuine bug (download/write/empty response, unknown) we want to hear about.
_USER_FIXABLE_CODES = NETWORK_ERROR_CODES | frozenset({
    "AUTH_ERROR",
    "NO_KEY", "INVALID_KEY", "KEY_REVOKED", "AUTH_LOCKED",
    "INVALID_CRS", "ANTIMERIDIAN", "POLAR", "TOO_LARGE",
    "MAP_ROTATED", "ZONE_TOO_SMALL", "PAYLOAD_TOO_LARGE",
    "BAD_REQUEST", "BAD_INPUT", "INVALID_INPUT", "RESOLUTION_NOT_ALLOWED",
    "QUOTA_EXCEEDED", "LIMIT_REACHED", "USAGE_LIMIT_REACHED",
    "MONTHLY_LIMIT_REACHED", "TRIAL_EXHAUSTED",
    "SUBSCRIPTION_EXPIRED", "SUBSCRIPTION_INACTIVE", "FREE_TIER_EXPIRED",
    "DEVICE_LIMIT_EXCEEDED",
    "GENERATION_CANCELLED",
})

_TRANSIENT_CODES = frozenset({
    "RATE_LIMITED", "RATE_LIMITER_DOWN", "STORAGE_UNAVAILABLE", "SIGN_FAILED",
    "UPSTREAM_UNAVAILABLE", "UPSTREAM_EMPTY", "SERVER_ERROR",
    "GENERATION_TIMED_OUT", "FAL_ERROR", "FAL_BAD_RESPONSE",
    "NOT_READY", "NOT_AVAILABLE", "UPLOAD_TOKEN_INVALID", "UPLOAD_TOKEN_MISMATCH",
})

# Failures where a credit is never kept: the generation either never reached the
# charge point, or the server auto-refunds the failed job. We append a short
# "Aucun crédit utilisé." so the user is reassured on EVERY such error. The
# messages that already spell out the refund (download/write/timeout) and the
# model-failure branch carry their own wording and are intentionally excluded.
_CREDIT_REASSURE_CODES = frozenset({
    "GENERATION_FAILED", "SERVER_ERROR", "FAL_ERROR", "FAL_BAD_RESPONSE",
    "UPSTREAM_UNAVAILABLE", "UPSTREAM_EMPTY", "EMPTY_RESPONSE",
    "IMAGE_FORMAT_UNSUPPORTED", "STORAGE_UNAVAILABLE", "RATE_LIMITED",
    "RATE_LIMITER_DOWN", "MISCONFIGURED", "DB_ERROR", "NOT_AVAILABLE",
    "NOT_READY",
})


def _report_policy(normalized_code: str) -> str:
    """Return 'none' | 'link' | 'dialog' for a failure code (see tier comment)."""
    if normalized_code in _USER_FIXABLE_CODES:
        return "none"
    if normalized_code in _TRANSIENT_CODES:
        return "link"
    return "dialog"


def _enrich_error_message(error: str, code: str = "") -> str:
    """Translate the server-supplied error (via code), then append actionable
    guidance (deep links, network hints) based on the same code."""
    localized = _localize_server_error(error, code)
    if code in (
        "INVALID_KEY", "KEY_REVOKED",
        "SUBSCRIPTION_EXPIRED", "SUBSCRIPTION_INACTIVE", "FREE_TIER_EXPIRED",
    ):
        return f'{localized}. <a href="{DASHBOARD_ERROR_URL}">{tr("Check your dashboard")}</a>'
    if code == "TRIAL_EXHAUSTED":
        config = get_server_config()
        dashboard = config.get("upgrade_url", get_dashboard_url())
        return f'{localized}. <a href="{dashboard}">{tr("Subscribe")}</a>'
    if code == "DEVICE_LIMIT_EXCEEDED":
        return f'{localized} <a href="{DASHBOARD_ERROR_URL}">{tr("Manage your computers")}</a>'
    if code == "PROXY_ERROR":
        return f"{localized}. {tr('Check QGIS proxy settings: Settings > Options > Network')}"
    if code == "SSL_ERROR":
        return f"{localized}. {tr('If you are on a corporate network, ask your IT team about SSL inspection settings')}"
    if code in ("DNS_ERROR", "NO_NETWORK"):
        return f"{localized}. {tr('Check your internet connection')}"
    if code == "TIMEOUT":
        return f"{localized}. {tr('Try again, or check your internet speed')}"
    if code == "CONNECTION_REFUSED":
        return f"{localized}. {tr('The service may be temporarily unavailable')}"
    if code == "AUTH_ERROR":
        return f'{localized}. <a href="{DASHBOARD_ERROR_URL}">{tr("Check your dashboard")}</a>'
    return localized


# Substrings that mark a generation that the model couldn't produce (no image,
# safety block, server catch-all "couldn't complete"). The server marks these
# jobs failed and auto-refunds the credit, so they are a user-facing outcome,
# not a bug. Matched on the message because the failed-status response carries
# no specific error code (status route returns {status, error} only).
_MODEL_FAILURE_HINTS = (
    "no image",
    "no_image",
    "finish_reason",
    "no candidates",
    "block",
    "safety",
    "couldn't complete",
    "could not complete",
    "rephrasing your prompt",
    "no credit was charged",
)

_NON_MODEL_FAILURE_CODES = NETWORK_ERROR_CODES | frozenset({
    "QUOTA_EXCEEDED",
    "LIMIT_REACHED",
    "USAGE_LIMIT_REACHED",
    "MONTHLY_LIMIT_REACHED",
    "TRIAL_EXHAUSTED",
    "NO_KEY",
    "INVALID_KEY",
    "KEY_REVOKED",
    "AUTH_LOCKED",
    "SUBSCRIPTION_EXPIRED",
    "SUBSCRIPTION_INACTIVE",
    "FREE_TIER_EXPIRED",
    "AUTH_ERROR",
    "GENERATION_CANCELLED",
})


def _is_model_failure(message: str, normalized_code: str) -> bool:
    """True when the model couldn't produce an image (no-output / safety block /
    server catch-all). Network / auth / quota / cancel codes are excluded so
    only genuine generation failures (which the server auto-refunds) match."""
    if normalized_code in _NON_MODEL_FAILURE_CODES:
        return False
    text = (message or "").lower()
    return any(needle in text for needle in _MODEL_FAILURE_HINTS)


# Transient capacity errors: the image service is momentarily overloaded. The
# user was not charged (the server refunds), so show a calm "try again" message
# inline rather than the bug-report dialog.
_BUSY_HINTS = (
    "resource exhausted",
    "resource_exhausted",
    "too many requests",
    "rate limit",
    "rate-limit",
    "service is busy",
    "busy right now",
    "try again in a moment",
)


def _is_service_busy(message: str, normalized_code: str) -> bool:
    if normalized_code in {"RATE_LIMITED", "RESOURCE_EXHAUSTED", "PROVIDER_BUSY", "SERVICE_BUSY"}:
        return True
    text = (message or "").lower()
    return any(needle in text for needle in _BUSY_HINTS)


def _scrub_paths(text: str) -> str:
    """Strip usernames from any /Users/<name>, \\Users\\<name>, or /home/<name> path."""
    import re
    return re.sub(r"(?i)([/\\](?:Users|home)[/\\])[^/\\]+", r"\1***", text or "")


def _failure_stage(normalized_code: str) -> str:
    """Map an error code to the pipeline stage it failed at."""
    if normalized_code == "DOWNLOAD_FAILED":
        return "download"
    if normalized_code == "WRITE_ERROR":
        return "write"
    if normalized_code in {
        "NO_NETWORK", "DNS_ERROR", "SSL_ERROR", "TIMEOUT", "PROXY_ERROR",
        "CONNECTION_REFUSED", "TOO_LARGE", "BAD_REQUEST",
    }:
        return "submit"
    return "poll"


def _resolve_class_label(
    vector_color: str | None, vector_classes: list[dict] | None
) -> str:
    """Look up the semantic class name for ``vector_color`` in the multi-class
    catalog. Empty string when the template only declares a color, leaving
    the field blank in the table for the user to fill in (autocomplete kicks
    in after the first value is typed).
    """
    if not vector_color or not isinstance(vector_classes, list):
        return ""
    target = vector_color.upper().lstrip("#")
    for entry in vector_classes:
        if not isinstance(entry, dict):
            continue
        color = (entry.get("color") or "").upper().lstrip("#")
        if color and color == target:
            label = entry.get("label")
            if isinstance(label, str):
                return label.strip()
    return ""


class AIEditPlugin:
    """Main QGIS plugin class. Orchestrates all tiers."""

    def __init__(self, iface):
        self._iface = iface
        self._canvas = iface.mapCanvas()
        self._dock_widget = None
        self._map_tool = None
        self._swipe_controller = None
        self._action = None
        self._settings_action = None
        self._selected_extent = None
        self._worker = None
        # Off-thread canvas exporter. Built fresh per click in _on_generate so
        # the heavy render+PNG-encode doesn't freeze the UI.
        self._export_worker: ExportWorker | None = None
        # Stash data captured on the main thread that the export-completed
        # callback needs to chain into the GenerationWorker. Cleared after use.
        self._pending_generation: dict | None = None
        self._selection_rubber_band = None
        self._selection_rubber_band_halo = None
        # Onboarding tile-warm-up watchers (see _start_imagery_gate).
        self._imagery_settle_timer = None
        self._imagery_cap_timer = None
        self._previous_map_tool = None
        self._terralab_toolbar = None
        self._export_config_loader = None
        self._credits_loader = None
        self._catalog_loader = None
        # Startup config bootstrap task; cancelled in unload like the loaders.
        self._bootstrap_task = None
        # Holds in-flight history actions (add-to-map / download) so the task
        # isn't garbage-collected mid-run.
        self._history_tasks: list = []
        # Preserved for retry (re-generate from original, not from AI result)
        self._last_image_b64 = None
        self._last_guidance_b64 = None
        self._last_guidance_format = None
        self._last_input_format = None
        self._last_input_bytes = None
        self._last_extent_dict = None
        self._last_crs_wkt = None
        self._last_aspect_ratio = None
        self._last_suggested_res = None
        # Iteration anchor: sent as parent_request_id on the next submit.
        self._last_completed_request_id: str | None = None
        # Version lineage for the strip. Index 0 is the Original (layer_id /
        # request_id None); each generation appends a record. Reset on a new
        # zone. The selected index picks the export base + parent_request_id.
        self._versions: list[dict] = []
        self._selected_version_index = 0
        # Iteration session id: shared by every generation in one continuous
        # flow on a zone. Minted on a fresh zone, re-entered on restore. Groups
        # versions in history and rebuilds the strip. None until the first zone.
        self._session_id: str | None = None
        self._key_validation_worker = None
        # One-click connect: polls the server for the browser handoff.
        self._pairing_worker = None
        # Skip /usage round-trips while rapidly toggling the dock (10/60s rate limit).
        self._last_key_validation_unix: float = 0.0
        # Mark up state. Lazy: manager is created on first entry.
        self._markup_manager: MarkupLayerManager | None = None
        self._markup_tool_objs: dict[str, object] = {}
        self._pre_markup_map_tool = None
        # Re-entrancy guard for the markup Done/close path (capture pumps events).
        self._markup_done_in_progress = False
        # Throttle for the "draw inside the zone" notice so repeated out-of-zone
        # strokes don't stack message bars.
        self._markup_outside_notice_active = False
        # Stored (layer_id, color_hex, class_label) of the last generation so
        # the canvas Vectorize pill can open the panel pre-filled, mirroring
        # the dock CTA. None when the last run was not a detection template.
        self._vectorize_suggestion: tuple[str, str | None, str] | None = None
        # Whether the canvas action pills (×/Compare/Vectorize) belong on the
        # canvas right now. Tool panels (Vectorize / Mark up) hide them while
        # open; this flag lets the panel-exit path bring them back.
        self._pills_armed = False
        # Markup undo runs via a main-window event filter (more reliable
        # than QShortcut, which loses key races against QGIS's own Ctrl+Z).
        self._markup_event_filter: _MarkupUndoFilter | None = None
        # While Markup is open we disable any main-window QAction bound to
        # Ctrl/Cmd+Z so QGIS's project undo doesn't swallow the keystroke
        # before the map tool's keyPressEvent sees it. Restored on exit.
        self._suppressed_undo_actions: list[tuple[QAction, bool]] = []
        # Global "Launch AI Edit" shortcut. Cross-platform via QKeySequence
        # (Cmd+Alt+E on macOS, Ctrl+Alt+E on Windows/Linux).
        self._launch_shortcut: QShortcut | None = None
        self._in_tool_panel: str | None = None
        # True only while _toggle_dock is programmatically hiding the dock, so
        # the visibilityChanged handler can tell a non-destructive toolbar
        # toggle from a real close (title-bar X) and skip the teardown.
        self._toggling_dock = False
        self._selection_tool_was_active = False
        # Startup network (bootstrap) is deferred until the dock is first shown,
        # so a user who never opens AI Edit this session makes zero network
        # calls. Once-guarded so toggling the dock never refires it.
        self._startup_bootstrap_done = False
        # One transient "no connection" notice per startup episode (anti-spam).
        self._connectivity_notice_shown = False
        # Fires once per QGIS session, on the first dock-open. Lifecycle event,
        # ships without explicit consent (no PII).
        self._plugin_opened_emitted = False
        # Cached cohort props enriched onto every generation event.
        self._first_generation_milestone_emitted = False
        # Carried from generation_started to the terminal event so completed /
        # failed can report is_retry and used_markup without re-deriving them.
        self._last_generation_is_retry = False
        self._last_generation_used_markup = False

        # Initialize tiers
        self._dev_mode = False
        self._skip_trial_check = False
        # ConfigStore owns server config, cached activation config, telemetry
        # collector. Wired so unload + Plugin Reloader leave no globals behind.
        self._config_store = ConfigStore()
        set_store(self._config_store)
        self._client = self._create_client()
        self._auth_manager = AuthManager(self._client)
        self._generation_service = GenerationService(self._client)
        self._reference_store = ReferenceImageStore()

    @property
    def auth_manager(self):
        return self._auth_manager

    @property
    def generation_service(self):
        return self._generation_service

    @property
    def client(self):
        return self._client

    def _create_client(self):
        """Create TerraLabClient. Reads TERRALAB_BASE_URL from .env.local."""
        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(plugin_dir, ".env.local")
        env_vars: dict = {}
        try:
            if os.path.isfile(env_path):
                env_vars = self._load_env_file(env_path)
        except OSError as err:
            # Network shares or USB drives can vanish mid-read; defaults are
            # safe (dev_mode and skip_trial_check stay False).
            log_warning(f".env.local read failed, using defaults: {err}")
        self._dev_mode = env_vars.get("DEBUG", "").lower() == "true"
        self._skip_trial_check = env_vars.get("SKIP_TRIAL_CHECK", "").lower() == "true"
        if env_vars.get("RAW_PROMPT", "").lower() == "true":
            log_warning("DEV MODE: RAW_PROMPT is active - prompts sent bare (team allowlist enforced server side)")
        return TerraLabClient(env_vars=env_vars)

    @staticmethod
    def _load_env_file(path: str) -> dict:
        """Parse a simple KEY=VALUE env file (ignores comments and blank lines)."""
        env: dict = {}
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        env[key.strip()] = value.strip().strip('"').strip("'")
        except OSError as err:
            log_warning(f".env.local read failed: {err}")
        return env

    @staticmethod
    def _read_plugin_version() -> str:
        """Read plugin version from metadata.txt."""
        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        metadata_path = os.path.join(plugin_dir, "metadata.txt")
        try:
            with open(metadata_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("version="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            pass
        return "unknown"

    def initGui(self):
        # Idempotent; defers silently if auth DB is locked.
        try:
            migrate_legacy_key()
        except Exception as err:  # nosec B110
            log_warning(f"Auth migration raised: {err}")

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        icon_path = os.path.join(plugin_dir, "resources", "icons", "icon.png")

        from .terralab_menu import (
            _UTILITY_SEPARATOR,
            add_plugin_to_menu,
            add_to_plugins_menu,
            get_or_create_terralab_menu,
        )

        main_window = self._iface.mainWindow()
        self._terralab_menu = get_or_create_terralab_menu(main_window)

        self._action = QAction(
            QIcon(icon_path) if os.path.exists(icon_path) else QIcon(),
            tr("AI Edit"),
            main_window,
        )
        self._action.setToolTip(tr("AI Edit by TerraLab\nAI-powered image editing for geospatial data"))
        self._action.triggered.connect(self._toggle_dock)
        add_plugin_to_menu(self._terralab_menu, self._action, "ai-edit")

        from .terralab_toolbar import (
            add_action_to_toolbar,
            get_or_create_terralab_toolbar,
        )

        self._terralab_toolbar = get_or_create_terralab_toolbar(self._iface)
        add_action_to_toolbar(self._terralab_toolbar, self._action, "ai-edit")

        add_to_plugins_menu(self._iface, self._action)

        # Cross-plugin discovery: show AI Segmentation entry (#47).
        from .cross_plugin_discovery import make_ai_seg_action
        ai_seg_icon_path = os.path.join(plugin_dir, "resources", "icons", "ai_segmentation_icon.png")
        ai_seg_icon = QIcon(ai_seg_icon_path) if os.path.exists(ai_seg_icon_path) else None
        self._ai_seg_action = make_ai_seg_action(
            main_window,
            self._iface,
            tr("AI Segmentation"),
            tr("Segment elements on raster images using AI (opens AI Segmentation plugin)"),
            icon=ai_seg_icon,
        )
        add_action_to_toolbar(self._terralab_toolbar, self._ai_seg_action, "ai-segmentation", is_cross_promo=True)
        add_plugin_to_menu(
            self._terralab_menu, self._ai_seg_action, "ai-segmentation", is_cross_promo=True
        )
        add_to_plugins_menu(self._iface, self._ai_seg_action)

        # Add "Settings" to the TerraLab menu utility section
        settings_icon = QIcon(":/images/themes/default/mActionOptions.svg")
        self._settings_action = QAction(settings_icon, tr("Settings"), main_window)
        self._settings_action.setObjectName("_terralab_settings_action")
        # Prevent macOS from moving this to the app menu (Cocoa treats "Settings" as Preferences)
        self._settings_action.setMenuRole(QAction.MenuRole.NoRole)
        self._settings_action.triggered.connect(self._on_settings_clicked)
        # Insert before "Check for Updates" (first action after the separator)
        insert_before = None
        found_sep = False
        for a in self._terralab_menu.actions():
            if a.objectName() == _UTILITY_SEPARATOR:
                found_sep = True
                continue
            if found_sep:
                insert_before = a
                break
        if insert_before:
            self._terralab_menu.insertAction(insert_before, self._settings_action)
        else:
            self._terralab_menu.addAction(self._settings_action)

        # Create dock widget and register it with QGIS (hidden by default)
        self._dock_widget = AIEditDockWidget(
            self._iface.mainWindow(), reference_store=self._reference_store
        )
        self._dock_widget.set_library_dependencies(self._client, self._auth_manager)
        # Defer stale catalog read off initGui so startup never blocks on disk.

        def _load_stale_catalog():
            try:
                from ..core.prompts.prompt_presets_client import read_cached_catalog_stale_ok

                if self._dock_widget is not None:
                    self._dock_widget.set_server_catalog(read_cached_catalog_stale_ok())
            except Exception as err:  # noqa: BLE001
                log_warning(f"Server catalog stale read failed: {err}")
                if self._dock_widget is not None:
                    self._dock_widget.set_server_catalog(None)
        QTimer.singleShot(0, _load_stale_catalog)
        # Fresh catalog arrives via the startup bootstrap bundle (initGui);
        # library opens trigger _load_server_catalog refetches afterwards.
        self._iface.addDockWidget(QtC.RightDockWidgetArea, self._dock_widget)
        # Auto-open the panel on first install and after every upgrade (new version),
        # but never on a routine launch. Same-version launches let QGIS restore the dock
        # to the state the user left it in (open/closed + position), via its objectName.
        settings = QSettings()
        current_version = self._read_plugin_version()
        last_shown_version = settings.value("AIEdit/dock_shown_version", "", type=str)
        auto_open_source = None
        if last_shown_version != current_version:
            settings.setValue("AIEdit/dock_shown_version", current_version)
            auto_open_source = "auto_install" if not last_shown_version else "auto_upgrade"
            self._dock_widget.show()
            self._dock_widget.raise_()
            self._ensure_dock_height()
        # Check for a newer plugin version once QGIS has fetched repo metadata.
        # That cache is often empty just after startup, so retry on a backoff.
        self._update_check_done = False
        self._update_check_delays = [5000, 30000, 60000, 120000]
        self._update_check_index = 0
        # Parent the timer to the dock so it can't fire into a torn-down plugin
        # after unload (and doesn't retain the plugin in the global event loop).
        QtC.safe_single_shot(
            self._update_check_delays[0], self._dock_widget, self._check_for_plugin_update
        )
        self._dock_widget.stop_clicked.connect(self._on_stop)
        self._dock_widget.generate_clicked.connect(self._on_generate)
        self._dock_widget.retry_clicked.connect(self._on_retry)
        self._dock_widget.base_version_selected.connect(self._on_base_version_selected)
        self._dock_widget.template_selected.connect(self._on_template_selected)
        self._dock_widget.catalog_refresh_requested.connect(self._load_server_catalog)
        self._dock_widget.history_add_to_map.connect(self._on_history_add_to_map)
        self._dock_widget.history_download.connect(self._on_history_download)
        self._dock_widget.history_restore.connect(self._on_history_restore)
        self._dock_widget.pairing_requested.connect(self._on_pairing_requested)
        self._dock_widget.pairing_cancel_requested.connect(self._on_cancel_pairing)
        self._dock_widget.settings_clicked.connect(self._on_settings_clicked)
        self._dock_widget.launch_clicked.connect(self._on_launch_clicked)
        self._dock_widget.try_example_requested.connect(self._on_try_example)
        self._dock_widget.exit_clicked.connect(self._on_exit_clicked)
        self._dock_widget.zone_clear_requested.connect(self._on_zone_delete_requested)
        self._dock_widget.markup_clicked.connect(self._on_markup_clicked)
        self._dock_widget.markup_done_clicked.connect(self._on_markup_done_clicked)
        self._dock_widget.markup_clear_clicked.connect(self._on_markup_clear_clicked)
        self._dock_widget.markup_tool_changed.connect(self._on_markup_tool_changed)
        self._dock_widget.markup_color_changed.connect(self._on_markup_color_changed)
        self._dock_widget.vectorize_clicked.connect(self._on_vectorize_clicked)
        self._dock_widget.vectorize_done_clicked.connect(self._on_vectorize_done_clicked)
        self._dock_widget.vectorize_suggestion_clicked.connect(
            self._on_vectorize_suggestion_clicked
        )
        # Before/After swipe: toggle-based, no dock panel. The footer
        # button toggle drives the SwipeController; the controller signals
        # back so the button visual + enable state stays in sync.
        from .panels.swipe_panel import SwipeController
        # parent must be a QObject; AIEditPlugin is a plain Python class.
        # The controller has no natural Qt parent, so we own its lifecycle
        # explicitly via cleanup() in unload().
        self._swipe_controller = SwipeController(None)
        self._dock_widget.swipe_toggled.connect(self._on_swipe_toggled)
        self._swipe_controller.activated.connect(self._on_swipe_armed)
        self._swipe_controller.deactivated.connect(self._on_swipe_disarmed)
        self._swipe_controller.eligibility_changed.connect(
            self._dock_widget.set_swipe_button_enabled
        )
        # Opening the Help menu disarms any active swipe, same rule as
        # the other AI Edit actions: only one tool owns the canvas at a
        # time so the user is never left guessing which mode they are in.
        self._dock_widget.help_menu_open_changed.connect(self._on_help_menu_open_changed)
        # Seed the initial enable state from the layer that's active right
        # now (currentLayerChanged only fires on subsequent changes).
        self._dock_widget.set_swipe_button_enabled(
            self._swipe_controller.can_swipe_now()
        )
        # Title-bar X close doesn't go through _toggle_dock, so listen for the
        # underlying visibility change to keep the map tool / cursor in sync.
        self._dock_widget.visibilityChanged.connect(self._on_dock_visibility_changed)

        # Global launch shortcut: Ctrl+Alt+E on Win/Linux, Cmd+Alt+E (⌥⌘E)
        # on macOS. WindowShortcut scope fires from anywhere inside QGIS
        # without us having to focus a particular widget first.
        self._launch_shortcut = QShortcut(
            QKeySequence(LAUNCH_SHORTCUT),
            self._iface.mainWindow(),
        )
        self._launch_shortcut.setContext(QtC.WindowShortcut)
        self._launch_shortcut.activated.connect(self._on_launch_shortcut)

        # Create map tool
        self._map_tool = RectangleSelectionTool(self._canvas)
        self._map_tool.selection_made.connect(self._on_zone_selected)
        self._map_tool.zone_too_small.connect(self._on_zone_too_small)
        self._map_tool.zone_invalid.connect(self._on_zone_invalid)
        self._map_tool.zone_delete_requested.connect(self._on_zone_delete_requested)
        self._map_tool.compare_requested.connect(self._on_canvas_compare)
        self._map_tool.vectorize_requested.connect(self._on_canvas_vectorize)

        # Restore saved activation key. UI only here (optimistic activated or
        # sign-up screen); the network half rides the single bootstrap call
        # below instead of three separate startup requests.
        self._check_activation_state(validate=False)
        if not self._auth_manager.has_activation_key():
            telemetry.track(te.ACTIVATION_SCREEN_VIEWED)

        from .dialogs.error_report_dialog import start_log_collector

        start_log_collector()

        # Initialize telemetry (respects consent + auth, non-blocking)
        telemetry.init_telemetry(
            self._client, self._auth_manager, self._read_plugin_version()
        )
        # Dock auto-opened on install/upgrade never went through _toggle_dock,
        # so emit the open here to stop undercounting sessions.
        if auto_open_source is not None and not self._plugin_opened_emitted:
            self._plugin_opened_emitted = True
            telemetry.track(te.PLUGIN_OPENED, {"open_source": auto_open_source})
            telemetry.flush()

        # Startup network is deferred until the dock is actually shown (see
        # _maybe_bootstrap_on_show), so a user who never opens AI Edit makes no
        # network calls. The auto-open branch above shows the dock BEFORE
        # visibilityChanged is connected, so fire it explicitly here when the
        # dock is already visible; the once-guard makes the later signal a no-op.
        # A QGIS-restored-open dock becomes visible after initGui, which the
        # visibilityChanged handler then catches.
        if self._dock_widget.isVisible():
            self._maybe_bootstrap_on_show()

        if self._dev_mode:
            log("AI Edit plugin loaded [DEV MODE]")
        else:
            log("AI Edit plugin loaded")
        if self._skip_trial_check:
            log_warning("DEV MODE: SKIP_TRIAL_CHECK is active - auth checks bypassed")

    def unload(self):
        """Called by QGIS when plugin is unloaded."""
        # Make any pending plugin-update-check timer a no-op (belt-and-suspenders
        # alongside parenting it to the dock).
        self._update_check_done = True
        # Tear down any in-flight onboarding tile-warm-up watcher (disconnects
        # the canvas signal so it can't fire against a torn-down dock).
        self._finish_imagery_gate()
        # Stop generation task. QgsTaskManager owns the task lifecycle, so we
        # request cancellation and drop our reference; the framework drains
        # the run() loop and emits taskTerminated on the main thread.
        if self._worker is not None and self._worker.is_active():
            self._generation_service.cancel()
            for sig in [self._worker.succeeded, self._worker.progress, self._worker.failed]:
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            try:
                self._worker.cancel()
            except Exception:  # nosec B110
                pass
        self._worker = None

        # Same drain for the canvas-export worker. Drop the pending hand-off
        # so a late completed-signal doesn't try to kick a GenerationWorker
        # against a torn-down dock.
        self._pending_generation = None
        if self._export_worker is not None and self._export_worker.is_active():
            for sig in [self._export_worker.completed, self._export_worker.failed]:
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            try:
                self._export_worker.cancel()
            except Exception:  # nosec B110
                pass
        self._export_worker = None

        # Stop background loader QgsTasks. We disconnect signals (slots
        # would land on a dying dock) then cancel; the task manager drains
        # the run() loop and disposes of the task itself.
        for loader in [
            self._export_config_loader,
            self._credits_loader,
            self._key_validation_worker,
            self._pairing_worker,
            self._catalog_loader,
            self._bootstrap_task,
        ]:
            if loader is None:
                continue
            try:
                loader.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                loader.cancel()
            except Exception:  # nosec B110
                pass
        self._export_config_loader = None
        self._credits_loader = None
        self._key_validation_worker = None
        self._pairing_worker = None
        self._catalog_loader = None
        self._bootstrap_task = None

        # Drain in-flight history tasks (add-to-map, download, reference reload).
        # Their succeeded/failed slots touch self._canvas / self._iface, which
        # are stale after unload, so disconnect then cancel before teardown.
        for task in list(self._history_tasks):
            try:
                task.succeeded.disconnect()
                task.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                task.cancel()
            except Exception:  # nosec B110
                pass
        self._history_tasks.clear()

        self._clear_selection_rectangle()

        # Detach any Mark up tool we set on the canvas before our objects vanish.
        if self._markup_tool_objs:
            current = self._canvas.mapTool() if self._canvas else None
            if current in self._markup_tool_objs.values():
                self._canvas.unsetMapTool(current)
            self._markup_tool_objs.clear()
        self._clear_markup_layer()
        if self._markup_manager is not None:
            self._markup_manager.disconnect_signals()
        self._markup_manager = None
        self._pre_markup_map_tool = None
        if self._markup_event_filter is not None:
            try:
                self._iface.mainWindow().removeEventFilter(self._markup_event_filter)
            except RuntimeError:
                pass
            self._markup_event_filter = None
        self._restore_qgis_undo()
        if self._launch_shortcut is not None:
            try:
                self._launch_shortcut.setEnabled(False)
                self._launch_shortcut.deleteLater()
            except RuntimeError:
                pass
            self._launch_shortcut = None

        if self._swipe_controller is not None:
            try:
                self._swipe_controller.cleanup()
            except RuntimeError:
                pass
            self._swipe_controller = None

        if self._dock_widget:
            # Disconnect QgsProject signals before the dock is destroyed.
            # closeEvent used to do this but firing on every hide also broke
            # the dock when the user re-opened it from the Panels menu.
            try:
                self._dock_widget.cleanup()
            except Exception as err:  # nosec B110
                log_warning(f"Dock cleanup failed: {err}")
            self._iface.removeDockWidget(self._dock_widget)
            self._dock_widget.deleteLater()
            self._dock_widget = None

        # Wipe session-scoped reference images from disk.
        try:
            self._reference_store.cleanup()
        except Exception as err:  # nosec B110
            log_warning(f"Reference store cleanup failed: {err}")

        from .dialogs.error_report_dialog import stop_log_collector

        stop_log_collector()

        if self._settings_action and self._terralab_menu:
            self._terralab_menu.removeAction(self._settings_action)
            self._settings_action = None

        if self._action:
            from .terralab_menu import remove_from_plugins_menu, remove_plugin_from_menu

            remove_from_plugins_menu(self._iface, self._action)
            remove_plugin_from_menu(
                self._terralab_menu, self._action, self._iface.mainWindow()
            )

            ai_seg_action = getattr(self, "_ai_seg_action", None)
            if ai_seg_action is not None:
                try:
                    remove_from_plugins_menu(self._iface, ai_seg_action)
                except (RuntimeError, AttributeError):
                    pass
                try:
                    remove_plugin_from_menu(
                        self._terralab_menu, ai_seg_action, self._iface.mainWindow())
                except (RuntimeError, AttributeError):
                    pass

            from .terralab_toolbar import remove_action_from_toolbar

            if self._terralab_toolbar:
                try:
                    remove_action_from_toolbar(
                        self._terralab_toolbar, self._action, self._iface.mainWindow()
                    )
                except (RuntimeError, AttributeError):
                    pass
                if ai_seg_action is not None:
                    try:
                        remove_action_from_toolbar(
                            self._terralab_toolbar, ai_seg_action, self._iface.mainWindow()
                        )
                    except (RuntimeError, AttributeError):
                        pass
                self._terralab_toolbar = None

            self._action = None
            self._ai_seg_action = None
            self._terralab_menu = None

        if self._map_tool is not None:
            # Detach the selection tool from the canvas before we drop it, or
            # QgsMapCanvas keeps pointing at a torn-down tool and the next click
            # after a reload dispatches into freed state. Mirrors the markup
            # unset above.
            try:
                if self._canvas is not None and self._canvas.mapTool() is self._map_tool:
                    self._canvas.unsetMapTool(self._map_tool)
            except RuntimeError:  # nosec B110 - C++ canvas already gone
                pass
            try:
                self._map_tool.cleanup()
            except Exception as err:  # nosec B110
                log_warning(f"Map tool cleanup failed: {err}")
        self._map_tool = None
        clear_config_cache()
        # Cancel any in-flight telemetry flush tasks before tearing down the
        # store so QgsTaskManager doesn't outlive the collector.
        telemetry.shutdown_telemetry()
        if self._config_store is not None:
            self._config_store.clear()
        set_store(None)
        log("AI Edit plugin unloaded")

    @staticmethod
    def _days_since_activation() -> int | None:
        """Cohort prop. None for legacy users without a stored timestamp."""
        raw = QSettings().value("AIEdit/activation_timestamp_unix", "", type=str)
        if not raw:
            return None
        try:
            ts = int(raw)
        except (TypeError, ValueError):
            return None
        delta = int((time.time() - ts) // 86400)
        return max(delta, 0)

    def _enrich_generation_props(self, base: dict) -> dict:
        enriched = {
            **base,
            "context_image_count": self._reference_store.count(),
            "context_total_size_bytes": self._reference_store.total_size_bytes(),
        }
        days = self._days_since_activation()
        if days is not None:
            enriched["days_since_activation"] = days
        return enriched

    def _maybe_emit_first_generation_milestone(self):
        """One-shot event when the user completes their first successful generation.

        Persisted via QSettings so it never re-fires across QGIS sessions.
        """
        if self._first_generation_milestone_emitted:
            return
        settings = QSettings()
        already = settings.value("AIEdit/first_generation_milestone_fired", False, type=bool)
        if already:
            self._first_generation_milestone_emitted = True
            return
        days = self._days_since_activation()
        props = {}
        if days is not None:
            props["days_since_activation"] = days
        telemetry.track(te.FIRST_GENERATION_MILESTONE, props)
        telemetry.flush()
        settings.setValue("AIEdit/first_generation_milestone_fired", True)
        # Force-flush so a crash doesn't lose the flag and re-fire the milestone.
        try:
            settings.sync()
        except Exception:  # nosec B110
            pass
        self._first_generation_milestone_emitted = True

    def _toggle_dock(self):
        if self._dock_widget.isVisible():
            # A toolbar toggle is a pure, non-destructive hide: it must preserve
            # everything (in-progress generation, selected zone, tool panel) so
            # re-opening restores the exact state. The guard tells the
            # visibilityChanged handler this is a toggle, not a real close, so it
            # skips the teardown that cancels the generation. The selection tool
            # is the one thing we stand down while hidden, so a stray map click
            # can't draw a zone behind the user's back; it returns on re-open.
            self._selection_tool_was_active = self._canvas.mapTool() is self._map_tool
            if self._selection_tool_was_active:
                self._deactivate_selection_tool()
            self._toggling_dock = True
            try:
                self._dock_widget.hide()
            finally:
                self._toggling_dock = False
            log_debug("Dock hidden (toggle)")
        else:
            # On the FIRST open the deferred bootstrap (fired by show() below via
            # visibilityChanged) already validates the key + fetches credits, so
            # skip this call to avoid a duplicate /usage. Later opens use it for
            # the cheap 900s-guarded revalidation.
            if self._startup_bootstrap_done:
                self._check_activation_state()
            self._dock_widget.show()
            self._dock_widget.raise_()
            self._ensure_dock_height()
            # Restore the selection tool if it was active when we toggled away.
            if self._selection_tool_was_active:
                self._activate_selection_tool()
                self._selection_tool_was_active = False
            if not self._plugin_opened_emitted:
                self._plugin_opened_emitted = True
                telemetry.track(te.PLUGIN_OPENED, {"open_source": "manual"})
                telemetry.flush()
            log_debug("Dock shown")

    def _ensure_dock_height(self):
        """Open AI Edit tall enough to actually work in. QGIS can dock it as a
        short box; grow it to most of the window height. Never shrinks a dock
        the user has already made taller. Deferred one tick so the resize runs
        after QGIS finishes laying the dock out."""
        def _apply():
            try:
                dock = self._dock_widget
                mw = self._iface.mainWindow()
                if dock is None or mw is None or not dock.isVisible():
                    return
                target = int(mw.height() * 0.85)
                if dock.height() >= target:
                    return
                mw.resizeDocks([dock], [target], Qt.Orientation.Vertical)
            except Exception as err:  # nosec B110 - sizing is best-effort.
                log_debug(f"Dock height adjust skipped: {err}")
        QTimer.singleShot(0, _apply)

    def _check_activation_state(self, validate: bool = True):
        """Check activation key presence and validate with server.

        ``validate=False`` does only the synchronous UI half (optimistic
        activated view or sign-up screen); the caller provides the server
        confirmation through another channel (the startup bootstrap bundle).
        """
        settings = QSettings()
        saved_key = get_activation_key(settings)
        if not saved_key:
            clear_activation(settings)
            self._auth_manager.set_activation_key("")
            self._dock_widget.set_activated(False)
            self._settings_action.setEnabled(False)
            self._last_key_validation_unix = 0.0
            return

        self._auth_manager.set_activation_key(saved_key)

        # Revalidation window. The server enforces auth on every real request
        # anyway, so this client-side check is purely cosmetic (which screen to
        # show); 30s made every dock toggle cost two /usage calls and was the
        # single largest source of API traffic (10x the generation volume).
        if (time.time() - self._last_key_validation_unix) < 900:
            self._dock_widget.set_activated(True)
            self._settings_action.setEnabled(True)
            # Stay on LAUNCH state; tool is activated on user click.
            return

        # Optimistic activation: a saved key implies the user is already
        # signed up. Show the activated UI immediately and disable
        # Launch until the async credit check confirms; otherwise the
        # sign-up screen flashes for half a second on every reload.
        self._dock_widget.set_activated(True)
        self._settings_action.setEnabled(True)
        self._dock_widget.set_launch_enabled(False)

        if not validate:
            return

        self._key_validation_worker = GenericRequestTask(
            "AI Edit key validation",
            lambda c=self._client, k=saved_key: _key_validation_request(c, k),
            silent=True,
        )
        self._key_validation_worker.succeeded.connect(self._on_key_valid)
        self._key_validation_worker.failed.connect(self._on_key_invalid)
        QgsApplication.taskManager().addTask(self._key_validation_worker)

    def _on_key_valid(self, usage=None):
        """Server confirmed the key is valid. The validation call IS a /usage
        fetch, so its payload feeds the credits display directly instead of
        firing a second, identical request."""
        # Connection works again: re-arm the one-shot connectivity notice.
        self._connectivity_notice_shown = False
        self._last_key_validation_unix = time.time()
        self._dock_widget.set_activated(True)
        self._settings_action.setEnabled(True)
        # Stay on LAUNCH state; tool is activated on user click.
        if isinstance(usage, dict) and "images_used" in usage:
            self._auth_manager.seed_usage(usage)
            self._on_credits_loaded(usage)
        else:
            self._dock_widget.set_checking_credits(True)
            self._refresh_credits()

    def _on_key_invalid(self, message: str, code: str):
        """Key validation came back negative.

        A genuine auth rejection (INVALID_KEY, SUBSCRIPTION_*, ...) signs the
        user out. A connectivity failure must NOT: clearing the key on a network
        blip would dump an offline user back to the sign-up screen and lose their
        stored key. On a network error we keep the optimistic-activated session
        (the server re-checks on every real call) and show one quiet notice.
        """
        if (code or "").strip().upper() in NETWORK_ERROR_CODES:
            self._dock_widget.set_activated(True)
            self._settings_action.setEnabled(True)
            # The optimistic path disabled Launch pending this check; restore it
            # so an offline user can still open the tool from a cached session.
            self._dock_widget.set_launch_enabled(True)
            self._show_connectivity_notice(code)
            return
        self._last_key_validation_unix = 0.0
        clear_activation()
        self._auth_manager.set_activation_key("")
        self._dock_widget.set_activated(False)
        self._dock_widget.set_activation_message(message, is_error=True)
        self._settings_action.setEnabled(False)

    def _on_settings_clicked(self):
        """Open the Account Settings dialog."""
        if not self._auth_manager.has_activation_key():
            return
        self._disarm_swipe()
        from .dialogs.account_settings_dialog import AccountSettingsDialog

        dlg = AccountSettingsDialog(
            client=self._client,
            auth=self._auth_manager.get_auth_header(),
            activation_key=self._auth_manager.get_activation_key(),
            parent=self._iface.mainWindow(),
        )
        dlg.sign_out_requested.connect(self._on_sign_out)
        self._dock_widget.set_settings_button_active(True)
        try:
            dlg.exec()
        finally:
            self._dock_widget.set_settings_button_active(False)

    def _load_export_config(self):
        """Fetch export config from server in background thread."""
        self._export_config_loader = GenericRequestTask(
            "AI Edit export config",
            self._client.get_export_config,
            silent=True,
        )
        self._export_config_loader.succeeded.connect(self._on_export_config_loaded)
        self._export_config_loader.failed.connect(
            lambda msg, code: self._on_export_config_failed(f"Server error: {msg}")
        )
        QgsApplication.taskManager().addTask(self._export_config_loader)

    def _maybe_bootstrap_on_show(self):
        """Run the startup bundle the first time the dock is shown this session.

        Once-guarded so toggling the dock open/closed never refires a network
        storm. Deferring here (instead of initGui) means an idle install makes
        no network calls at all.
        """
        if self._startup_bootstrap_done:
            return
        self._startup_bootstrap_done = True
        self._bootstrap_startup()

    def _bootstrap_startup(self):
        """One background call for export config + preset catalog + key
        validation/credits. Replaces three separate startup requests; falls
        back to the legacy loaders when the server predates /bootstrap."""
        auth = self._auth_manager.get_auth_header()
        task = GenericRequestTask(
            "AI Edit bootstrap",
            lambda c=self._client, a=auth: c.get_bootstrap(a),
            silent=True,
        )
        task.succeeded.connect(self._on_bootstrap_loaded)
        task.failed.connect(lambda _msg, _code: self._bootstrap_fallback())
        self._bootstrap_task = task
        QgsApplication.taskManager().addTask(task)

    def _on_bootstrap_loaded(self, payload):
        if not isinstance(payload, dict) or "export_config" not in payload:
            self._bootstrap_fallback()
            return
        config = payload.get("export_config")
        if isinstance(config, dict):
            self._on_export_config_loaded(config)
        catalog_payload = payload.get("catalog")
        if isinstance(catalog_payload, dict):
            try:
                from ..core.prompts.prompt_presets_client import store_catalog

                catalog = store_catalog(catalog_payload)
                if catalog is not None:
                    self._last_catalog_fetch_unix = time.time()
                    self._on_server_catalog_loaded(catalog)
            except Exception as err:  # nosec B110
                log_warning(f"Bootstrap catalog handling failed: {err}")
        usage = payload.get("usage")
        if isinstance(usage, dict) and "error" in usage:
            self._on_key_invalid(
                str(usage.get("error", "")), str(usage.get("code", ""))
            )
        elif isinstance(usage, dict):
            self._on_key_valid(usage)
        # usage None = signed-out startup; the sign-up screen is already shown.

    def _bootstrap_fallback(self):
        """Older server without /bootstrap: run the three legacy loaders."""
        log_debug("Bootstrap unavailable; using individual startup requests")
        self._load_export_config()
        self._load_server_catalog()
        if self._auth_manager.has_activation_key():
            self._check_activation_state()

    def _load_server_catalog(self):
        """Fetch the AI Edit preset catalog in the background and hand it to
        the dock when ready. Failures are silent - the stale cache or the
        local fallback covers the dialog in the meantime.

        `force_refresh=True` is the stale-while-revalidate move: the dock
        already shows the stale cache synchronously for instant UX, and this
        background worker always re-hits the server so the user gets the
        latest catalog within seconds. Without force_refresh, fetch would
        short-circuit on cache hit and the user could stay on a stale catalog
        until the TTL expired (painful right after a server-side push)."""
        # Every library open lands here; a catalog fetched less than a minute
        # ago is plenty fresh, so skip the refetch instead of re-hitting the
        # server on each open.
        now = time.time()
        if now - getattr(self, "_last_catalog_fetch_unix", 0.0) < 60.0:
            return
        self._last_catalog_fetch_unix = now
        self._catalog_loader = GenericRequestTask(
            "AI Edit preset catalog",
            lambda c=self._client: _server_catalog_request(c, force_refresh=True),
            silent=True,
        )
        self._catalog_loader.succeeded.connect(self._on_server_catalog_loaded)
        self._catalog_loader.failed.connect(lambda _msg, _code: self._on_server_catalog_failed())
        QgsApplication.taskManager().addTask(self._catalog_loader)

    def _on_server_catalog_loaded(self, catalog: dict):
        if self._dock_widget is not None:
            self._dock_widget.set_server_catalog(catalog)

    def _on_server_catalog_failed(self):
        log_debug("Server catalog: background fetch failed (stale or local fallback in effect)")

    def _on_export_config_loaded(self, config):
        """Set global export config from server response."""
        # Connection works again: re-arm the one-shot connectivity notice.
        self._connectivity_notice_shown = False
        set_server_config(config)
        costs = config.get("resolution_credit_costs", {})
        if self._dock_widget:
            self._dock_widget.set_resolution_credit_costs(costs)

    def _on_export_config_failed(self, error_message: str):
        """Handle export config loading failure (fallback path)."""
        log_warning(f"Export config failed to load: {error_message}")
        self._show_connectivity_notice()

    def _show_connectivity_notice(self, code: str = "") -> None:
        """Show ONE transient, dismissible 'no connection' notice per startup
        episode. Non-blocking (message bar), deduped so the three fallback
        loaders (config + catalog + key validation) never stack notices. The
        flag resets on any successful startup fetch so a later real outage can
        notify again."""
        if self._connectivity_notice_shown:
            return
        self._connectivity_notice_shown = True
        from qgis.core import Qgis
        self._notify(
            tr("AI Edit could not reach the server. Some features need an internet connection."),
            level=Qgis.MessageLevel.Warning,
            duration=8,
        )
        if self._dock_widget:
            detail = _localize_server_error("", code) or tr("No internet connection.")
            self._dock_widget.set_status(detail, is_error=True)

    def _activate_selection_tool(self):
        """Activate selection tool. Preserves any existing zone."""
        if self._canvas.mapTool() != self._map_tool:
            current_tool = self._canvas.mapTool()
            if current_tool:
                self._previous_map_tool = current_tool
            self._canvas.setMapTool(self._map_tool)
        self._dock_widget.set_status("")

    def _deactivate_selection_tool(self):
        """Restore the map tool that was active before selection started."""
        if self._previous_map_tool:
            try:
                self._canvas.setMapTool(self._previous_map_tool)
            except RuntimeError:
                pass
        self._previous_map_tool = None

    def _on_stop(self):
        """Dock closing mid-generation: cancel work and clear zone state.

        Triggered by the dock's closeEvent (title-bar X). The Exit button has
        its own handler that also returns the dock to LAUNCH - see
        _on_exit_clicked.
        """
        if self._worker is not None and self._worker.is_active():
            duration = time.time() - getattr(self, "_generation_start_time", time.time())
            telemetry.track(te.GENERATION_CANCELLED, self._enrich_generation_props({
                "duration_ms": int(duration * 1000),
                "resolution": getattr(self, "_last_suggested_res", ""),
            }))
            telemetry.flush()
            self._generation_service.cancel()
            # Cancel the task too, not just the service. Otherwise finished()
            # sees isCanceled()==False and emits a stale "Generation cancelled"
            # error into the reset UI (plus a spurious generation_failed event).
            try:
                self._worker.cancel()
            except Exception:  # nosec B110
                pass
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._last_image_b64 = None
        self._last_guidance_b64 = None
        self._last_guidance_format = None
        if self._map_tool:
            self._map_tool.set_has_zone(False)
        self._deactivate_selection_tool()

    def _on_launch_shortcut(self):
        """Global shortcut: open the dock if hidden, then start a new edit."""
        if self._dock_widget is None:
            return
        if not self._dock_widget.isVisible():
            self._dock_widget.setVisible(True)
            self._ensure_dock_height()
        self._dock_widget.raise_()
        self._on_launch_clicked()

    def _on_launch_clicked(self):
        """User clicked 'Launch AI Edit' on the entry screen."""
        telemetry.track(te.LAUNCH_CLICKED)
        self._disarm_swipe()
        self._activate_selection_tool()
        self._dock_widget.set_selecting_zone_state()

    def _on_dock_visibility_changed(self, visible: bool):
        if visible:
            # First real show (toolbar, launch shortcut, or QGIS restoring the
            # dock open at launch) is what kicks off the deferred startup network.
            self._maybe_bootstrap_on_show()
            return
        # A toolbar toggle hide is non-destructive (see _toggle_dock): preserve
        # the in-progress generation and all dock state so re-opening restores
        # it. Only a real close (title-bar X) runs the teardown below.
        if self._toggling_dock:
            return
        # Closing the dock from inside a tool panel (Mark up / Vectorize)
        # must reset to the base view; otherwise the next open stacks the
        # main widget on top of the still-visible tool panel (issue #164).
        # Done before the mid-generation early-return so it always runs.
        if self._in_tool_panel is not None:
            self._exit_tool_panel()
        # Mid-generation: cancel through _on_stop so refund + state reset run together.
        if self._worker is not None and self._worker.is_active():
            self._on_stop()
            return
        self._deactivate_selection_tool()
        self._clear_selection_rectangle()
        self._selected_extent = None
        if self._map_tool:
            self._map_tool.set_has_zone(False)
        # Disarm swipe: without the dock the toggle is unreachable.
        if self._swipe_controller is not None and self._swipe_controller.is_active():
            self._swipe_controller.stop()

    def _check_for_plugin_update(self):
        """Poll QGIS's plugin metadata for a newer version, retrying on a backoff.

        Early-returns if the dock was unloaded so a stale QTimer fire can't crash.
        """
        if not self._dock_widget or self._update_check_done:
            return
        if self._dock_widget.check_for_updates():
            self._update_check_done = True
            return
        self._update_check_index += 1
        if self._update_check_index < len(self._update_check_delays):
            delay = self._update_check_delays[self._update_check_index]
            QtC.safe_single_shot(delay, self._dock_widget, self._check_for_plugin_update)

    def _on_exit_clicked(self):
        """User clicked Exit / Done: cancel work and return to LAUNCH."""
        self._disarm_swipe()
        self._pills_armed = False
        if self._worker is not None and self._worker.is_active():
            duration = time.time() - getattr(self, "_generation_start_time", time.time())
            telemetry.track(te.GENERATION_CANCELLED, self._enrich_generation_props({
                "duration_ms": int(duration * 1000),
                "resolution": getattr(self, "_last_suggested_res", ""),
            }))
            telemetry.flush()
            self._generation_service.cancel()
            # Cancel the task too, not just the service. Otherwise finished()
            # sees isCanceled()==False and emits a stale "Generation cancelled"
            # error into the reset UI (plus a spurious generation_failed event).
            try:
                self._worker.cancel()
            except Exception:  # nosec B110
                pass
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._last_image_b64 = None
        self._last_guidance_b64 = None
        self._last_guidance_format = None
        if self._map_tool:
            self._map_tool.set_has_zone(False)
        self._deactivate_selection_tool()
        # Mark up annotations persist across sessions on a single shared layer.
        # User wipes them explicitly via the Clear all button.
        self._dock_widget.set_launch_state()

    def _on_retry(self, prompt: str):
        """Retry on same zone: re-export the current canvas view (includes generated layers)."""
        if not self._selected_extent:
            self._dock_widget.set_status(
                tr("Cannot retry: no zone selected."), is_error=True
            )
            return
        self._on_generate(prompt)

    def _on_base_version_selected(self, index: int):
        """A version tile was clicked: mirror it on the canvas.

        Show only the selected version's layer among the AI results (Original
        hides them all so the clean map shows through); the rest stay hidden.
        The active Mark up layer is user guidance, not an AI edit, so it is left
        untouched. Compare / Vectorize pills act on the AI result, so they drop
        when Original is selected and return for any generated version.
        """
        if 0 <= index < len(self._versions):
            self._selected_version_index = index
        sel_layer_id = self._versions[index]["layer_id"] if 0 <= index < len(self._versions) else None
        is_original = sel_layer_id is None
        try:
            self._sync_canvas_to_version(sel_layer_id)

            if self._map_tool is not None:
                if is_original:
                    self._pills_armed = False
                    self._disarm_swipe()
                    self._map_tool.hide_action_badges()
                else:
                    self._pills_armed = True
                    self._show_action_pills()
        except Exception as err:
            log_warning(f"version-select layer visibility sync failed: {err}")

    def _sync_canvas_to_version(self, sel_layer_id: str | None) -> None:
        """Show only ``sel_layer_id`` among the AI-Edit layers, hide the rest.

        ``None`` (Original) hides every AI result so the clean map shows
        through. The active Mark up layer is user guidance, not an AI edit, so it
        is kept checked. Mirrors the export base on the canvas and sets the
        selected version active so the pills act on it."""
        from qgis.core import QgsProject

        from .layer_groups import set_ai_edit_layers_checked

        except_ids: set[str] = set()
        markup_id = self._markup_layer_id_if_any()
        if markup_id:
            except_ids.add(markup_id)
        # Hide all AI-Edit layers (other versions, vectorize overlays) but keep
        # markup, then re-check only the selected version.
        set_ai_edit_layers_checked(False, except_ids=except_ids)
        if sel_layer_id:
            root = QgsProject.instance().layerTreeRoot()
            node = root.findLayer(sel_layer_id)
            if node is not None:
                node.setItemVisibilityChecked(True)
            layer = QgsProject.instance().mapLayer(sel_layer_id)
            if layer is not None:
                try:
                    self._iface.setActiveLayer(layer)
                except Exception as err:  # nosec B110
                    log_warning(f"setActiveLayer on version select failed: {err}")

    def _markup_layer_id_if_any(self) -> str | None:
        """Return the active Mark up layer id, or None when there is no markup."""
        if self._markup_manager is None or self._markup_manager.annotation_count() <= 0:
            return None
        try:
            markup_layer = self._markup_manager.layer()
            return markup_layer.id() if markup_layer is not None else None
        except RuntimeError:
            return None

    def _on_template_selected(self, template_id: str, template_name: str = ""):
        """Track template selection for analytics."""
        props = {"template_id": template_id}
        if template_name:
            props["template_name"] = template_name
        telemetry.track(te.TEMPLATE_SELECTED, props)

    # --- Past-generation actions (from the prompt library Recent/Favorites) ---

    def _notify(self, text: str, level=None, duration: int = 5):
        """Show a transient message in the QGIS message bar."""
        from qgis.core import Qgis

        if level is None:
            level = Qgis.MessageLevel.Info
        try:
            self._iface.messageBar().pushMessage("AI Edit", text, level=level, duration=duration)
        except Exception as err:  # nosec B110
            log_warning(f"messageBar push failed: {err}")

    def _hold_history_task(self, task):
        """Keep a hard ref (QgsTask GC'd mid-run aborts QGIS) and release it
        once the task settles."""
        self._history_tasks.append(task)
        task.succeeded.connect(lambda *_: self._release_history_task(task))
        task.failed.connect(lambda *_: self._release_history_task(task))
        QgsApplication.taskManager().addTask(task)

    def _release_history_task(self, task):
        if task in self._history_tasks:
            self._history_tasks.remove(task)

    def _on_history_add_to_map(self, job: dict):
        """Re-add a past generation's output as a georeferenced layer.

        Reconstructs the geotransform from the stored location, downloads the
        output, writes a GeoTIFF off-thread, then adds the layer on the main
        thread."""
        output_url = job.get("output_url")
        if not output_url:
            self._notify(tr("This generation's image is no longer available."), duration=4)
            return
        geo = extent_and_crs_from_job(job)
        if geo is None:
            self._notify(tr("Location data unavailable for this generation."), duration=4)
            return
        extent_dict, crs_wkt = geo
        prompt = job.get("prompt") or ""
        output_dir = get_output_dir()

        def _work(url=output_url, ed=extent_dict, wkt=crs_wkt, p=prompt, d=output_dir):
            from .raster_writer import write_geotiff

            data = self._client.download_image(url)
            path = write_geotiff(data, ed, wkt, d, prompt=p)
            return {"path": path, "prompt": p, "crs_wkt": wkt}

        task = GenericRequestTask(tr("Adding past generation to the map"), _work)
        task.succeeded.connect(self._on_history_layer_ready)
        task.failed.connect(
            lambda msg, _code: self._notify(
                tr("Could not add to map: {msg}").format(msg=msg), duration=6
            )
        )
        self._notify(tr("Adding to map..."), duration=2)
        self._hold_history_task(task)

    def _on_history_layer_ready(self, result):
        from qgis.core import Qgis

        path = (result or {}).get("path")
        if not path:
            return
        try:
            layer = add_geotiff_to_project(
                path,
                (result or {}).get("prompt", ""),
                crs_wkt=(result or {}).get("crs_wkt", ""),
            )
        except Exception as err:  # noqa: BLE001
            self._notify(tr("Could not add layer: {msg}").format(msg=err), duration=6)
            return
        if layer is not None:
            try:
                self._iface.setActiveLayer(layer)
                self._canvas.setExtent(layer.extent())
                self._canvas.refresh()
            except Exception as err:  # nosec B110
                log_warning(f"focus added history layer failed: {err}")
        self._notify(tr("Added to map."), level=Qgis.MessageLevel.Success, duration=4)

    def _on_history_download(self, job: dict):
        """Save a past generation to a file the user picks. When location data
        exists, write a georeferenced GeoTIFF (so it drops into the right place
        in any QGIS project); otherwise save the raw image. ``download_side``
        on the job selects the input (captured zone) or the output (result)."""
        from qgis.PyQt.QtWidgets import QFileDialog

        from .raster_writer import _slugify

        side = job.get("download_side") or "output"
        output_url = job.get("input_url") if side == "input" else job.get("output_url")
        if not output_url:
            self._notify(tr("This generation's image is no longer available."), duration=4)
            return
        base_slug = _slugify(job.get("prompt") or "") or "ai_edit"
        slug = f"{base_slug}_{side}"
        geo = extent_and_crs_from_job(job)

        if geo is not None:
            extent_dict, crs_wkt = geo
            prompt = job.get("prompt") or ""
            default_name = os.path.join(get_output_dir(), f"{slug}.tif")
            dest, _filter = QFileDialog.getSaveFileName(
                self._iface.mainWindow(),
                tr("Save georeferenced GeoTIFF"),
                default_name,
                tr("GeoTIFF (*.tif)"),
            )
            if not dest:
                return

            def _work(url=output_url, ed=extent_dict, wkt=crs_wkt, p=prompt, path=dest):
                import shutil
                import tempfile

                from .raster_writer import write_geotiff

                data = self._client.download_image(url)
                tmp_dir = tempfile.mkdtemp(prefix="ai_edit_dl_")
                try:
                    produced = write_geotiff(data, ed, wkt, tmp_dir, prompt=p)
                    if os.path.exists(path):
                        os.remove(path)
                    shutil.move(produced, path)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                return {"path": path}
        else:
            default_name = os.path.join(get_output_dir(), f"{slug}.png")
            dest, _filter = QFileDialog.getSaveFileName(
                self._iface.mainWindow(),
                tr("Save generation image"),
                default_name,
                tr("Images (*.png *.jpg *.webp);;All files (*)"),
            )
            if not dest:
                return

            def _work(url=output_url, path=dest):
                data = self._client.download_image(url)
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
                return {"path": path}

        task = GenericRequestTask(tr("Downloading generation"), _work)
        task.succeeded.connect(self._on_history_download_done)
        task.failed.connect(
            lambda msg, _code: self._notify(
                tr("Download failed: {msg}").format(msg=msg), duration=6
            )
        )
        self._hold_history_task(task)

    def _on_history_download_done(self, result):
        from qgis.core import Qgis

        path = (result or {}).get("path", "")
        self._notify(
            tr("Saved to {path}").format(path=path),
            level=Qgis.MessageLevel.Success,
            duration=5,
        )

    def _on_history_restore(self, job: dict):
        """Reproduce a past generation: restore the zone at its original spot,
        refill the prompt, and reload its reference image(s) so the user can
        re-run on the same location."""
        if self._dock_widget is None:
            return
        geo = extent_and_crs_from_job(job)
        if geo is None:
            self._notify(tr("Location data unavailable for this generation."), duration=4)
            return
        extent_dict, crs_wkt = geo
        if not self._restore_zone(extent_dict, crs_wkt):
            return
        # Re-enter the restored generation's session so new edits continue it
        # and group with its siblings. _restore_zone reset the lineage and
        # minted a fresh id above; keep that fresh one for legacy jobs that
        # carry no session.
        if job.get("session_id"):
            self._session_id = job.get("session_id")
        # Reuse means "replace what I have now", so wipe the current prompt
        # (restore_generation_context overwrites it) and any reference images
        # before loading the reused generation's own references.
        self._dock_widget.clear_references()
        self._dock_widget.restore_generation_context(
            job.get("prompt") or "",
            job.get("template_id"),
            job.get("template_name"),
        )
        self._load_reference_images(job.get("reference_image_urls") or [])
        # Rebuild the iteration session this generation belongs to (Original,
        # V1, V2...) so the next edit continues the chain instead of starting
        # a blank lineage. Thumbnails arrive async; the strip appears then.
        self._restore_session_chain(job)
        self._notify(tr("Generation restored. Adjust and generate again."), duration=4)

    def _session_chain_for(self, job: dict) -> list[dict]:
        """All cached generations from `job`'s iteration session, oldest first.

        Keys on the client-minted session_id, so it groups chained iterations
        AND multi-model siblings the user made in one flow on a zone. Falls back
        to just `job` when it carries no session (legacy rows / older plugins)."""
        if not job.get("request_id"):
            return []
        try:
            jobs = self._dock_widget.get_cached_recent_jobs()
        except Exception:  # nosec B110 - cache is best-effort.
            jobs = []
        return session_jobs_for(job, jobs)

    def _restore_session_chain(self, job: dict) -> None:
        """Download the chain's thumbnails off-thread, then seed the version
        strip with the whole session."""
        chain = self._session_chain_for(job)
        if not chain or self._client is None:
            return
        self._pending_session_rid = job.get("request_id")
        urls = [chain[0].get("input_thumb_url") or chain[0].get("input_url")]
        urls += [j.get("output_thumb_url") or j.get("output_url") for j in chain]

        def _work(items=tuple(urls)):
            blobs = []
            for url in items:
                blob = None
                if url:
                    try:
                        blob = self._client.download_image(url)
                    except Exception as err:  # noqa: BLE001
                        log_warning(f"session thumb download failed: {err}")
                blobs.append(blob)
            return {"blobs": blobs}

        restored_rid = job.get("request_id")
        task = GenericRequestTask(tr("Loading session"), _work)
        task.succeeded.connect(
            lambda payload, c=chain, rid=restored_rid:
            self._on_session_thumbs_loaded(c, rid, payload)
        )
        task.failed.connect(
            lambda msg, _code: log_warning(f"session restore failed: {msg}")
        )
        self._hold_history_task(task)

    def _on_session_thumbs_loaded(
        self, chain: list, selected_rid: str | None, payload: dict
    ) -> None:
        if self._dock_widget is None:
            return
        # Stale arrival: the user restored something else since, or drew a new
        # zone (which invalidates the token). Never overwrite a live session.
        if selected_rid != getattr(self, "_pending_session_rid", None):
            return
        # The user already started generating: the export seeded the lineage.
        if self._versions:
            return
        blobs = payload.get("blobs") or []
        if len(blobs) != len(chain) + 1:
            return
        self._versions = [{"layer_id": None, "request_id": None, "prompt": ""}]
        self._dock_widget.seed_version_strip(self._pixmap_from_blob(blobs[0]))
        for j, blob in zip(chain, blobs[1:]):
            dims = None
            if j.get("output_w") and j.get("output_h"):
                dims = f"{j['output_w']} × {j['output_h']}"
            meta = {
                "definition": j.get("resolution") or "",
                "dimensions": dims,
                "template_name": j.get("template_name"),
                "base_label": None,
            }
            self._versions.append({
                "layer_id": None,
                "request_id": j.get("request_id"),
                "prompt": j.get("prompt") or "",
            })
            self._dock_widget.add_version_thumb(
                self._pixmap_from_blob(blob), j.get("prompt") or "", meta
            )
        index = next(
            (i for i, v in enumerate(self._versions) if v["request_id"] == selected_rid),
            len(self._versions) - 1,
        )
        self._selected_version_index = index
        self._dock_widget.select_version(index)
        self._dock_widget.reveal_version_strip()

    @staticmethod
    def _pixmap_from_blob(blob):
        from qgis.PyQt.QtGui import QPixmap

        pixmap = QPixmap()
        if blob:
            try:
                pixmap.loadFromData(blob)
            except Exception:  # nosec B110 - a broken thumb shows as blank.
                pixmap = QPixmap()
        return pixmap

    def _restore_zone(self, extent_dict: dict, crs_wkt: str) -> bool:
        """Recreate the selection zone from a stored extent + CRS so a past
        generation can be reproduced on the exact same spot. Returns True on
        success."""
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsProject,
        )

        from ..core.errors import AIEditError
        from .canvas_exporter import validate_zone

        src_crs = QgsCoordinateReferenceSystem()
        src_crs.createFromWkt(crs_wkt)
        if not src_crs.isValid():
            self._notify(tr("Location data unavailable for this generation."), duration=4)
            return False
        rect = QgsRectangle(
            float(extent_dict["xmin"]),
            float(extent_dict["ymin"]),
            float(extent_dict["xmax"]),
            float(extent_dict["ymax"]),
        )
        canvas_crs = self._canvas.mapSettings().destinationCrs()
        if src_crs != canvas_crs:
            try:
                xform = QgsCoordinateTransform(src_crs, canvas_crs, QgsProject.instance())
                rect = xform.transformBoundingBox(rect)
            except Exception as err:  # noqa: BLE001
                log_warning(f"restore zone transform failed: {err}")
                self._notify(tr("Could not place the zone on the current map."), duration=4)
                return False
        try:
            validate_zone(rect, canvas_crs, self._canvas.rotation())
        except AIEditError as err:
            self._notify(err.message, duration=5)
            return False
        except Exception:  # nosec B110 - validation is best-effort here.
            pass

        self._selected_extent = rect
        self._last_completed_request_id = None
        self._reset_version_lineage()
        self._show_selection_rectangle(rect)
        if self._map_tool is not None:
            self._map_tool.set_zone(rect)
        self._activate_selection_tool()
        self._dock_widget.set_zone_selected()
        try:
            self._dock_widget.set_reference_target_extent(QgsRectangle(rect), canvas_crs)
        except Exception:  # nosec B110 - alignment is best-effort.
            pass
        try:
            zoom = QgsRectangle(rect)
            zoom.scale(1.15)
            self._canvas.setExtent(zoom)
            self._canvas.refresh()
        except Exception as err:  # nosec B110
            log_warning(f"zoom to restored zone failed: {err}")
        return True

    # --- Empty-canvas onboarding ("Try it on an example") --------------------

    def _on_try_example(self):
        """Empty-canvas one-click onboarding. Drop a satellite basemap and, on a
        blank project, frame a known demo scene, pre-draw an example zone and
        pre-fill a land-cover prompt so the only remaining step is Generate. On
        a project that already has layers, only add a global backdrop and leave
        the user's view and inputs untouched."""
        from qgis.core import QgsProject

        was_empty = len(QgsProject.instance().mapLayers()) == 0
        layer = self._add_backdrop_layer(demo=was_empty)
        ok = layer is not None
        if ok and was_empty:
            # Defer past QGIS's zoom-to-first-layer (queued during addMapLayer),
            # which would otherwise snap to the whole world and undo our framing.
            QTimer.singleShot(0, self._prime_demo_scene)
        elif not ok:
            self._dock_widget.show_basemap_error()
        telemetry.track(te.BASEMAP_CTA_CLICKED, {"success": ok})
        telemetry.flush()

    def _add_backdrop_layer(self, demo: bool):
        """Add a satellite basemap at the bottom of the layer tree (AI Edit
        outputs stack above it). On the demo path prefer IGN's sharp France
        ortho, falling back to global Esri if it won't load; otherwise use Esri
        directly. Returns the layer, or None if nothing loaded."""
        from qgis.core import QgsProject, QgsRasterLayer

        layer = None
        source = ""
        if demo:
            candidate = QgsRasterLayer(_IGN_ORTHO_URI, "Orthophoto (IGN)", "wms")
            if candidate.isValid():
                layer, source = candidate, "ign"
        if layer is None:
            candidate = QgsRasterLayer(_ESRI_WORLD_IMAGERY_URI, "Satellite (Esri)", "wms")
            if candidate.isValid():
                layer, source = candidate, "esri"
        if layer is None:
            log_warning("onboarding basemap: no source loaded")
            return None
        project = QgsProject.instance()
        project.addMapLayer(layer, False)
        project.layerTreeRoot().insertLayer(-1, layer)
        log(f"onboarding basemap added (source={source})")
        return layer

    def _prime_demo_scene(self):
        """Frame the demo scene, pre-draw the example zone and pre-fill the
        land-cover prompt, then hold Generate until the tiles have painted."""
        from qgis.core import QgsCoordinateReferenceSystem

        wgs84_wkt = QgsCoordinateReferenceSystem("EPSG:4326").toWkt()
        # _restore_zone frames (zoom to zone x1.15), draws the rubber band and
        # flips the dock into ZONE_SELECTED - exactly the demo framing we want.
        if not self._restore_zone(dict(_DEMO_ZONE_WGS84), wgs84_wkt):
            return
        preset = get_preset_by_id(_DEMO_PRESET_ID)
        if preset:
            self._dock_widget.prime_prompt_from_preset(preset)
        self._start_imagery_gate()

    def _start_imagery_gate(self):
        """Hold Generate while the online basemap's tiles warm. Online providers
        fetch tiles async and repaint as they arrive, so exporting now would
        ship a blank input (a crop error). We debounce mapCanvasRefreshed (tiles
        settled once refreshes stop) with a hard cap so a slow or offline
        network never traps the user."""
        if self._dock_widget is None or self._canvas is None:
            return
        self._dock_widget.set_imagery_loading(True)
        # Any failure while arming the watchers must release the gate, or
        # Generate stays stuck on "Loading imagery…" forever.
        try:
            # Parent to the dock (a QObject); the plugin instance is not a
            # QObject, and an unparented QTimer would be at risk of GC.
            self._imagery_settle_timer = QTimer(self._dock_widget)
            self._imagery_settle_timer.setSingleShot(True)
            self._imagery_settle_timer.timeout.connect(self._finish_imagery_gate)
            self._imagery_cap_timer = QTimer(self._dock_widget)
            self._imagery_cap_timer.setSingleShot(True)
            self._imagery_cap_timer.timeout.connect(self._finish_imagery_gate)
            self._canvas.mapCanvasRefreshed.connect(self._on_imagery_refresh)
            self._imagery_cap_timer.start(8000)
            self._imagery_settle_timer.start(1200)
        except Exception as err:  # noqa: BLE001 - release rather than trap Generate.
            log_warning(f"imagery gate setup failed, releasing: {err}")
            self._finish_imagery_gate()

    def _on_imagery_refresh(self):
        """Each finished render restarts the quiet window; when tiles stop
        arriving the window elapses and the gate lifts."""
        if self._imagery_settle_timer is not None:
            self._imagery_settle_timer.start(1200)

    def _finish_imagery_gate(self):
        """Release Generate and tear down the warm-up watchers (idempotent)."""
        for attr in ("_imagery_settle_timer", "_imagery_cap_timer"):
            timer = getattr(self, attr, None)
            if timer is not None:
                timer.stop()
                setattr(self, attr, None)
        if self._canvas is not None:
            try:
                self._canvas.mapCanvasRefreshed.disconnect(self._on_imagery_refresh)
            except (TypeError, RuntimeError):
                pass  # nosec B110 - already disconnected.
        if self._dock_widget is not None:
            self._dock_widget.set_imagery_loading(False)

    def _load_reference_images(self, urls: list):
        """Download a past generation's reference images off-thread, then inject
        them into the dock's reference strip."""
        urls = [u for u in urls if u]
        if not urls or self._client is None:
            return

        def _work(items=tuple(urls)):
            blobs = []
            for url in items:
                try:
                    blobs.append(self._client.download_image(url))
                except Exception as err:  # noqa: BLE001
                    log_warning(f"reference image download failed: {err}")
                    blobs.append(None)
            return {"blobs": blobs}

        task = GenericRequestTask(tr("Loading reference images"), _work)
        task.succeeded.connect(self._on_reference_images_loaded)
        task.failed.connect(
            lambda msg, _code: log_warning(f"reference reload failed: {msg}")
        )
        self._hold_history_task(task)

    def _on_reference_images_loaded(self, result):
        from qgis.PyQt.QtCore import QByteArray
        from qgis.PyQt.QtGui import QImage

        blobs = (result or {}).get("blobs") or []
        items = []
        for i, data in enumerate(blobs):
            if not data:
                continue
            img = QImage()
            if img.loadFromData(QByteArray(data)):
                items.append((img, f"reference_{i + 1}"))
        if items and self._dock_widget is not None:
            self._dock_widget.restore_reference_images(items)

    def _run_generation_from_stored(self, prompt: str):
        """Run generation using previously stored zone data (for retry)."""
        if self._worker is not None and self._worker.is_active():
            self._dock_widget.set_status(tr("Generation already in progress"), is_error=True)
            return

        if not has_consent():
            save_consent()
            self._dock_widget.hide_consent()

        if not has_server_config():
            self._dock_widget.set_status(
                tr(
                    "Cannot generate: export config not loaded from server. "
                    "Check your internet connection and restart QGIS."
                ),
                is_error=True,
            )
            return

        # Update resolution from selector (user may have changed it on retry)
        if not self._dock_widget._is_free_tier:
            self._last_suggested_res = self._dock_widget.get_selected_resolution()

        ctx = PipelineContext()
        ctx.aspect_ratio = self._last_aspect_ratio
        # Reuse the original export's encoded bytes, so the upload must be
        # labeled with the same format the canvas was encoded as.
        ctx.input_format = self._last_input_format
        # Retry on the same zone = iteration. Anchor on the previous result's
        # original input so the model keeps style coherence.
        ctx.parent_request_id = self._last_completed_request_id
        # Same continuous flow on this zone = same session.
        ctx.session_id = self._session_id

        # Armed template wins over text match so user edits keep vector hints.
        armed = self._dock_widget.get_active_template()
        match = armed or lookup_template_by_prompt(prompt)
        if match:
            ctx.template_id, ctx.template_name = match
            ctx.vector_color, ctx.vector_classes = get_vector_hints(ctx.template_id)
        else:
            # No preset matched. Still light up the Vectorize CTA when the
            # free-form prompt asks to segment, detect, or vectorize one
            # feature type without naming colors (server paints #FF0000).
            ctx.vector_color = detect_freeform_vector_intent(prompt)

        output_dir = get_output_dir()

        # Show the zone rectangle during retry
        if self._selected_extent:
            self._show_selection_rectangle(self._selected_extent)

        if self._map_tool:
            self._map_tool.set_locked(True)
        self._dock_widget.set_generating(True)
        self._dock_widget.set_status("")
        self._generation_service.reset()
        self._generation_start_time = time.time()
        self._last_generation_is_retry = True
        self._last_generation_used_markup = bool(self._last_guidance_b64)
        telemetry.track(te.GENERATION_STARTED, self._enrich_generation_props({
            "prompt_length": len(prompt),
            "aspect_ratio": self._last_aspect_ratio or "",
            "resolution": self._last_suggested_res or "",
            "input_image_bytes": self._last_input_bytes,
            "input_image_format": self._last_input_format,
            "is_retry": True,
            "has_geo_context": self._reference_store.count() > 0,
            "template_id": ctx.template_id,
            "template_name": ctx.template_name,
            "used_template": bool(ctx.template_id),
            "used_markup": bool(self._last_guidance_b64),
        }))
        log_debug(f"Retry generation: prompt_len={len(prompt)}")

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

        self._worker = GenerationWorker(
            client=self._client,
            auth_manager=self._auth_manager,
            service=self._generation_service,
            image_b64=self._last_image_b64,
            prompt=prompt,
            aspect_ratio=self._last_aspect_ratio or "",
            extent_dict=self._last_extent_dict,
            crs_wkt=self._last_crs_wkt or "",
            output_dir=output_dir,
            ctx=ctx,
            debug_mode=self._dev_mode,
            plugin_dir=plugin_dir,
            skip_trial_check=self._skip_trial_check,
            suggested_resolution=self._last_suggested_res or "1K",
            context_images=self._reference_store.get_all_b64(),
            guidance_image=self._last_guidance_b64,
            guidance_format=self._last_guidance_format,
        )
        self._worker.succeeded.connect(self._on_generation_finished)
        self._worker.progress.connect(self._on_generation_progress)
        self._worker.failed.connect(self._on_generation_error)
        QgsApplication.taskManager().addTask(self._worker)

    def _on_zone_selected(self, extent: QgsRectangle):
        self._selected_extent = extent
        # Keep markup clipped to the new zone if the manager already exists.
        if self._markup_manager is not None:
            self._markup_manager.set_clip_zone(extent)
        # Fresh zone breaks the iteration chain (parent_request_id + armed template).
        self._last_completed_request_id = None
        self._reset_version_lineage()
        if self._dock_widget is not None:
            try:
                self._dock_widget.clear_active_template()
            except AttributeError:
                pass
            # Drop any Mark up reference baked at the previous zone extent so it
            # is not shipped as context for this new, differently-located zone.
            self._dock_widget.clear_markup_reference()
        self._show_selection_rectangle(extent)
        self._dock_widget.set_zone_selected()
        # Soft heads-up if the zone is so zoomed out the model won't resolve
        # small features. Best-effort: never blocks selection.
        try:
            from .canvas_exporter import estimate_native_ground_resolution_m
            gr = estimate_native_ground_resolution_m(
                self._canvas.mapSettings(), extent
            )
            self._dock_widget.set_zone_guidance(gr)
        except Exception:  # nosec B110 - advisory hint only.
            pass
        # Align reference renders to this zone so context layers line up with
        # the input image instead of the looser canvas view.
        try:
            zone_crs = self._canvas.mapSettings().destinationCrs()
            self._dock_widget.set_reference_target_extent(QgsRectangle(extent), zone_crs)
        except Exception:  # nosec B110 - alignment is best-effort, never blocks selection.
            pass
        # Captures the common case of drawing a zone without generating, which
        # would otherwise go unmeasured. Dimensions only, never coordinates.
        try:
            mupp = self._canvas.mapSettings().mapUnitsPerPixel()
            w_px = int(round(extent.width() / mupp)) if mupp else 0
            h_px = int(round(extent.height() / mupp)) if mupp else 0
            aspect_ratio = round(w_px / h_px, 3) if h_px else 0
            telemetry.track(te.ZONE_DRAWN, {
                "zone_width_px": w_px,
                "zone_height_px": h_px,
                "aspect_ratio": aspect_ratio,
            })
        except Exception:  # nosec B110 - telemetry must never block selection.
            pass
        log_debug("Zone selected")

    def _on_zone_too_small(self):
        try:
            canvas = self._canvas
            canvas_w = canvas.width() if canvas else 0
            min_pct = int(round(50 * 100 / canvas_w)) if canvas_w > 0 else 5
        except Exception:
            min_pct = 5
        self._dock_widget.set_status(
            tr(
                "Selected zone too small. Draw a rectangle at least "
                "{pct}% of the canvas size."
            ).format(pct=max(1, min_pct)),
            is_error=True,
        )

    def _on_zone_invalid(self, code: str, message: str):
        """Edge-zone refusal at draw time (antimeridian, polar, oversized,
        rotated map, invalid CRS). Surfaces a clear localized banner so the
        user can adjust before clicking Generate."""
        self._dock_widget.set_status(message, is_error=True)
        log_warning(f"Zone refused: {code} - {message}")

    def _on_zone_delete_requested(self):
        """Clear the current zone and return to the SELECTING_ZONE step.

        Triggered by right-click 'Clear zone', the badge button, or Escape
        from the prompt step. We keep the typed prompt and edit group so the
        user can redraw and continue iterating.
        """
        # The × pill doubles as the exit from a live comparison: stop the swipe
        # before tearing the zone down so the canvas is not left in swipe mode.
        self._disarm_swipe()
        self._pills_armed = False
        self._clear_selection_rectangle()
        self._selected_extent = None
        # Clearing the zone breaks the iteration chain.
        self._last_completed_request_id = None
        self._reset_version_lineage()
        if self._map_tool is not None:
            self._map_tool.set_has_zone(False)
        # The captured Mark up reference belongs to the zone we are clearing.
        if self._dock_widget is not None:
            self._dock_widget.clear_markup_reference()
        self._dock_widget.set_zone_cleared()
        log_debug("Zone cleared")

    def _reset_version_lineage(self) -> None:
        """Start a fresh lineage: empty the version list and clear the strip.

        A new zone (or Exit then a new zone) is a new session, so mint a fresh
        session id here. Restore overrides it afterwards to re-enter a session."""
        self._versions = []
        self._selected_version_index = 0
        self._session_id = uuid.uuid4().hex
        # Invalidate any in-flight session-restore download: its thumbnails
        # must not seed a lineage the user has since broken.
        self._pending_session_rid = None
        if self._dock_widget is not None:
            try:
                self._dock_widget.reset_version_strip()
            except AttributeError:
                pass

    def _pixmap_from_b64(self, image_b64: str | None) -> QPixmap | None:
        """Decode the export's base64 bytes into a pixmap for the Original tile."""
        if not image_b64:
            return None
        try:
            import base64

            pixmap = QPixmap()
            pixmap.loadFromData(base64.b64decode(image_b64))
            return pixmap if not pixmap.isNull() else None
        except Exception as err:  # nosec B110 - a missing thumb is non-fatal.
            log_warning(f"version thumb decode failed: {err}")
            return None

    def _render_layer_thumb(self, layer) -> QPixmap | None:
        """Render a result raster layer to a pixmap for its version tile."""
        try:
            from .layer_renderer import render_layers_to_qimage

            image = render_layers_to_qimage([layer])
            if image is None or image.isNull():
                return None
            return QPixmap.fromImage(image)
        except Exception as err:  # nosec B110 - a missing thumb is non-fatal.
            log_warning(f"version thumb render failed: {err}")
            return None

    # --- Mark up / Vectorize tool panels -------------------------------

    def _on_markup_clicked(self):
        """User picked Tools → Mark up. Swap the dock view and arm the canvas.

        Toggles: a second click on the footer Mark up icon while the panel is
        already open closes it (same as the in-panel Finish button).
        """
        self._disarm_swipe()
        if self._map_tool is not None:
            self._map_tool.hide_action_badges()

        if self._in_tool_panel == "markup":
            # Closing via the footer toggle must match the in-panel Finish
            # button: capture the marks as a reference and drop the layer.
            self._on_markup_done_clicked()
            return
        if self._markup_manager is None:
            self._markup_manager = MarkupLayerManager(self._canvas, self._dock_widget)
            self._markup_manager.annotation_count_changed.connect(
                self._dock_widget.set_markup_annotation_count
            )
            self._markup_manager.outside_zone_attempted.connect(
                self._on_markup_outside_zone
            )
        # Capture the current map tool so Done can restore it.
        current = self._canvas.mapTool()
        if current is not None and current not in self._markup_tool_objs.values():
            self._pre_markup_map_tool = current
        self._in_tool_panel = "markup"
        self._dock_widget.set_markup_state()
        self._dock_widget.set_markup_zone_present(self._selected_extent is not None)
        self._markup_manager.set_clip_zone(self._selected_extent)
        self._dock_widget.set_markup_annotation_count(
            self._markup_manager.annotation_count()
        )
        # Cmd/Ctrl+Z undo across all focus paths: suppress QGIS undo action,
        # map tool key handler (canvas focus), main-window event filter (dock focus).
        if self._markup_event_filter is None:
            self._markup_event_filter = _MarkupUndoFilter(self._on_markup_undo)
        self._iface.mainWindow().installEventFilter(self._markup_event_filter)
        self._suppress_qgis_undo()
        telemetry.track(te.MARKUP_OPENED)

    def _on_markup_tool_changed(self, tool_key: str):
        """User picked Pencil / Arrow / Circle in the Mark up panel."""
        if self._markup_manager is None:
            return
        existing = self._markup_tool_objs.get(tool_key)
        if existing is None:
            if tool_key == "pencil":
                existing = PencilMapTool(self._canvas, self._markup_manager)
            elif tool_key == "arrow":
                existing = ArrowMapTool(self._canvas, self._markup_manager)
            elif tool_key == "circle":
                existing = CircleMapTool(self._canvas, self._markup_manager)
            else:
                return
            self._markup_tool_objs[tool_key] = existing
        existing.set_color(self._dock_widget.get_markup_color())
        # Tell the selection tool to keep its zone state across this switch
        # so the rectangle outline + delete badge stay visible while the
        # user annotates. Without this hint deactivate() wipes them.
        if self._map_tool is not None and self._canvas.mapTool() is self._map_tool:
            self._map_tool.preserve_state_on_next_deactivate()
        self._canvas.setMapTool(existing)

    def _on_markup_color_changed(self, color: QColor):
        """User changed the annotation color - propagate to the active tool."""
        for tool in self._markup_tool_objs.values():
            tool.set_color(color)

    def _on_markup_clear_clicked(self):
        if self._markup_manager is not None:
            self._markup_manager.clear_all()
        # "Clear all" wipes the markup entirely: current strokes and the saved
        # reference thumbnail, so nothing markup-related is left behind.
        self._dock_widget.clear_markup_reference()

    def _on_markup_undo(self):
        if self._in_tool_panel == "markup" and self._markup_manager is not None:
            self._markup_manager.undo_last()

    def _on_markup_outside_zone(self):
        """Yellow notice when the user draws outside the selected zone. Marks
        only count inside the zone, so an out-of-zone stroke is dropped."""
        if self._markup_outside_notice_active:
            return
        self._markup_outside_notice_active = True
        try:
            self._iface.messageBar().pushMessage(
                "AI Edit",
                tr("You can only draw inside the selected zone."),
                level=Qgis.MessageLevel.Warning,
                duration=3,
            )
        except Exception:  # nosec B110 - a missing message bar never blocks drawing.
            pass
        QtC.safe_single_shot(
            3000, self._dock_widget, self._reset_markup_outside_notice
        )

    def _reset_markup_outside_notice(self):
        self._markup_outside_notice_active = False

    def _on_markup_done_clicked(self):
        """Leave the Mark up panel. The marks stay on the map so they render
        directly onto the image sent to the model; the same zone WITHOUT the
        marks is sent alongside so the model restores the pixels under each
        mark and no stroke appears in the result. The markup layer is dropped
        once the image has been captured at generation time."""
        if getattr(self, "_markup_done_in_progress", False):
            return
        self._markup_done_in_progress = True
        try:
            self._exit_tool_panel()
        finally:
            self._markup_done_in_progress = False

    def _on_vectorize_clicked(self):
        """User picked Tools → Vectorize.

        Toggles: a second click on the footer Vectorize icon while the panel
        is open closes it (same as the in-panel Done button).
        """
        self._disarm_swipe()
        if self._map_tool is not None:
            self._map_tool.hide_action_badges()
        if self._in_tool_panel == "vectorize":
            self._exit_tool_panel()
            return
        current = self._canvas.mapTool()
        if current is not None and current not in self._markup_tool_objs.values():
            self._pre_markup_map_tool = current
        self._in_tool_panel = "vectorize"
        self._dock_widget.set_vectorize_state()
        telemetry.track(te.VECTORIZE_PANEL_OPENED, {"source": "footer"})

    def _on_vectorize_suggestion_clicked(
        self, layer_id: str, color_hex: str, class_label: str
    ):
        """User clicked the post-generation \"Vectorize this result\" CTA.
        Open the panel with the source raster + color + class label pre-filled.

        Bypasses the toggle in `_on_vectorize_clicked` so a second click on
        the CTA never closes an already-open panel.
        """
        if self._map_tool is not None:
            self._map_tool.hide_action_badges()
        if self._in_tool_panel != "vectorize":
            current = self._canvas.mapTool()
            if current is not None and current not in self._markup_tool_objs.values():
                self._pre_markup_map_tool = current
            self._in_tool_panel = "vectorize"
            self._dock_widget.set_vectorize_state()
            telemetry.track(te.VECTORIZE_PANEL_OPENED, {"source": "canvas_pill"})
        # activate() runs first via set_vectorize_state; preconfigure overrides
        # the just-reset state with the template's values.
        self._dock_widget._vectorize_panel.preconfigure(
            layer_id=layer_id, color_hex=color_hex, class_label=class_label
        )
        telemetry.track(
            te.VECTORIZE_SUGGESTION_CLICKED,
            {"color": color_hex, "has_class_label": bool(class_label)},
        )

    def _on_vectorize_done_clicked(self):
        self._exit_tool_panel()

    def _on_canvas_compare(self) -> None:
        """Compare pill (canvas) clicked: toggle the before/after swipe.

        The pill stays on the canvas during the comparison (see the overlay
        click-forwarding below), so this is a real toggle: arm if off, disarm
        if already comparing. Arming preserves the zone + pills so they survive
        the swipe taking the canvas, and passes the click-forwarding callback
        so the pills stay live underneath it.
        """
        if self._swipe_controller is None:
            return
        if self._swipe_controller.is_active():
            self._swipe_controller.stop()
            return
        if self._map_tool is not None and self._canvas.mapTool() is self._map_tool:
            self._map_tool.preserve_state_on_next_deactivate()
        self._swipe_controller.start(self._forward_canvas_overlay_click)

    def _forward_canvas_overlay_click(self, canvas_pt) -> bool:
        """Let the action pills claim a click while the swipe owns the canvas.

        Pure hit-test here; the actual action is deferred a tick because it may
        swap the map tool, which must not happen inside the swipe tool's own
        press event.
        """
        if self._map_tool is None:
            return False
        which = self._map_tool.overlay_hit(canvas_pt)
        if which is None:
            return False
        QTimer.singleShot(0, lambda: self._dispatch_overlay_action(which))
        return True

    def _dispatch_overlay_action(self, which: str) -> None:
        # Deferred a tick from the canvas click; if the plugin was unloaded in
        # between (unload() clears _map_tool), the captured callback would touch
        # torn-down state. Bail before doing anything.
        if self._map_tool is None:
            return
        if which == "compare":
            self._on_canvas_compare()
        elif which == "vectorize":
            self._on_canvas_vectorize()
        elif which == "delete":
            self._on_zone_delete_requested()

    def _selected_version_layer(self):
        """The raster layer of the selected version (or the newest version
        that has one). None when the lineage holds no on-map layer."""
        from qgis.core import QgsProject

        if not self._versions:
            return None
        candidates = []
        if 0 <= self._selected_version_index < len(self._versions):
            candidates.append(self._versions[self._selected_version_index])
        candidates.extend(reversed(self._versions))
        for version in candidates:
            layer_id = version.get("layer_id")
            if layer_id:
                layer = QgsProject.instance().mapLayer(layer_id)
                if layer is not None:
                    return layer
        return None

    def _show_action_pills(self) -> None:
        """Show the canvas action pills with the right options for the current
        result: Compare when a before/after is possible, Vectorize when the run
        was a detection / segmentation template.

        Self-healing: tool detours (Mark up, Vectorize, eyedropper) often leave
        a vector layer active, which made Compare silently vanish while
        Vectorize stayed. If Compare is ineligible but the lineage has a result
        raster on the map, re-activate it and re-check. Same for the zone rect:
        programmatic flows may not have armed the map tool, so restore it from
        the selected extent before showing badges (they anchor to it)."""
        if self._map_tool is None:
            return
        if getattr(self._map_tool, "_zone_rect", None) is None and self._selected_extent is not None:
            try:
                self._map_tool.set_zone(QgsRectangle(self._selected_extent))
            except Exception as err:  # nosec B110
                log_warning(f"zone rect restore for pills failed: {err}")
        can_compare = self._swipe_controller is not None and self._swipe_controller.can_swipe_now()
        if not can_compare and self._swipe_controller is not None:
            layer = self._selected_version_layer()
            if layer is not None:
                try:
                    self._iface.setActiveLayer(layer)
                    can_compare = self._swipe_controller.can_swipe_now()
                except Exception as err:  # nosec B110
                    log_warning(f"re-activate result for Compare failed: {err}")
        color = self._vectorize_suggestion[1] if self._vectorize_suggestion else None
        self._map_tool.show_action_badges(compare=can_compare, vectorize=bool(color))

    def _on_canvas_vectorize(self) -> None:
        """Vectorize pill (canvas) clicked: open the Vectorize panel pre-filled
        with the just-generated result, mirroring the dock CTA."""
        if self._vectorize_suggestion is None:
            return
        self._disarm_swipe()
        layer_id, color_hex, class_label = self._vectorize_suggestion
        self._on_vectorize_suggestion_clicked(
            layer_id, color_hex or "", class_label or ""
        )

    def _disarm_swipe(self) -> None:
        """Stop the swipe map tool if it is currently armed.

        Called from every other AI Edit action (vectorize, markup,
        settings, help, exit, launch) so the canvas only ever runs one
        AI Edit tool at a time. The user explicitly asked for this: as
        soon as they pick another action, the swipe must release the
        canvas so they are not left in a stale compare mode.
        """
        if self._swipe_controller is not None and self._swipe_controller.is_active():
            self._swipe_controller.stop()

    def _on_help_menu_open_changed(self, opened: bool) -> None:
        if opened:
            self._disarm_swipe()

    def _on_swipe_toggled(self, checked: bool) -> None:
        """Footer Before/After toggled by the user.

        ``checked=True`` arms the swipe map tool on the currently active
        AI-Edit raster; ``checked=False`` disarms it and restores the
        previous map tool. No dock panel is shown either way.
        """
        if checked:
            self._swipe_controller.start()
        else:
            self._swipe_controller.stop()

    def _on_swipe_armed(self) -> None:
        self._dock_widget.set_swipe_button_checked(True)
        if self._map_tool is not None:
            self._map_tool.set_compare_active(True)
        telemetry.track(te.SWIPE_ARMED)

    def _on_swipe_disarmed(self) -> None:
        self._dock_widget.set_swipe_button_checked(False)
        if self._map_tool is not None:
            self._map_tool.set_compare_active(False)
        # After disarm, the active layer might have become non-eligible
        # while the swipe was on; re-evaluate the enable state so the
        # button greys out cleanly.
        self._dock_widget.set_swipe_button_enabled(
            self._swipe_controller.can_swipe_now()
        )
        telemetry.track(te.SWIPE_DISARMED)

    def _exit_tool_panel(self):
        """Common path for Done from either tool panel."""
        # Restore the canvas tool that was active before opening the panel.
        if self._pre_markup_map_tool is not None:
            try:
                self._canvas.setMapTool(self._pre_markup_map_tool)
            except RuntimeError:
                pass
        self._pre_markup_map_tool = None
        if self._markup_event_filter is not None:
            try:
                self._iface.mainWindow().removeEventFilter(self._markup_event_filter)
            except RuntimeError:
                pass
        self._restore_qgis_undo()
        self._in_tool_panel = None
        self._dock_widget.exit_tool_panel()
        # Tool panels only hid the canvas pills; if they still belong to the
        # current result (Done from Vectorize / Mark up), bring them back.
        # Vectorize leaves a vector layer active, which would make Compare
        # ineligible, so re-activate the result raster first.
        if self._pills_armed:
            if self._vectorize_suggestion is not None:
                from qgis.core import QgsProject

                layer = QgsProject.instance().mapLayer(self._vectorize_suggestion[0])
                if layer is not None:
                    try:
                        self._iface.setActiveLayer(layer)
                    except Exception as err:  # nosec B110
                        log_warning(f"re-activate result layer failed: {err}")
            self._show_action_pills()

    def _suppress_qgis_undo(self) -> None:
        """Disable every main-window QAction bound to Cmd/Ctrl+Z while in
        Markup so QGIS's project-undo shortcut never intercepts the
        keystroke before our handlers can fire. Restored via the matching
        ``_restore_qgis_undo()`` call on panel exit / unload.
        """
        if self._suppressed_undo_actions:
            return
        target_seq = QKeySequence(QKeySequence.StandardKey.Undo)
        mainwin = self._iface.mainWindow()
        for action in mainwin.findChildren(QAction):
            try:
                shortcuts = action.shortcuts() or [action.shortcut()]
            except RuntimeError:
                continue
            if any(sc == target_seq for sc in shortcuts if not sc.isEmpty()):
                self._suppressed_undo_actions.append((action, action.isEnabled()))
                action.setEnabled(False)

    def _restore_qgis_undo(self) -> None:
        for action, was_enabled in self._suppressed_undo_actions:
            try:
                action.setEnabled(was_enabled)
            except RuntimeError:
                pass
        self._suppressed_undo_actions = []

    def _clear_markup_layer(self):
        """Drop the in-memory annotation layer (no-op if absent)."""
        if self._markup_manager is not None:
            self._markup_manager.remove_layer()

    def _on_sign_out(self):
        """Disconnect: clear the stored key and return to the sign-in screen."""
        self._last_key_validation_unix = 0.0
        clear_activation()
        self._auth_manager.set_activation_key("")
        self._dock_widget.set_activated(False)
        self._settings_action.setEnabled(False)
        log_debug("Signed out")

    def _apply_activation(self, key: str):
        """Shared success funnel for both manual paste and one-click connect.

        Persists the key, loads it into the auth manager, flips the dock to the
        activated state, and kicks a credit refresh. Callers add their own
        path-specific telemetry afterward.
        """
        save_activation(key)
        self._auth_manager.set_activation_key(key)
        self._dock_widget.set_activated(True)
        self._dock_widget.set_activation_message(tr("Activation key verified!"), is_error=False)
        self._dock_widget.hide_activation_limit_cta()
        self._settings_action.setEnabled(True)
        # Stay on LAUNCH state; tool is activated on user click.
        self._dock_widget.set_checking_credits(True)
        self._refresh_credits()
        # Persist activation timestamp once for cohort analysis.
        settings = QSettings()
        if not settings.value("AIEdit/activation_timestamp_unix", "", type=str):
            settings.setValue("AIEdit/activation_timestamp_unix", str(int(time.time())))

    def _cancel_pairing_worker(self):
        """Cancel any in-flight pairing poll (never terminate())."""
        if self._pairing_worker is not None and self._pairing_worker.is_active():
            try:
                self._pairing_worker.cancel()
            except Exception:  # nosec B110
                pass

    # --- One-click connect (browser pairing handoff) ------------------------

    def _on_pairing_requested(self, code: str):
        """Open the browser to /connect and start polling for the key.

        Re-entrant: if a poll is already running (the user clicked "open the
        page again"), we only re-open the browser instead of starting a second
        worker.
        """
        # Build the connect URL from the client base so .env.local
        # TERRALAB_BASE_URL is honored in dev. Never log the code or full URL.
        url = (
            f"{self._client.base_url}/connect?code={code}&product=ai-edit"
            "&utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=connect"
        )
        # Hand the URL to the dock so its copy-link button can offer it (lets
        # the user finish sign-in in a different browser).
        self._dock_widget.set_pairing_link(url)
        opened = QDesktopServices.openUrl(QUrl(url))
        if not opened:
            self._dock_widget.show_pairing_idle()
            self._dock_widget.set_activation_message(
                tr("Couldn't open your browser. Copy the link and open it manually."),
                is_error=True,
            )
            return

        if self._pairing_worker is not None and self._pairing_worker.is_active():
            # Already polling for this code; the browser was just re-opened.
            return

        self._pairing_worker = PairingPollTask(self._client, code)
        self._pairing_worker.pairing_succeeded.connect(self._on_pairing_succeeded)
        self._pairing_worker.pairing_failed.connect(self._on_pairing_failed)
        self._pairing_worker.pairing_timeout.connect(self._on_pairing_timeout)
        self._pairing_worker.pairing_browser_seen.connect(self._on_pairing_browser_seen)
        self._pairing_worker.pairing_stalled.connect(self._on_pairing_stalled)
        QgsApplication.taskManager().addTask(self._pairing_worker)
        telemetry.track(te.AI_EDIT_PAIR_STARTED)
        telemetry.flush()
        log("Pairing started")

    def _on_pairing_succeeded(self, key: str):
        self._apply_activation(key)
        # Bring QGIS back to front so the user sees the activated dock.
        try:
            mw = self._iface.mainWindow()
            mw.activateWindow()
            mw.raise_()
            self._dock_widget.raise_()
        except Exception:  # nosec B110
            pass
        telemetry.track(te.AI_EDIT_PAIR_SUCCEEDED)
        telemetry.track(te.PLUGIN_ACTIVATED, {"activation_method": "pairing"})
        telemetry.flush()
        log("Pairing successful")

    def _on_pairing_failed(self, message: str, code: str):
        self._dock_widget.show_pairing_idle()
        self._dock_widget.set_activation_message(message, is_error=True)
        telemetry.track(te.AI_EDIT_PAIR_FAILED, {"error_code": (code or "UNKNOWN")})
        telemetry.flush()
        log_warning("Pairing failed")

    def _on_pairing_browser_seen(self):
        if self._dock_widget:
            self._dock_widget.show_pairing_browser_seen()

    def _on_pairing_stalled(self):
        if self._dock_widget:
            self._dock_widget.show_pairing_stalled_hint()
        log_warning("Pairing stalled: browser never reached /connect")

    def _on_pairing_timeout(self):
        self._dock_widget.show_pairing_idle()
        self._dock_widget.set_activation_message(
            tr("Sign-in timed out. Click Connect to try again, "
               "or enter your key manually."),
            is_error=True,
        )
        telemetry.track(te.AI_EDIT_PAIR_TIMEOUT)
        telemetry.flush()
        log("Pairing timed out")

    def _on_cancel_pairing(self, code: str = ""):
        self._cancel_pairing_worker()
        if code:
            # Retire the code server-side so a later Confirm in the browser
            # shows "expired" instead of binding a key nobody is polling for.
            task = GenericRequestTask(
                tr("Cancelling sign-in"),
                lambda c=code: self._client.cancel_pairing(c),
                silent=True,
            )
            self._hold_history_task(task)
        telemetry.track(te.AI_EDIT_PAIR_CANCELLED)
        telemetry.flush()
        log("Pairing cancelled")

    def _on_generate(self, prompt: str):
        if self._worker is not None and self._worker.is_active():
            self._dock_widget.set_status(tr("Generation already in progress"), is_error=True)
            return
        if self._export_worker is not None and self._export_worker.is_active():
            # Click landed while a previous render is still in flight - swallow.
            return
        if not self._selected_extent:
            self._dock_widget.set_status(tr("No zone selected"), is_error=True)
            return
        # Launching a new generation is a clear "I am done comparing"
        # signal: drop the swipe overlay so the canvas renders fresh.
        self._disarm_swipe()

        # Save consent on first generation and hide checkbox
        if not has_consent():
            save_consent()
            self._dock_widget.hide_consent()

        # Ensure server config is loaded before generation
        if not has_server_config():
            self._dock_widget.set_status(
                tr(
                    "Cannot generate: export config not loaded from server. "
                    "Check your internet connection and restart QGIS."
                ),
                is_error=True
            )
            return

        ctx = PipelineContext()
        # Base picked in the version strip. Index 0 (Original) rebuilds from the
        # clean map (every AI result dropped from the export below) and is not an
        # iteration; any generated version keeps only that version in the export
        # and anchors the next edit on its request id.
        base_version = (
            self._versions[self._selected_version_index]
            if 0 <= self._selected_version_index < len(self._versions)
            else None
        )
        base_layer_id = base_version["layer_id"] if base_version else None
        ctx.parent_request_id = base_version["request_id"] if base_version else None
        # Same continuous flow on this zone = same session.
        ctx.session_id = self._session_id

        # Tag the job with template_id. The armed template (set when the
        # user picked a preset) wins so prompt edits keep the association;
        # fall back to exact text match for prompts loaded any other way.
        armed = self._dock_widget.get_active_template()
        match = armed or lookup_template_by_prompt(prompt)
        if match:
            ctx.template_id, ctx.template_name = match
            ctx.vector_color, ctx.vector_classes = get_vector_hints(ctx.template_id)
        else:
            # No preset matched. Still light up the Vectorize CTA when the
            # free-form prompt asks to segment, detect, or vectorize one
            # feature type without naming colors (server paints #FF0000).
            ctx.vector_color = detect_freeform_vector_intent(prompt)

        if self._dock_widget._is_free_tier:
            suggested_res = "1K"
        else:
            suggested_res = self._dock_widget.get_selected_resolution()

        # Lock UI; prep ticker animates while export+upload run off-thread.
        self._dock_widget.set_generating(True)
        self._dock_widget.set_status("")

        # When the user drew markup, the marks are rendered directly onto the
        # MAIN image (co-located guidance), and the same zone WITHOUT the marks
        # is sent as a clean base so the model can restore the pixels under each
        # mark. With no markup, behave exactly as before (single clean render).
        markup_layer = None
        if self._markup_manager is not None and self._markup_manager.annotation_count() > 0:
            try:
                markup_layer = self._markup_manager.layer()
            except RuntimeError:
                markup_layer = None

        # Pick the base by excluding every AI-Edit result from the EXPORT except
        # the selected version, so the model sees exactly that base. Original
        # (base_layer_id None) drops them all for the clean map. The active Mark
        # up layer is kept (user guidance, not an AI edit). prepare_export
        # filters a clone, so on-screen layers and the just-generated image are
        # never hidden.
        from .layer_groups import collect_ai_edit_layer_ids

        exclude_layer_ids = collect_ai_edit_layer_ids()
        if base_layer_id:
            exclude_layer_ids.discard(base_layer_id)
        if markup_layer is not None:
            exclude_layer_ids.discard(markup_layer.id())

        try:
            map_settings = self._canvas.mapSettings()
            # Always export the input at the chosen resolution (1K/2K/4K). The
            # model only ever works at those sizes, so sending the full native
            # zone is pointless: a big Google Satellite selection would balloon
            # into tens of MB, stall the upload, and gain nothing.
            prep = prepare_export(
                map_settings,
                self._selected_extent,
                target_resolution=suggested_res,
                markup_layer=markup_layer,
                exclude_layer_ids=exclude_layer_ids,
            )
        except Exception as e:
            self._dock_widget.set_generating(False)
            msg = tr("Export error: {error}").format(error=e)
            self._dock_widget.set_status(msg, is_error=True)
            telemetry.track(te.EXPORT_FAILED, {
                "stage": "export",
                "error_code": "canvas_export_failed",
                "error_message": _scrub_paths(str(e))[:200],
            })
            telemetry.flush()
            self._show_error_report(msg)
            return

        # Hand off everything the export-completed callback needs.
        self._pending_generation = {
            "prompt": prompt,
            "ctx": ctx,
            "prep": prep,
            "suggested_res": suggested_res,
            "crs_wkt": map_settings.destinationCrs().toWkt(),
        }

        worker = ExportWorker(prep)
        worker.completed.connect(self._on_export_completed)
        worker.failed.connect(self._on_export_failed)
        self._export_worker = worker
        QgsApplication.taskManager().addTask(worker)

    def _cleanup_export_worker(self, worker):
        if self._export_worker is worker:
            self._export_worker = None

    def _on_export_failed(self, error_msg: str):
        self._pending_generation = None
        self._dock_widget.set_generating(False)
        msg = tr("Export error: {error}").format(error=error_msg)
        self._dock_widget.set_status(msg, is_error=True)
        telemetry.track(te.EXPORT_FAILED, {
            "stage": "export",
            "error_code": "canvas_export_failed",
            "error_message": _scrub_paths(error_msg)[:200],
        })
        telemetry.flush()
        self._show_error_report(msg)

    def _on_export_completed(
        self,
        image_b64: str,
        img_w: int,
        img_h: int,
        actual_extent,
        size_bytes: int,
        input_format: str,
        guidance_b64: str = "",
        guidance_format: str = "",
    ):
        pending = self._pending_generation
        self._pending_generation = None
        if pending is None:
            # User cancelled / dock was torn down before the render finished.
            return

        ctx = pending["ctx"]
        prep = pending["prep"]
        prompt = pending["prompt"]
        suggested_res = pending["suggested_res"]
        crs_wkt = pending["crs_wkt"]

        log_debug(
            f"Export completed: main_b64={len(image_b64)}, "
            f"guidance_b64={len(guidance_b64)}, "
            f"guidance_format={guidance_format or '-'}, "
            f"used_markup={bool(guidance_b64)}"
        )

        apply_export_context(ctx, prep, actual_extent, size_bytes, input_format)

        # Canvas captured: advance the prep ticker to the upload phase so the
        # message pool reflects what's actually happening next (sending bytes).
        self._dock_widget.prep_advance_phase("upload")

        # Use "auto" so the model preserves the input image dimensions.
        # Explicit ratios (e.g. "21:9") cause the model to reshape the output,
        # which creates alignment issues with the source imagery.
        aspect_ratio = "auto"
        ctx.aspect_ratio = aspect_ratio

        # Use the actual rendered extent (QGIS may adjust it to match the
        # output pixel aspect ratio). This prevents image stretching.
        extent_dict = {
            "xmin": actual_extent.xMinimum(),
            "ymin": actual_extent.yMinimum(),
            "xmax": actual_extent.xMaximum(),
            "ymax": actual_extent.yMaximum(),
        }
        output_dir = get_output_dir()

        # Update rubber band to match the actual rendered extent
        self._selected_extent = actual_extent
        self._show_selection_rectangle(actual_extent)

        # ``guidance_b64`` here is the clean base (the zone with the marks
        # removed); the marks ride on ``image_b64``. The server is told via the
        # marks_on_input flag to restore the pixels under each mark from it, so
        # no stroke appears in the result.

        # Preserve original zone for retry (never chain from AI result)
        self._last_image_b64 = image_b64
        self._last_guidance_b64 = guidance_b64 or None
        self._last_guidance_format = guidance_format or None
        self._last_input_format = input_format
        self._last_input_bytes = size_bytes
        self._last_extent_dict = extent_dict
        self._last_crs_wkt = crs_wkt
        self._last_aspect_ratio = aspect_ratio
        self._last_suggested_res = suggested_res

        # Seed the version strip's Original tile from the very first export of
        # this lineage - that render is the clean zone before any AI edit. Later
        # exports (iterations) skip this; the Original is captured once. When
        # markup was drawn, prefer the clean base so the Original tile shows the
        # unmarked zone rather than the strokes.
        if not self._versions:
            self._versions.append({"layer_id": None, "request_id": None, "prompt": ""})
            self._selected_version_index = 0
            pixmap = self._pixmap_from_b64(guidance_b64 or image_b64)
            try:
                self._dock_widget.seed_version_strip(pixmap)
            except AttributeError:
                pass

        # Keep the markup layer alive through the generation so the marks stay
        # visible while the model works. It is dropped when the generation ends
        # (_on_generation_finished / _on_generation_error / cancel) so the
        # result shows clean and the temporary layer never piles up.

        if self._map_tool:
            self._map_tool.set_locked(True)
        # set_generating(True) already called at click time. Don't call again
        # here or we'd reset the prep ticker phase + bar back to 1%.
        self._generation_service.reset()
        self._generation_start_time = time.time()
        self._last_generation_is_retry = False
        used_markup = bool(guidance_b64)
        telemetry.track(te.GENERATION_STARTED, self._enrich_generation_props({
            "prompt_length": len(prompt),
            "aspect_ratio": aspect_ratio,
            "resolution": suggested_res,
            "zone_width_px": img_w,
            "zone_height_px": img_h,
            "input_image_bytes": size_bytes,
            "input_image_format": input_format,
            "is_retry": False,
            "has_geo_context": self._reference_store.count() > 0,
            "template_id": ctx.template_id,
            "template_name": ctx.template_name,
            "used_template": bool(ctx.template_id),
            "used_markup": used_markup,
        }))
        self._last_generation_used_markup = used_markup
        log(f"Generation started: prompt_len={len(prompt)}, resolution={suggested_res}, zone={img_w}x{img_h}px")

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

        self._worker = GenerationWorker(
            client=self._client,
            auth_manager=self._auth_manager,
            service=self._generation_service,
            image_b64=image_b64,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            extent_dict=extent_dict,
            crs_wkt=crs_wkt,
            output_dir=output_dir,
            ctx=ctx,
            debug_mode=self._dev_mode,
            plugin_dir=plugin_dir,
            skip_trial_check=self._skip_trial_check,
            suggested_resolution=suggested_res,
            context_images=self._reference_store.get_all_b64(),
            guidance_image=guidance_b64 or None,
            guidance_format=guidance_format or None,
        )
        self._worker.succeeded.connect(self._on_generation_finished)
        self._worker.progress.connect(self._on_generation_progress)
        self._worker.failed.connect(self._on_generation_error)
        QgsApplication.taskManager().addTask(self._worker)

    def _on_generation_progress(self, status: str, percentage: int):
        self._dock_widget.set_progress_message(status, percentage)

    def _on_generation_error(self, message: str, code: str, ctx_snapshot: dict | None = None):
        if self._map_tool:
            self._map_tool.set_locked(False)
        self._dock_widget.set_generating(False)
        # Template metadata arrives in the ctx_snapshot dict copied off the
        # worker thread (C2). Read it before cleanup so generation_failed is
        # segmentable by template in telemetry.
        snap = ctx_snapshot or {}
        template_id = snap.get("template_id")
        template_name = snap.get("template_name")
        self._cleanup_worker()
        # Generation ended (with an error): drop the markup layer so it does not
        # linger or accumulate. A retry re-sends the cached marked image.
        self._clear_markup_layer()
        normalized_code = (code or "").strip().upper()
        message_lower = (message or "").lower()
        quota_codes = {
            "QUOTA_EXCEEDED",
            "LIMIT_REACHED",
            "USAGE_LIMIT_REACHED",
            "MONTHLY_LIMIT_REACHED",
        }
        is_quota_error = normalized_code in quota_codes or "monthly limit reached" in message_lower
        duration = time.time() - getattr(self, "_generation_start_time", time.time())
        # error_code must never be empty: the polling path returns a bare
        # status=failed (model could not produce an image) with no code.
        effective_code = (code or "").strip() or "model_failure"
        extra_props: dict = {
            "error_code": effective_code,
            "stage": _failure_stage(normalized_code),
            "is_retry": self._last_generation_is_retry,
            "duration_ms": int(duration * 1000),
            "resolution": getattr(self, "_last_suggested_res", ""),
            "template_id": template_id,
            "template_name": template_name,
            "used_template": bool(template_id),
        }
        # WRITE_ERROR is ~90% Windows-path; surface enough to triage the sub-class.
        if normalized_code == "WRITE_ERROR":
            try:
                import sys as _sys
                output_dir = get_output_dir() or ""
                extra_props.update({
                    "os": _sys.platform,
                    "output_dir_len": len(output_dir),
                    "output_dir_has_unicode": not output_dir.isascii(),
                    "output_dir_has_spaces": " " in output_dir,
                    "exception_msg": _scrub_paths((message or "")[:200]),
                })
            except Exception:  # nosec B110
                pass
        telemetry.track(te.GENERATION_FAILED, self._enrich_generation_props(extra_props))
        telemetry.flush()
        if normalized_code == "TRIAL_EXHAUSTED":
            config = get_server_config(self._client)
            dashboard = config.get("upgrade_url", get_dashboard_url())
            self._dock_widget.show_trial_exhausted_info(message, dashboard)
            telemetry.track(te.TRIAL_EXHAUSTED_VIEWED, {"is_free_tier": True})
        elif is_quota_error:
            self._dock_widget.show_usage_limit_info(message, SUBSCRIBE_ERROR_URL)
            telemetry.track(te.TRIAL_EXHAUSTED_VIEWED, {"is_free_tier": False})
        elif _is_model_failure(message, normalized_code):
            # The model couldn't produce an image (no-output / safety block). The
            # server already marked the job failed and refunded the credit, so we
            # never show the raw provider error or open the bug-report dialog:
            # reassure the user (not charged) and tell them what to try instead.
            if "block" in (message or "").lower() or "safety" in (message or "").lower():
                enriched = tr(
                    "Generation failed: the request was blocked by a safety filter. "
                    "You have not been charged. Try rephrasing your prompt."
                )
            else:
                enriched = tr(
                    "Generation failed: the AI couldn't create an image for this "
                    "request. You have not been charged. Try rephrasing your prompt, "
                    "or pick a different zone."
                )
            self._dock_widget.set_status(enriched, is_error=True)
        elif _is_service_busy(message, normalized_code):
            # Servers momentarily overloaded; user not charged. Calm inline retry,
            # never the bug-report dialog (nothing for the user to report).
            enriched = tr(
                "Our image servers are busy right now. You have not been charged. "
                "Please wait a moment and try again."
            )
            self._dock_widget.set_status(enriched, is_error=True)
        else:
            enriched = _enrich_error_message(message, code)
            # Reassure on EVERY credit-safe failure that no credit was kept (the
            # server refunds failed jobs; pre-charge errors never charged).
            if normalized_code in _CREDIT_REASSURE_CODES:
                enriched = f"{enriched} {tr('No credit was used.')}"
            request_id = snap.get("request_id") or ""
            policy = _report_policy(normalized_code)
            if policy == "link":
                # Transient/our-side: clean inline message + an OPTIONAL log link.
                # We never force a modal for something the user just retries.
                report_link = (
                    f'<a href="{REPORT_PROBLEM_HREF}">{tr("Report a problem")}</a>'
                )
                self._dock_widget.arm_report_context(request_id)
                self._dock_widget.set_status(f"{enriched} {report_link}", is_error=True)
            elif policy == "dialog":
                # Likely a genuine bug: surface it and proactively offer to send
                # the log so we hear about it.
                self._dock_widget.set_status(enriched, is_error=True)
                self._show_error_report(enriched, request_id)
            else:
                # User-fixable (network, key, zone, plan): plain inline message.
                self._dock_widget.set_status(enriched, is_error=True)
        log_warning(f"Generation failed: {message} (code={code})")

    def _show_error_report(self, error_message: str, request_id: str = "") -> None:
        """Open the copy-logs/email report dialog. A failure here must never
        mask the original error, so it is swallowed (and logged)."""
        try:
            from .dialogs.error_report_dialog import show_error_report
            show_error_report(self._iface.mainWindow(), error_message, request_id)
        except Exception as err:  # nosec B110
            log_warning(f"Could not open error report dialog: {err}")

    def _on_generation_finished(self, result_info: dict):
        if self._map_tool:
            self._map_tool.set_locked(False)
        # result_info already holds the ctx snapshot copied off the worker.
        self._last_completed_request_id = result_info.get("request_id")
        vector_color: str | None = result_info.get("vector_color")
        vector_classes: list[dict] | None = result_info.get("vector_classes")
        template_id: str | None = result_info.get("template_id")
        template_name: str | None = result_info.get("template_name")
        self._cleanup_worker()
        # Generation is over: drop the markup layer so the result shows clean
        # and the temporary layer does not accumulate. The marks stayed visible
        # for the whole run.
        self._clear_markup_layer()
        duration = time.time() - getattr(self, "_generation_start_time", time.time())

        try:
            layer = add_geotiff_to_project(
                result_info["geotiff_path"],
                result_info.get("prompt", ""),
                crs_wkt=result_info.get("crs_wkt", ""),
            )
            try:
                self._iface.setActiveLayer(layer)
            except Exception as err:  # nosec B110
                log_warning(f"setActiveLayer failed: {err}")
            prompt_history.add_recent(result_info.get("prompt", ""))
            # New result exists server-side now, so the library's session cache
            # of Recent/Favorites must refetch the next time it opens.
            if self._dock_widget is not None:
                self._dock_widget.mark_library_history_dirty()
            telemetry.track(te.GENERATION_COMPLETED, self._enrich_generation_props({
                "duration_ms": int(duration * 1000),
                "resolution": getattr(self, "_last_suggested_res", ""),
                "is_retry": self._last_generation_is_retry,
                "used_markup": self._last_generation_used_markup,
                "output_rescued": bool(result_info.get("output_rescued")),
                "template_id": template_id,
                "template_name": template_name,
                "used_template": bool(template_id),
            }))
            telemetry.flush()
            self._maybe_emit_first_generation_milestone()
            self._dock_widget.set_generation_complete(layer.name(), layer.id())
            # Append this result to the lineage and let the strip show + select
            # it. The Original tile was seeded at export time, so by now the
            # strip already holds at least the Original.
            result_prompt = result_info.get("prompt", "")
            # The base this result was generated from is the version that was
            # selected when generation started (still current until we append).
            base_index = self._selected_version_index
            base_label = tr("Original") if base_index <= 0 else f"V{base_index}"
            self._versions.append({
                "layer_id": layer.id(),
                "request_id": self._last_completed_request_id,
                "prompt": result_prompt,
            })
            self._selected_version_index = len(self._versions) - 1
            thumb = self._render_layer_thumb(layer)
            # Metadata surfaced in the version-details dialog: the definition the
            # user picked and whether a prompt template shaped this run.
            try:
                dims = f"{layer.width()} × {layer.height()}"
            except Exception:  # nosec B110 - dimensions are cosmetic only.
                dims = None
            version_meta = {
                "definition": getattr(self, "_last_suggested_res", "") or "",
                "dimensions": dims,
                "template_name": template_name,
                "base_label": base_label,
            }
            try:
                self._dock_widget.add_version_thumb(thumb, result_prompt, version_meta)
            except AttributeError:
                pass
            class_label = _resolve_class_label(vector_color, vector_classes)
            self._dock_widget.set_vectorize_suggestion(
                layer.id(), vector_color, class_label
            )
            # Surface the next action on the canvas, beside the × badge:
            # Compare whenever a before/after is possible, Vectorize when the
            # run came from a detection / segmentation template (same signal
            # that drives the dock CTA above).
            self._vectorize_suggestion = (layer.id(), vector_color, class_label)
            self._pills_armed = True
            self._show_action_pills()
            self._refresh_credits()
            log(f"Generation complete ({round(duration, 1)}s): {result_info['geotiff_path']}")
        except Exception as e:
            telemetry.track(te.PLUGIN_ERROR, {
                "stage": "write",
                "error_code": "layer_add_failed",
                "error_message": _scrub_paths(str(e))[:200],
            })
            telemetry.flush()
            self._dock_widget.set_generating(False)
            msg = tr("Error adding layer: {error}").format(error=e)
            self._dock_widget.set_status(msg, is_error=True)
            self._show_error_report(msg, result_info.get("request_id") or "")
            log_warning(f"Failed to add layer: {e}")

    def _refresh_credits(self):
        """Fetch and display current credits in background (non-blocking)."""
        self._credits_loader = GenericRequestTask(
            "AI Edit credits",
            self._auth_manager.get_usage_info,
            silent=True,
        )
        self._credits_loader.succeeded.connect(self._on_credits_loaded)
        self._credits_loader.failed.connect(lambda _msg, _code: self._on_credits_failed())
        QgsApplication.taskManager().addTask(self._credits_loader)

    def _on_credits_failed(self):
        """Credits fetch failed - clear loading state so the UI is usable."""
        if self._dock_widget:
            self._dock_widget.set_checking_credits(False)
            self._dock_widget.set_launch_enabled(True)

    def _on_credits_loaded(self, usage: dict):
        """Update dock widget with credits from background fetch."""
        if self._dock_widget:
            self._dock_widget.set_checking_credits(False)
            self._dock_widget.set_launch_enabled(True)
            used = usage.get("images_used")
            limit = usage.get("images_limit")
            is_free = usage.get("is_free_tier", False)
            # Prime the subscribe URL BEFORE set_credits so the dock's
            # auto-surface logic can show the upsell banner inline.
            if is_free:
                config = get_server_config(self._client)
                dashboard = config.get("upgrade_url", get_dashboard_url())
                self._dock_widget.set_subscribe_url(dashboard)
            self._dock_widget.set_credits(
                used=used,
                limit=limit,
                is_free_tier=is_free,
            )
            # Paid-tier monthly limit still needs the dedicated CTA (different
            # message + different URL than the free-tier upsell).
            both_ints = isinstance(used, int) and isinstance(limit, int)
            if both_ints and limit > 0 and used >= limit and not is_free:
                self._dock_widget.show_usage_limit_info(
                    f"Monthly limit reached ({used}/{limit}).",
                    SUBSCRIBE_ERROR_URL,
                )

    def _cleanup_worker(self):
        """Drop our reference to the QgsTask; TaskManager owns its lifetime."""
        if self._worker is None:
            return
        for sig in [self._worker.succeeded, self._worker.progress, self._worker.failed]:
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._worker = None

    # --- Selection rectangle management ---

    def _show_selection_rectangle(self, extent):
        self._clear_selection_rectangle()
        rb = QgsRubberBand(self._canvas, QtC.PolygonGeometry)
        rb.setColor(QColor(0, 0, 0, 0))
        rb.setStrokeColor(QColor(65, 105, 225, 180))
        rb.setWidth(2)
        # Sit above the Before/After swipe overlay (zValue 100) so the blue zone
        # frame stays fully visible on all four sides while the user swipes,
        # instead of the overlay covering its right half.
        rb.setZValue(110)
        for x, y, last in (
            (extent.xMinimum(), extent.yMinimum(), False),
            (extent.xMaximum(), extent.yMinimum(), False),
            (extent.xMaximum(), extent.yMaximum(), False),
            (extent.xMinimum(), extent.yMaximum(), True),
        ):
            rb.addPoint(QgsPointXY(x, y), last)
        self._selection_rubber_band = rb

    def _clear_selection_rectangle(self):
        for attr in ("_selection_rubber_band_halo", "_selection_rubber_band"):
            band = getattr(self, attr, None)
            if not band:
                continue
            try:
                scene = band.scene()
                if scene is not None:
                    scene.removeItem(band)
            except (RuntimeError, AttributeError):
                pass
            setattr(self, attr, None)
