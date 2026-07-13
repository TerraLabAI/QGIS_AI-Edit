from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QKeySequence, QShortcut
from qgis.PyQt.QtWidgets import QDockWidget

from ...core import qt_compat as QtC
from ...core.i18n import tr
from ...core.reference_image_store import ReferenceImageStore
from ...core.resolution_labels import DEFAULT_RESOLUTION_CREDIT_COSTS
from ..panel_helpers import make_section_header
from .account import DockAccountMixin
from .build import build_ui
from .chrome import DockChromeMixin
from .generation_state import DockGenerationStateMixin
from .library import DockLibraryMixin
from .prompts import DockPromptMixin
from .tools_footer import DockToolsFooterMixin
from .versions import DockVersionsMixin

_make_section_header = make_section_header  # backward-compat alias


class AIEditDockWidget(
    DockChromeMixin,
    DockAccountMixin,
    DockLibraryMixin,
    DockGenerationStateMixin,
    DockVersionsMixin,
    DockPromptMixin,
    DockToolsFooterMixin,
    QDockWidget,
):
    """Dock widget with prompt-first flow.

    The prompt view is always visible after activation. The selection tool
    stays active so the user can draw a zone at any time. The Generate
    button is disabled (shows "Select your zone") until a zone is drawn.
    """

    stop_clicked = pyqtSignal()
    generate_clicked = pyqtSignal(str)
    # Post-generation base picked in the version strip (0 = Original, i = the
    # i-th generated version). The plugin mirrors it on the canvas and uses it
    # to pick the export base + parent for the next edit.
    base_version_selected = pyqtSignal(int)
    retry_clicked = pyqtSignal(str)       # retry on same zone with (possibly edited) prompt
    pairing_requested = pyqtSignal(str)        # one-click connect: emits the minted pairing code
    pairing_cancel_requested = pyqtSignal(str)  # user cancelled the browser handoff (emits the code)
    settings_clicked = pyqtSignal()
    launch_clicked = pyqtSignal()          # user clicked "Launch AI Edit" on entry screen
    try_example_requested = pyqtSignal()   # empty-canvas one-click onboarding (demo basemap only)
    exit_clicked = pyqtSignal()            # user clicked the always-visible Exit button
    zone_clear_requested = pyqtSignal()    # Escape pressed while a zone was selected
    markup_clicked = pyqtSignal()          # user picked Tools → Mark up
    vectorize_clicked = pyqtSignal()       # user picked Tools → Vectorize
    # (layer_id, color_hex, class_label, trigger) from the "Vectorize this
    # result" CTA in the result panel. class_label seeds the class_name
    # attribute on every produced polygon (empty for mono-class templates
    # that lack a server-side label); trigger says what surfaced the CTA
    # (template | freeform_verb | flat_output) for the telemetry funnel.
    vectorize_suggestion_clicked = pyqtSignal(str, str, str, str)
    # Footer Before/After is a checkable toggle: True = user wants the
    # swipe map tool armed, False = user wants it disarmed. The plugin
    # routes both states to the SwipeController.
    swipe_toggled = pyqtSignal(bool)
    markup_done_clicked = pyqtSignal()     # user clicked Done in Mark up panel
    markup_clear_clicked = pyqtSignal()    # user clicked Clear all in Mark up
    markup_tool_changed = pyqtSignal(str)  # 'pencil' | 'arrow' | 'circle'
    markup_color_changed = pyqtSignal(QColor)
    vectorize_done_clicked = pyqtSignal()  # user clicked Done in Vectorize panel
    # (template_id, template_name) for analytics - id is stable, name is human-readable.
    template_selected = pyqtSignal(str, str)
    # Fired when the prompt library opens; plugin listens and kicks off a
    # background catalog refetch so the NEXT open shows the latest server
    # state. Stale-while-revalidate: this open uses whatever the dock has
    # cached, the refetch updates `self._server_catalog` for next time.
    catalog_refresh_requested = pyqtSignal()
    # A past generation (history row dict) the user wants re-added to the map
    # as a georeferenced layer, or downloaded to disk. The plugin owns the
    # download + write + layer-add orchestration.
    history_add_to_map = pyqtSignal(dict)
    history_download = pyqtSignal(dict)
    # A past generation the user chose to fully reproduce: the plugin restores
    # the prompt, the reference image(s), and the original zone on the map.
    history_restore = pyqtSignal(dict)
    # Fired when the Help (?) menu opens (True) or closes (False). The
    # plugin uses this to light the green active tint on the help button
    # and to disarm the swipe map tool when the user opens another action.
    help_menu_open_changed = pyqtSignal(bool)

    def __init__(self, parent=None, reference_store: ReferenceImageStore | None = None):
        super().__init__(tr("AI Edit by TerraLab"), parent)
        # Stable objectName lets QGIS save/restore the dock (position + visibility) across
        # sessions, like the native Layers panel.
        self.setObjectName("AIEditDockWidget")
        self.setAllowedAreas(QtC.LeftDockWidgetArea | QtC.RightDockWidgetArea)
        # Scale min width with font so hi-DPI displays don't crop the footer.
        try:
            char_w = self.fontMetrics().averageCharWidth()
            self.setMinimumWidth(max(300, int(char_w * 50)))
        except Exception:
            self.setMinimumWidth(300)
        self._reference_store = reference_store
        self._library_client = None
        self._library_auth_manager = None
        self._server_catalog: dict | None = None

        # Cache of the prompt library's Recent + Favorites, so reopening the
        # library is instant instead of refetching + blank-then-fill each time.
        # Seeded from a persistent disk cache so even the FIRST open of a session
        # renders immediately (then a background refresh picks up any changes);
        # marked dirty so that refresh always runs once per session and after a
        # new generation.
        from ...core.prompts import history_cache as _history_cache

        self._library_recent_cache: list = _history_cache.get_recent_jobs()
        self._library_favorite_cache: list = _history_cache.get_favorite_jobs()
        self._library_history_loaded = bool(
            self._library_recent_cache or self._library_favorite_cache
        )
        self._library_history_dirty = True

        # Armed template: set when the user picks a preset from the prompt
        # library so edits to the prompt text don't drop the association
        # (used by plugin.py to keep vector hints + Vectorize CTA active).
        self._active_template_id: str | None = None
        self._active_template_name: str | None = None

        # Parented so the 12 s shot dies with the dock, not against a deleted widget.
        self._status_hide_timer: QTimer | None = None

        # Global Escape: exit the flow no matter where focus is (canvas while
        # drawing a zone, prompt textarea, progress bar, etc.). WindowShortcut
        # context lets the shortcut fire on the parent main window's key events
        # via ShortcutOverride, which beats the map tool's local Escape handler.
        self._escape_shortcut = QShortcut(QKeySequence(QtC.Key_Escape), self)
        self._escape_shortcut.setContext(QtC.WindowShortcut)
        self._escape_shortcut.activated.connect(self._on_escape_pressed)

        # Global Enter / Return: launch generation from anywhere in the dock.
        # The prompt textarea consumes Return in its own keyPressEvent so this
        # shortcut only fires when focus is on a non-text-input child.
        self._generate_shortcut_return = QShortcut(QKeySequence(QtC.Key_Return), self)
        self._generate_shortcut_return.setContext(QtC.WindowShortcut)
        self._generate_shortcut_return.activated.connect(self._on_generate_shortcut)
        self._generate_shortcut_enter = QShortcut(QKeySequence(QtC.Key_Enter), self)
        self._generate_shortcut_enter.setContext(QtC.WindowShortcut)
        self._generate_shortcut_enter.activated.connect(self._on_generate_shortcut)

        self._setup_title_bar()

        build_ui(self)

        # State
        self._zone_selected = False
        # While an onboarding basemap warms its online tiles, Generate is held
        # so the first-run demo cannot export a blank input (crop error).
        self._imagery_loading = False
        self._activated = False
        self._checking_credits = False
        self._swipe_eligible = False
        self._swipe_panel_lock = False
        self._is_free_tier = True  # default hidden until confirmed Pro
        self._cached_used: int | None = None
        self._cached_limit: int | None = None
        # Pre-confirmation seed. Paid accounts are bumped to the "2K"
        # (Detailed) default once set_credits confirms the tier; free tier
        # keeps getting coerced to "1K". A manual pick always wins
        # (_resolution_user_choice) so tier refreshes never override it.
        self._selected_resolution = "1K"
        self._resolution_user_choice = False
        # Credit cost per resolution. Used to suffix the Generate/Regenerate
        # button text ("Generate (30 credits)"). Overwritten by
        # set_resolution_credit_costs once the server config loads.
        self._resolution_credit_costs: dict[str, int] = dict(DEFAULT_RESOLUTION_CREDIT_COSTS)

        # Layer monitoring. We listen to add/remove, visibility-changed in the
        # legend, AND project lifecycle (readProject/cleared) so the Launch
        # button stays in sync when the user starts a new project or opens a
        # different one - those transitions replace the layerTreeRoot, which
        # invalidates any visibilityChanged binding made before.
        # layersAdded/layersRemoved fire before QGIS finishes syncing the layer
        # tree, so the new node is not yet in layerTreeRoot().findLayers() when a
        # synchronous handler runs. Defer the gate re-check by one event loop tick
        # (same pattern as _on_project_loaded) so adding the first basemap on a
        # fresh session actually enables the Launch button.
        QgsProject.instance().layersAdded.connect(self._schedule_layer_warning_update)
        QgsProject.instance().layersRemoved.connect(self._schedule_layer_warning_update)
        QgsProject.instance().layerTreeRoot().visibilityChanged.connect(
            self._update_layer_warning
        )
        QgsProject.instance().readProject.connect(self._on_project_loaded)
        QgsProject.instance().cleared.connect(self._on_project_loaded)
        self._update_layer_warning()

    def _on_escape_pressed(self):
        """Escape walks the flow back one step at a time.

        SWIPE ACTIVE → disarm swipe (highest priority, the canvas-tool
        Escape handler only fires when canvas has focus, but the swipe
        button stays checked otherwise; route the dock-level Escape
        through here so swipe always exits cleanly).
        ZONE_SELECTED → SELECTING_ZONE (drop the zone, keep the panel open).
        SELECTING_ZONE / LAUNCH / RESULT → exit to LAUNCH.
        A generation in progress is never cancelable by Escape - credits are
        already booked; only the Stop button can cancel.
        """
        if not self.isVisible() or not self._main_widget.isVisible():
            return
        if self._progress_widget.isVisible():
            return
        # WindowShortcut means the dock receives Escape from anywhere in the
        # QGIS main window. Bail out unless the user is genuinely interacting
        # with AI Edit (canvas focused with our map tool, or focus is inside
        # the dock itself) so we don't steal Escape from QGIS digitizing,
        # measure tool, identify panel, etc.
        if not self._is_escape_for_us():
            return
        # Swipe takes priority: clicking the (already-checked) button
        # toggles it off, which routes through swipe_toggled → plugin →
        # swipe_controller.stop().
        if self._swipe_btn.isChecked():
            self._swipe_btn.click()
            return
        if self._zone_selected and self._prompt_section.isVisible():
            self.zone_clear_requested.emit()
            return
        self.exit_clicked.emit()

    def _is_escape_for_us(self) -> bool:
        """Decide whether an Escape keypress should drive AI Edit's flow.

        True when focus is inside the dock, OR the canvas currently runs
        one of our map tools (rectangle selection / Mark up pencil/arrow/
        circle). Anywhere else, Escape belongs to the active QGIS tool.
        """
        from qgis.PyQt.QtWidgets import QApplication

        focus = QApplication.focusWidget()
        if focus is not None:
            w = focus
            while w is not None:
                if w is self:
                    return True
                w = w.parent()
        try:
            from qgis.utils import iface as _iface
            if _iface is None:
                return False
            tool = _iface.mapCanvas().mapTool()
        except Exception:
            return False
        if tool is None:
            return False
        from ..panels.swipe_panel import _SwipeMapTool
        from ..tools.markup_tools import _MarkupBaseMapTool
        from ..tools.selection_map_tool import RectangleSelectionTool
        return isinstance(
            tool, (RectangleSelectionTool, _MarkupBaseMapTool, _SwipeMapTool)
        )

    def closeEvent(self, event):
        """Visibility-only teardown. Persistent disconnects live in cleanup()."""
        self._stop_progress_animation()
        if self._progress_widget.isVisible():
            self.stop_clicked.emit()
        self._vectorize_panel.deactivate()
        super().closeEvent(event)

    def cleanup(self):
        """Called once from plugin.unload() before the dock is removed."""
        try:
            QgsProject.instance().layersAdded.disconnect(self._schedule_layer_warning_update)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layersRemoved.disconnect(self._schedule_layer_warning_update)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layerTreeRoot().visibilityChanged.disconnect(
                self._update_layer_warning
            )
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().readProject.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().cleared.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass
        # LayerTreeComboBox hooks its own QgsProject signals; nothing else cleans it.
        try:
            combo = getattr(self._vectorize_panel, "_layer_combo", None)
            if combo is not None and hasattr(combo, "cleanup"):
                combo.cleanup()
        except Exception:  # nosec B110
            pass
