from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QKeySequence
from qgis.PyQt.QtWidgets import QApplication, QDockWidget

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...core.qt_compat import QShortcut
from ...core.reference_image_store import ReferenceImageStore
from ...core.resolution_labels import DEFAULT_RESOLUTION_CREDIT_COSTS
from ..keyboard_focus import apply_keyboard_focus_policy
from ..panel_helpers import make_section_header
from .account import DockAccountMixin
from .blocked_reasons import DockBlockedReasonsMixin
from .build import build_ui
from .chrome import DockChromeMixin
from .dock_sizing import dock_minimum_height
from .generation_state import DockGenerationStateMixin
from .library import DockLibraryMixin
from .pro_ceiling import DockProCeilingMixin
from .pro_nudges import DockProNudgesMixin
from .prompts import DockPromptMixin
from .tools_footer import DockToolsFooterMixin
from .update_banner import DockUpdateBannerMixin
from .versions import DockVersionsMixin

# Private names are listed too: other modules import them from here.
__all__ = [
    "_make_section_header",
    "AIEditDockWidget",
]

_make_section_header = make_section_header  # backward-compat alias


class AIEditDockWidget(
    DockChromeMixin,
    DockUpdateBannerMixin,
    DockAccountMixin,
    DockBlockedReasonsMixin,
    DockLibraryMixin,
    DockGenerationStateMixin,
    DockProCeilingMixin,
    DockProNudgesMixin,
    DockVersionsMixin,
    DockPromptMixin,
    DockToolsFooterMixin,
    QDockWidget,
):
    """Dock widget with prompt-first flow.

    Entry screen (Launch), then the zone step, then the prompt with
    Generate and Cancel. Generate is grey, with the reason under it, until
    the zone and a long enough prompt are there.
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
    exit_clicked = pyqtSignal()            # Cancel (zone step or prompt), or Escape back to Launch
    zone_clear_requested = pyqtSignal()    # Escape pressed while a zone was selected
    markup_clicked = pyqtSignal()          # Draw chip or its shortcut
    vectorize_clicked = pyqtSignal()       # user picked Tools → Vectorize
    # References chip clicked (either prompt container): open the References
    # panel. Done in that panel routes back through reference_done_clicked.
    reference_panel_requested = pyqtSignal()
    reference_done_clicked = pyqtSignal()
    reference_capture_requested = pyqtSignal()  # "Map" chip: drag a rectangle on the canvas
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
    markup_done_clicked = pyqtSignal()     # user clicked Done in the Draw panel
    markup_clear_clicked = pyqtSignal()    # user clicked Clear all in the Draw panel
    markup_undo_clicked = pyqtSignal()     # user clicked Undo in the Draw panel
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
    # Sessions (emitter moving to the Prompt Library page): a row asked for a
    # delete or a rename (payload is the conversation entry dict), or an older
    # history page is wanted (payload is the oldest cached created_at).
    conversation_delete = pyqtSignal(dict)
    conversation_rename = pyqtSignal(dict)
    conversations_page_requested = pyqtSignal(str)
    # The sessions list opened on a cache that never synced (or whose last
    # sync failed): the plugin retries the history fetch.
    conversations_refresh_requested = pyqtSignal()
    # UNREACHABLE (2026-09-18): the header lost its Help menu (Settings holds
    # tutorials, shortcuts, contact and report), so nothing emits this. Kept
    # because plugin_parts/lifecycle.py still connects to it.
    help_menu_open_changed = pyqtSignal(bool)

    def __init__(self, parent=None, reference_store: ReferenceImageStore | None = None):
        super().__init__(get_export_copy("dock.widget.title", tr("AI Edit by TerraLab")), parent)
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
        # Same idea downwards: docks stacked in one area share the height, and
        # without a floor the neighbour above squeezes this one to its header.
        self.setMinimumHeight(dock_minimum_height(self))
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

        # Escape walks the flow back (canvas while drawing a zone, prompt
        # textarea, progress bar, etc.). WindowShortcut context lets the
        # shortcut fire on the main window's key events via ShortcutOverride,
        # which beats the map tool's local Escape handler. It is the ONLY
        # Escape owner: the Mark up and Vectorize panels route through it, since
        # two live shortcuts on one key are ambiguous and neither fires.
        # _refresh_dock_key_shortcuts keeps it disabled unless focus is in the
        # dock or on the canvas under one of our tools.
        self._escape_shortcut = QShortcut(QKeySequence(QtC.Key_Escape), self)
        self._escape_shortcut.setContext(QtC.WindowShortcut)
        self._escape_shortcut.activated.connect(self._on_escape_pressed)

        # Enter / Return: launch generation from the dock. The prompt textarea
        # consumes Return in its own keyPressEvent so this shortcut only fires
        # when focus is on a non-text-input child. Same enabled gate as Escape.
        self._generate_shortcut_return = QShortcut(QKeySequence(QtC.Key_Return), self)
        self._generate_shortcut_return.setContext(QtC.WindowShortcut)
        self._generate_shortcut_return.activated.connect(self._on_generate_shortcut)
        self._generate_shortcut_enter = QShortcut(QKeySequence(QtC.Key_Enter), self)
        self._generate_shortcut_enter.setContext(QtC.WindowShortcut)
        self._generate_shortcut_enter.activated.connect(self._on_generate_shortcut)

        # One reused timer for the Launch gate re-check (see
        # _schedule_layer_warning_update); the dirty flag defers it while the
        # dock is hidden.
        self._layer_warning_timer = QTimer(self)
        self._layer_warning_timer.setSingleShot(True)
        self._layer_warning_timer.timeout.connect(self._run_layer_warning_update)
        self._layer_warning_dirty = False

        self._setup_title_bar()

        build_ui(self)
        # Buttons take focus from Tab only: the blue ring is the keyboard's.
        apply_keyboard_focus_policy(self)

        # "Guide the AI" tip: retires for good once either grounding feature
        # is used, even once (see _mark_guide_ai_touched).
        self.markup_clicked.connect(self._mark_guide_ai_touched)

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
        # ISO renewal date served alongside used/limit (reset_date /
        # period_end), read by the wall's secondary "returns on {date}" line.
        # None until a real credits payload lands, or when the server has
        # nothing to say (see core.date_format.format_reset_date fallback).
        self._reset_date: str | None = None
        # Fire trial_exhausted_viewed / paywall_prewall_shown once per
        # continuous wall/pre-wall state, not on every credits refresh.
        # Reset whenever set_credits sees a balance outside that state.
        self._wall_telemetry_shown = False
        self._prewall_telemetry_shown = False
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
            self._schedule_layer_warning_update
        )
        QgsProject.instance().readProject.connect(self._on_project_loaded)
        QgsProject.instance().cleared.connect(self._on_project_loaded)
        self._update_layer_warning()

        # Keep the Escape / Return shortcuts' enabled flag in step with focus,
        # the active map tool and the dock's own visibility.
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._refresh_dock_key_shortcuts)
        canvas = self._map_canvas_or_none()
        if canvas is not None:
            canvas.mapToolSet.connect(self._refresh_dock_key_shortcuts)
        self.visibilityChanged.connect(self._refresh_dock_key_shortcuts)
        self._refresh_dock_key_shortcuts()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        # Buttons built after the first sweep (result rows, cards) join it.
        apply_keyboard_focus_policy(self)
        # Layer changes that landed while the dock was hidden skipped the
        # tree walk; run it once now.
        if self._layer_warning_dirty:
            self._schedule_layer_warning_update()
        # Hidden generations keep running, but their UI timers sleep while the
        # dock is away. Resume only the animation work that was still pending.
        prep_ticker = getattr(self, "_prep_ticker", None)
        if getattr(self, "_resume_prep_ticker_on_show", False) and prep_ticker is not None:
            prep_ticker.start()
        self._resume_prep_ticker_on_show = False
        progress_timer = getattr(self, "_progress_timer", None)
        if (
            progress_timer is not None
            and self._progress_widget.isVisibleTo(self)
            and self._progress_bar.value() < getattr(
                self, "_progress_target", self._progress_bar.value()
            )
        ):
            progress_timer.start()

    def hideEvent(self, event):  # noqa: N802
        prep_ticker = getattr(self, "_prep_ticker", None)
        self._resume_prep_ticker_on_show = bool(
            prep_ticker is not None and prep_ticker.isActive()
        )
        self._stop_prep_ticker()
        self._stop_progress_animation()
        super().hideEvent(event)

    @staticmethod
    def _map_canvas_or_none():
        try:
            from qgis.utils import iface as _iface
            return _iface.mapCanvas() if _iface is not None else None
        except Exception:
            return None

    def _refresh_dock_key_shortcuts(self, *_args) -> None:
        """Enable the dock's Escape and Return keys only while they belong to
        AI Edit. A WindowShortcut eats its key even when the slot then does
        nothing, so the gate is the enabled flag: Escape must reach the QGIS
        locator or attribute table, and Return typed there must never start
        a paid generation."""
        try:
            ours = self.isVisible() and self._is_escape_for_us()
            vectorize_open = self._vectorize_panel.isVisible()
            self._escape_shortcut.setEnabled(ours)
            # The Vectorize panel binds Return / Enter to Run itself.
            for shortcut in (self._generate_shortcut_return, self._generate_shortcut_enter):
                shortcut.setEnabled(ours and not vectorize_open)
        except (RuntimeError, AttributeError):
            pass

    def _on_escape_pressed(self):
        """Escape walks the flow back one step at a time.

        COMPARISON LIVE (before/after swipe armed) → end the comparison and
        stop there (first stage). The canvas-tool Escape handler only fires
        when the canvas has focus, and the × badge that used to sit beside the
        Compare pill is hidden while a comparison runs, so Escape is the
        keyboard way out and must never walk past it into clearing the zone.
        MID-DRAW (points already placed on the polygon zone tool) → clear
        those points, stay armed (see _active_polygon_draw_tool - the dock's
        global shortcut wins the keyboard race before the tool's own
        keyPressEvent, so it has to delegate here instead of letting the tool
        handle it directly).
        ZONE_SELECTED → SELECTING_ZONE (drop the zone, keep the panel open).
        SELECTING_ZONE / LAUNCH / RESULT → exit to LAUNCH.
        A generation in progress is never cancelable by Escape: credits are
        already booked, and the panel has no Stop on purpose.
        """
        if not self.isVisible():
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
        # Stage one, ahead of every other stage: a live before/after
        # comparison. The × badge is off the canvas for its whole duration
        # (polygon_selection_tool.set_compare_active), so Escape has to end the
        # comparison and stop, never carry on to clearing the zone. Clicking
        # the (already-checked) button toggles it off, which routes through
        # swipe_toggled → plugin → swipe_controller.stop() and brings the ×
        # back. No markup tool can be armed at the same time (opening one
        # disarms the swipe first), so this cannot steal their Escape.
        if self._swipe_btn.isChecked():
            self._swipe_btn.click()
            return
        # The markup Line tool draws while the Mark up panel hides
        # _main_widget, so its delegation must run BEFORE the visibility
        # early-return below or the shortcut eats the key and does nothing.
        line_tool = self._active_markup_line_tool()
        if line_tool is not None:
            line_tool.escape_step()
            return
        if not self._main_widget.isVisible():
            # A tool panel is open. Escape means its Done: the panels carry
            # no Escape shortcut of their own (see __init__).
            for name in ("_markup_panel", "_vectorize_panel", "_reference_panel"):
                panel = getattr(self, name, None)
                if panel is not None and panel.isVisible():
                    panel.done_clicked.emit()
                    return
            return
        draw_tool = self._active_polygon_draw_tool()
        if draw_tool is not None:
            draw_tool.clear_in_progress_drawing()
            return
        if self._zone_selected and self._prompt_section.isVisible():
            self.zone_clear_requested.emit()
            return
        self.exit_clicked.emit()

    def _is_escape_for_us(self) -> bool:
        """Decide whether an Escape keypress should drive AI Edit's flow.

        True when focus is inside the dock, OR the canvas runs one of our
        map tools (polygon zone selection / Mark up pencil/arrow/circle /
        swipe) and holds focus. Anywhere else (another QGIS panel, the
        locator), the key belongs to QGIS. Also gates Return / Enter.
        """
        def inside(widget, ancestor) -> bool:
            while widget is not None:
                if widget is ancestor:
                    return True
                widget = widget.parent()
            return False

        focus = QApplication.focusWidget()
        if focus is not None and inside(focus, self):
            return True
        canvas = self._map_canvas_or_none()
        if canvas is None:
            return False
        try:
            tool = canvas.mapTool()
        except Exception:
            return False
        if tool is None:
            return False
        from ..panels.swipe_panel import _SwipeMapTool
        from ..tools.markup_tools import _MarkupBaseMapTool
        from ..tools.polygon_selection_tool import PolygonSelectionTool
        if not isinstance(
            tool, (PolygonSelectionTool, _MarkupBaseMapTool, _SwipeMapTool)
        ):
            return False
        return focus is None or inside(focus, canvas)

    def _active_polygon_draw_tool(self):
        """The active canvas map tool, if it is AI Edit's polygon zone tool
        AND it currently has an in-progress (uncommitted) shape - else None.

        The dock's Enter/Return and Escape shortcuts are QShortcut(WindowShortcut),
        which win the keyboard race before the map tool's own keyPressEvent
        ever runs. Mid-draw, both shortcuts delegate to the tool through this
        helper instead of doing their normal thing (see _on_escape_pressed and
        DockPromptMixin._on_generate_shortcut).
        """
        from ..tools.polygon_selection_tool import PolygonSelectionTool

        try:
            from qgis.utils import iface as _iface
            if _iface is None:
                return None
            tool = _iface.mapCanvas().mapTool()
        except Exception:
            return None
        if isinstance(tool, PolygonSelectionTool) and tool.has_points():
            return tool
        return None

    def _active_markup_line_tool(self):
        """The active canvas map tool, if it is the markup Line tool - else None.

        Unlike :meth:`_active_polygon_draw_tool` this returns the tool even
        with no points placed: Escape's second stage (deactivate, un-check the
        panel button) must work from the dock shortcut too, since the tool's
        own keyPressEvent never sees the key (WindowShortcut race).
        """
        from ..tools.markup_line_tool import LineMapTool

        try:
            from qgis.utils import iface as _iface
            if _iface is None:
                return None
            tool = _iface.mapCanvas().mapTool()
        except Exception:
            return None
        if isinstance(tool, LineMapTool):
            return tool
        return None

    def closeEvent(self, event):
        """Visibility-only teardown. Persistent disconnects live in cleanup().

        A running generation is never stopped here: the header X is a hide
        like any other (the plugin's visibility handler says "still
        generating" and the result lands on the map), and its credits are
        already booked. ``stop_clicked`` used to fire from here, so the X
        cancelled a paid run while the toolbar toggle kept it."""
        self._stop_progress_animation()
        self._vectorize_panel.deactivate()
        super().closeEvent(event)

    def cleanup(self):
        """Called once from plugin.unload() before the dock is removed."""
        self.cleanup_account_check()
        self.disconnect_update_refresh()
        # removeDockWidget() + deleteLater() never fire closeEvent, so nothing
        # else stops the Vectorize panel: unloading mid-run left a QgsTask
        # grinding on a project the plugin no longer owns, with succeeded /
        # failed bound to a panel about to be destroyed.
        try:
            self._vectorize_panel.deactivate()
        except Exception:  # nosec B110
            pass
        try:
            self._vectorize_panel.cancel_eyedropper()
        except Exception:  # nosec B110
            pass
        try:
            QgsProject.instance().layersAdded.disconnect(self._schedule_layer_warning_update)
        except (TypeError, RuntimeError):  # never connected or project already gone
            pass
        try:
            QgsProject.instance().layersRemoved.disconnect(self._schedule_layer_warning_update)
        except (TypeError, RuntimeError):  # never connected or project already gone
            pass
        try:
            QgsProject.instance().layerTreeRoot().visibilityChanged.disconnect(
                self._schedule_layer_warning_update
            )
        except (TypeError, RuntimeError):  # never connected or layer tree already gone
            pass
        self._layer_warning_timer.stop()
        # focusChanged is application-wide: a slot left on it would fire into
        # a deleted dock after unload.
        try:
            app = QApplication.instance()
            if app is not None:
                app.focusChanged.disconnect(self._refresh_dock_key_shortcuts)
        except (TypeError, RuntimeError):
            pass
        try:
            canvas = self._map_canvas_or_none()
            if canvas is not None:
                canvas.mapToolSet.disconnect(self._refresh_dock_key_shortcuts)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().readProject.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):  # never connected or project already gone
            pass
        try:
            QgsProject.instance().cleared.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):  # never connected or project already gone
            pass
        # LayerTreeComboBox hooks its own QgsProject signals; nothing else cleans it.
        for combo in (
            getattr(self._vectorize_panel, "_layer_combo", None),
            getattr(self, "_layer_combo", None),
        ):
            try:
                if combo is not None and hasattr(combo, "cleanup"):
                    combo.cleanup()
            except Exception:  # nosec B110
                pass
