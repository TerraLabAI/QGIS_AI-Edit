from __future__ import annotations

import os

from ..api.terralab_client import TerraLabClient
from ..core.auth.auth_manager import AuthManager
from ..core.config_store import ConfigStore, set_store
from ..core.generation.generation_service import GenerationService
from ..core.logger import log_warning
from ..core.qt_compat import QAction, QShortcut
from ..core.reference_image_store import ReferenceImageStore
from ..workers.export_worker import ExportWorker
from .plugin_parts.activation import ActivationMixin
from .plugin_parts.conversations import ConversationsMixin
from .plugin_parts.generation import GenerationMixin
from .plugin_parts.generation_results import GenerationResultsMixin
from .plugin_parts.history import HistoryMixin
from .plugin_parts.launch_shortcut import LaunchShortcutMixin
from .plugin_parts.lifecycle import PluginLifecycleMixin
from .plugin_parts.onboarding import OnboardingMixin
from .plugin_parts.processing_registration import ProcessingRegistrationMixin
from .plugin_parts.reference_capture import ReferenceCaptureMixin
from .plugin_parts.startup import StartupMixin
from .plugin_parts.tool_panels import ToolPanelsMixin, _MarkupUndoFilter
from .plugin_parts.zone_versions import ZoneVersionsMixin
from .tools.markup_tools import MarkupLayerManager


class AIEditPlugin(
    PluginLifecycleMixin,
    LaunchShortcutMixin,
    ProcessingRegistrationMixin,
    StartupMixin,
    ActivationMixin,
    ZoneVersionsMixin,
    HistoryMixin,
    ConversationsMixin,
    ToolPanelsMixin,
    ReferenceCaptureMixin,
    GenerationMixin,
    GenerationResultsMixin,
    OnboardingMixin,
):


    def __init__(self, iface):
        self._iface = iface
        self._canvas = iface.mapCanvas()
        self._dock_widget = None
        self._map_tool = None
        self._swipe_controller = None
        self._action = None
        self._settings_action = None
        self._selected_extent = None




        self._selected_polygon = None
        self._worker = None


        self._export_worker: ExportWorker | None = None


        self._pending_generation: dict | None = None


        self._generation_cancel_handled = False


        self._generation_started_from_result = False
        self._selection_rubber_band = None
        self._selection_rubber_band_halo = None

        self._imagery_settle_timer = None
        self._imagery_cap_timer = None
        self._previous_map_tool = None
        self._terralab_toolbar = None
        self._export_config_loader = None
        self._credits_loader = None
        self._catalog_loader = None

        self._activation_config_loader = None

        self._bootstrap_task = None
        self._tuned_config_task = None
        self._export_size_task = None
        self._zone_size_task = None
        self._size_request_token = None


        self._history_tasks: list = []


        self._version_fetch_active = False

        self._last_image_b64 = None
        self._last_guidance_b64 = None
        self._last_guidance_format = None
        self._last_suggested_res = None

        self._last_completed_request_id: str | None = None



        self._versions: list[dict] = []
        self._selected_version_index = 0



        self._session_id: str | None = None
        self._key_validation_worker = None

        self._pairing_worker = None

        self._last_key_validation_unix: float = 0.0

        self._markup_manager: MarkupLayerManager | None = None
        self._markup_tool_objs: dict[str, object] = {}
        self._pre_markup_map_tool = None



        self._markup_maptool_set_connected = False

        self._markup_done_in_progress = False


        self._markup_outside_notice_active = False




        self._vectorize_suggestion: tuple[str, str | None, str, str] | None = None



        self._pills_armed = False


        self._markup_event_filter: _MarkupUndoFilter | None = None



        self._suppressed_undo_actions: list[tuple[QAction, bool]] = []


        self._launch_shortcut: QShortcut | None = None
        self._in_tool_panel: str | None = None



        self._toggling_dock = False
        self._selection_tool_was_active = False



        self._startup_bootstrap_done = False

        self._connectivity_notice_shown = False


        self._plugin_opened_emitted = False


        self._pending_open_source: str | None = None

        self._privacy_notice_dialog = None


        self._privacy_notice_on_accept = None

        self._first_generation_milestone_emitted = False


        self._last_generation_is_retry = False
        self._last_generation_used_markup = False



        self._error_report_dialog_shown = False


        self._dev_mode = False
        self._skip_trial_check = False


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

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(plugin_dir, ".env.local")
        env_vars: dict = {}
        try:
            if os.path.isfile(env_path):
                env_vars = self._load_env_file(env_path)
        except OSError as err:


            log_warning(f".env.local read failed, using defaults: {err}")
        self._dev_mode = env_vars.get("DEBUG", "").lower() == "true"
        self._skip_trial_check = env_vars.get("SKIP_TRIAL_CHECK", "").lower() == "true"
        if env_vars.get("RAW_PROMPT", "").lower() == "true":
            log_warning("DEV MODE: RAW_PROMPT is active - prompts sent bare (team allowlist enforced server side)")
        return TerraLabClient(env_vars=env_vars)

    @staticmethod
    def _load_env_file(path: str) -> dict:

        env: dict = {}
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
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
