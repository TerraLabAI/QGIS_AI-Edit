from __future__ import annotations

import os

from qgis.PyQt.QtGui import QAction, QShortcut

from ..api.terralab_client import TerraLabClient
from ..core.auth.auth_manager import AuthManager
from ..core.config_store import ConfigStore, set_store
from ..core.generation.generation_service import GenerationService
from ..core.logger import log_warning
from ..core.reference_image_store import ReferenceImageStore
from ..workers.export_worker import ExportWorker
from .plugin_parts.activation import ActivationMixin
from .plugin_parts.generation import GenerationMixin
from .plugin_parts.generation_results import GenerationResultsMixin
from .plugin_parts.history import HistoryMixin
from .plugin_parts.lifecycle import PluginLifecycleMixin
from .plugin_parts.onboarding import OnboardingMixin
from .plugin_parts.startup import StartupMixin
from .plugin_parts.tool_panels import ToolPanelsMixin, _MarkupUndoFilter
from .plugin_parts.zone_versions import ZoneVersionsMixin
from .tools.markup_tools import MarkupLayerManager


class AIEditPlugin(
    PluginLifecycleMixin,
    StartupMixin,
    ActivationMixin,
    ZoneVersionsMixin,
    HistoryMixin,
    ToolPanelsMixin,
    GenerationMixin,
    GenerationResultsMixin,
    OnboardingMixin,
):
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
        # Set by Stop/Exit before they cancel, so the taskTerminated slot knows
        # the plugin already recovered the UI (vs a native task-manager Cancel).
        self._generation_cancel_handled = False
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
        # Off-thread pre-warm of the activation/upsell config (see startup).
        self._activation_config_loader = None
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
        # Stored (layer_id, color_hex, class_label, trigger) of the last
        # generation so the canvas Vectorize pill can open the panel
        # pre-filled, mirroring the dock CTA. None when the last run produced
        # nothing vectorizable (no template hints, no flat-color output).
        self._vectorize_suggestion: tuple[str, str | None, str, str] | None = None
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
        # Guards the auto-opened error-report dialog to at most one popup per
        # generation attempt; reset alongside the flags above when a new
        # generation starts (_on_export_completed).
        self._error_report_dialog_shown = False

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
