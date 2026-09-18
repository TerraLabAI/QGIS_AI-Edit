from __future__ import annotations

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ..icons import logo_pixmap, logo_size, pixmap_for
from .design_tokens import (
    BODY_QSS,
    BTN_GHOST_QSS,
    BTN_LINK_QSS,
    BTN_PRIMARY_WIDE_PX,
    BTN_PRIMARY_WIDE_QSS,
    BTN_QUIET_QSS,
    CARD_QSS,
    GREEN,
    HEADLINE_QSS,
    HINT_QSS,
    SPACE_CARD,
    SPACE_STAGE,
    qcolor,
)
from .widgets import _Spinner

# Sign-in spinner rotation tick.
_PAIRING_SPINNER_MS = 80
# The mark above the signed-out headline.
_SIGNIN_MARK_PX = 40


def _make_card(object_name: str = "card") -> QFrame:
    """A surface on a hairline, 10 px corners (design_tokens.CARD_QSS)."""
    card = QFrame()
    card.setObjectName(object_name)
    card.setAttribute(QtC.WA_StyledBackground, True)
    card.setStyleSheet(CARD_QSS + "QLabel { background: transparent; border: none; }")
    return card


class DockChromeMixin:
    """Title bar, sign-in section, empty-canvas card and shared-widget
    placement for AIEditDockWidget. The update card is update_banner.py."""

    def _setup_title_bar(self):
        """AI Agent's header as the dock's title bar (dock_header.py)."""
        from .dock_header import build_dock_header

        self._header_bar = build_dock_header(self)
        self.setTitleBarWidget(self._header_bar)

    def _build_activation_section(self) -> QWidget:
        """The signed-out screen, on AI Agent's line: the mark, one headline,
        the wide "Sign in" primary, and a card of what the free plan gives.
        While the browser handoff runs, the same place shows a waiting card."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 20, 4, 4)
        layout.setSpacing(SPACE_STAGE)

        mark = QLabel()
        mark.setFixedSize(logo_size(_SIGNIN_MARK_PX))
        mark.setPixmap(logo_pixmap(mark, _SIGNIN_MARK_PX))
        layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)

        self._setup_header = QLabel(
            get_export_copy("dock.chrome.setup_header", tr("Edit your map with AI"))
        )
        self._setup_header.setAlignment(QtC.AlignCenter)
        self._setup_header.setWordWrap(True)
        self._setup_header.setStyleSheet(HEADLINE_QSS)
        layout.addWidget(self._setup_header)
        layout.addSpacing(4)

        # --- Primary: one tap to sign in (browser handoff, no copy-paste) ---
        self._connect_section = QWidget()
        connect_layout = QVBoxLayout(self._connect_section)
        connect_layout.setContentsMargins(0, 0, 0, 0)
        connect_layout.setSpacing(SPACE_STAGE)

        self._connect_btn = QPushButton(
            get_export_copy("dock.chrome.connect_btn", tr("Sign in / Sign up to start"))
        )
        self._connect_btn.setToolTip(get_export_copy(
            "dock.chrome.connect_btn_tooltip", tr("Sign in via your browser to start using AI Edit")
        ))
        self._connect_btn.setFixedHeight(BTN_PRIMARY_WIDE_PX)
        self._connect_btn.setCursor(QtC.PointingHandCursor)
        self._connect_btn.setStyleSheet(BTN_PRIMARY_WIDE_QSS)
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        connect_layout.addWidget(self._connect_btn)

        # What the free plan gives, in words (the same card as AI Agent's and
        # AI Segmentation's sign-in screens). The monthly number is SERVED,
        # never written here, so the first promise cannot drift from the grant.
        hint_card = _make_card("card")
        hint_card_layout = QVBoxLayout(hint_card)
        hint_card_layout.setContentsMargins(12, 10, 12, 10)
        hint_card_layout.setSpacing(SPACE_CARD + 2)
        from ...core.paywall_state import advertised_free_generations

        for line in (
            tr("Free plan, {n} AI edits every month. Signing up takes 15 "
               "seconds in your browser.").replace(
                   "{n}", str(advertised_free_generations())),
            get_export_copy(
                "dock.chrome.signin_hint_line2",
                tr("Then type what to change on your imagery, and get the result "
                   "back as a georeferenced layer."),
            ),
        ):
            row = QHBoxLayout()
            row.setSpacing(8)
            check = QLabel()
            check.setFixedSize(14, 16)
            check.setPixmap(pixmap_for(check, "check", 14, qcolor(GREEN)))
            row.addWidget(check, 0, QtC.AlignTop)
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(BODY_QSS)
            row.addWidget(lbl, 1)
            hint_card_layout.addLayout(row)
        connect_layout.addWidget(hint_card)

        layout.addWidget(self._connect_section)

        # --- Waiting state: shown while the browser handoff is in progress ---
        self._pairing_wait_section = _make_card("card")
        wait_layout = QVBoxLayout(self._pairing_wait_section)
        wait_layout.setContentsMargins(14, 14, 14, 12)
        wait_layout.setSpacing(SPACE_STAGE)

        # Spinner + status text on one row (no jumping dots).
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._pairing_spinner = _Spinner(16)
        status_row.addWidget(self._pairing_spinner, 0, QtC.AlignTop)
        self._pairing_status = QLabel(
            get_export_copy("dock.chrome.pairing_waiting", tr("Finish signing in on the page that just opened"))
        )
        self._pairing_status.setWordWrap(True)
        self._pairing_status.setStyleSheet(BODY_QSS)
        status_row.addWidget(self._pairing_status, 1)
        wait_layout.addLayout(status_row)

        # Open again (ghost) and Cancel (quiet): neither is the screen's
        # primary, the browser is.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACE_CARD)
        self._pairing_reopen_btn = QPushButton(get_export_copy("dock.chrome.pairing_reopen_btn", tr("Open again")))
        self._pairing_reopen_btn.setToolTip(get_export_copy(
            "dock.chrome.pairing_reopen_tooltip", tr("Didn't open? Open the page again")
        ))
        self._pairing_reopen_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_reopen_btn.setStyleSheet(BTN_GHOST_QSS)
        self._pairing_reopen_btn.clicked.connect(self._on_pairing_reopen_clicked)
        btn_row.addWidget(self._pairing_reopen_btn, 1)

        self._pairing_cancel_btn = QPushButton(get_export_copy("dock.chrome.pairing_cancel_btn", tr("Cancel")))
        self._pairing_cancel_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_cancel_btn.setStyleSheet(BTN_QUIET_QSS)
        self._pairing_cancel_btn.clicked.connect(self._on_pairing_cancel_clicked)
        btn_row.addWidget(self._pairing_cancel_btn, 0)
        wait_layout.addLayout(btn_row)

        # Copy the connect link so the user can finish sign-in in a different
        # browser (e.g. their default has no Google session). Standard CLI
        # device-flow fallback ("open browser, or copy this link").
        self._pairing_copy_btn = QPushButton(
            get_export_copy("dock.chrome.pairing_copy_link_btn", tr("Link not opening? Copy link"))
        )
        self._pairing_copy_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_copy_btn.setStyleSheet(BTN_LINK_QSS)
        self._pairing_copy_btn.clicked.connect(self._on_pairing_copy_clicked)
        wait_layout.addWidget(self._pairing_copy_btn, 0, QtC.AlignCenter)

        self._pairing_wait_section.setVisible(False)
        self._pairing_active = False
        layout.addWidget(self._pairing_wait_section)

        # One timer rotates the spinner while waiting. Parented to the dock
        # (segfault-safe) and stopped the moment the wait section hides.
        self._pairing_anim_timer = QTimer(self)
        self._pairing_anim_timer.setInterval(
            get_export_dial("dock.chrome.pairing_spinner_ms", _PAIRING_SPINNER_MS)
        )
        self._pairing_anim_timer.timeout.connect(self._pairing_spinner.advance)
        self._pending_pairing_code = ""
        self._pairing_link = ""

        layout.addStretch(1)

        # Activation message (errors / success), coloured by set_activation_message.
        self._activation_message = QLabel("")
        self._activation_message.setAlignment(QtC.AlignCenter)
        self._activation_message.setWordWrap(True)
        self._activation_message.setStyleSheet(HINT_QSS)
        self._activation_message.setVisible(False)
        layout.addWidget(self._activation_message)

        return widget

    def _build_warning_widget(self) -> QWidget:
        """Build the empty-canvas first-run hero (shown when no visible layer).

        The empty state IS the onboarding. It leads with the truth the user
        must act on - the imagery is THEIRS to bring (any GeoTIFF / WMS / XYZ)
        - and keeps a one-click "Load a sample image" demo as the reassurance
        fallback for someone with no data on hand (Yvann 2026-07-08). The
        ghost secondary button adapts to the actual blocker (see
        _sync_warning_actions): Data Source Manager when the project holds no
        layers, one-click re-check of the topmost raster when layers exist
        but are all unchecked. It mirrors
        the AI Segmentation hero pixel for pixel so the two docks read as one
        family. No illustrative preview image: real product output only, a
        glyph is fine. The plugin handles the demo: it only adds a basemap
        and frames the scene; drawing a zone and writing a prompt stays the
        user's own step, same as with any imagery they bring in.

        Layout: a transparent, vertically-EXPANDING wrapper holds the compact
        blue-tinted card at the TOP with a single stretch below it, so when it
        is added with stretch factor 1 the card pins to the top and the surplus
        falls below. The plugin reads top-to-bottom, so the empty state starts
        at the top too (Yvann 2026-07-08), never centered."""
        wrapper = QWidget()
        wrapper.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        from ..icons import icon_for, pixmap_for
        from . import design_tokens as tokens

        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # AI Agent's empty line, no card: a glyph, one question, one quiet
        # line, then the two ways forward, centred in a column that stops
        # growing past a comfortable reading width.
        card = QWidget()
        card.setObjectName("firstRunHero")
        card.setMaximumWidth(320)
        card.setStyleSheet(
            "QWidget#firstRunHero { background: transparent; border: none; }"
            "QLabel { background: transparent; border: none; }"
        )
        col = QVBoxLayout(card)
        # 20 px from the top, the entry hero's and the sign-in screen's own
        # margin, so the glyph sits where the mark sits on the other screens.
        col.setContentsMargins(8, 20, 8, 16)
        col.setSpacing(6)

        glyph = QLabel()
        glyph.setAlignment(QtC.AlignCenter)
        glyph.setPixmap(pixmap_for(glyph, "layers", 28, tokens.qcolor(tokens.INK_2)))
        col.addWidget(glyph)
        col.addSpacing(SPACE_STAGE - 6)

        self._warning_title = QLabel(
            get_export_copy("dock.chrome.warning_title", tr("What would you like to edit?"))
        )
        self._warning_title.setWordWrap(True)
        self._warning_title.setAlignment(QtC.AlignCenter)
        # The headline token every home screen uses (entry, sign-in).
        self._warning_title.setStyleSheet(HEADLINE_QSS)
        col.addWidget(self._warning_title)

        # One quiet line, one job: name what counts as imagery. No workflow
        # prose. Kept as _warning_text so show_basemap_error can swap it and
        # _sync_warning_actions can retune it per state.
        # "Add", not "Turn on": this line shows when the project holds no
        # layer at all, where there is nothing to turn on (the hidden-layers
        # state has its own line, _sync_warning_actions).
        self._warning_text = QLabel(
            get_export_copy(
                "dock.chrome.warning_text_no_layer",
                tr("Add a layer, or start with a sample."),
            )
        )
        self._warning_text.setWordWrap(True)
        self._warning_text.setAlignment(QtC.AlignCenter)
        # The home screen's subline (HINT_QSS on the entry screen): the same
        # line under the same headline, whichever empty state shows.
        self._warning_text.setStyleSheet(HINT_QSS)
        col.addWidget(self._warning_text)
        col.addSpacing(14)

        # The headline names the blocker, so the screen owes the user the door
        # past it. Ghost, not filled: the sample button below is the screen's
        # single filled primary and this one must recede. Text, tooltip and
        # click target are state-dependent (_sync_warning_actions).
        wide_ghost = tokens.BTN_GHOST_QSS + (
            f"QPushButton {{ min-height: {tokens.BTN_PRIMARY_WIDE_PX - 2}px;"
            f" border-radius: {tokens.RADIUS_PILL_WIDE}px; font-size: {tokens.FONT_BASE}px; }}"
        )
        self._warning_show_layers_mode = False
        self._warning_error_text_active = False
        self._add_layer_btn = QPushButton(get_export_copy("dock.chrome.add_layer_btn", tr("Add a layer…")))
        self._add_layer_btn.setToolTip(
            get_export_copy(
                "dock.chrome.add_layer_btn_data_tooltip",
                tr("Open QGIS's Data Source Manager to add data"),
            )
        )
        self._add_layer_btn.setCursor(QtC.PointingHandCursor)
        self._add_layer_btn.setStyleSheet(wide_ghost)
        self._add_layer_btn.setIcon(icon_for(self._add_layer_btn, "plus", 14, tokens.qcolor(tokens.INK_2)))
        self._add_layer_btn.clicked.connect(self._on_add_layer_clicked)
        col.addWidget(self._add_layer_btn)

        # 'or' divider: the structural device that splits the two real paths
        # (bring your own vs. try a sample), so the sample reads as the
        # fallback without a sentence spelling it out.
        def _rule():
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background-color: {tokens.LINE_STRONG}; border: none;")
            return line

        div = QHBoxLayout()
        div.setContentsMargins(12, 0, 12, 0)
        div.setSpacing(10)
        or_lbl = QLabel(get_export_copy("dock.chrome.or_label", tr("or")))
        or_lbl.setStyleSheet(f"font-size: {tokens.FONT_HINT}px; color: {tokens.INK_3};")
        div.addWidget(_rule(), 1)
        div.addWidget(or_lbl, 0)
        div.addWidget(_rule(), 1)
        col.addSpacing(4)
        col.addLayout(div)
        col.addSpacing(4)

        # The one filled primary: the empty canvas is the funnel's cliff, and
        # this is the one-click way off it. The label names what the click
        # delivers, an image on the canvas, because "try an example" left
        # users guessing.
        self._basemap_btn = QPushButton(
            get_export_copy("dock.chrome.basemap_btn", tr("Load a sample image"))
        )
        self._basemap_btn.setCursor(QtC.PointingHandCursor)
        self._basemap_btn.setStyleSheet(tokens.BTN_PRIMARY_WIDE_QSS)
        self._basemap_btn.clicked.connect(self._on_try_example_clicked)
        col.addWidget(self._basemap_btn)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(card, 100)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        return wrapper

    def _sync_warning_actions(self) -> None:
        """Point the hero's secondary button at the actual blocker. Two states,
        re-decided every time the hero (re)shows via _update_layer_warning:

        - Project holds NO raster: "Add a layer…" opens the Data Source
          Manager (the imagery really is missing; vectors alone give "Image to
          edit" nothing to offer).
        - Project holds rasters but every one is unchecked: "Show my layers"
          re-checks the topmost raster. Dumping this user into the full import
          dialog when the data is already in the project was the complaint
          that created this split.
        """
        from qgis.core import QgsProject, QgsRasterLayer

        root = QgsProject.instance().layerTreeRoot()
        has_layers = any(
            isinstance(node.layer(), QgsRasterLayer) for node in root.findLayers()
        )
        self._warning_show_layers_mode = has_layers
        if has_layers:
            self._warning_title.setText(
                get_export_copy("dock.chrome.warning_title_hidden_layers", tr("Your layers are hidden"))
            )
            self._add_layer_btn.setText(get_export_copy("dock.chrome.show_layers_btn", tr("Show my layers")))
            self._add_layer_btn.setToolTip(get_export_copy(
                "dock.chrome.show_layers_btn_tooltip", tr("Re-check your topmost layer in the Layers panel")
            ))
            # A stale basemap-load error belongs to the no-layer state; the
            # hidden-layers line replaces it.
            self._warning_error_text_active = False
            # The same two ways as the no-layer line, in the button order:
            # the line used to describe the manual route the button beside it
            # does in one click.
            self._warning_text.setText(get_export_copy(
                "dock.chrome.warning_text_hidden_layers_v2",
                tr("Show a layer, or start with a sample."),
            ))
        else:
            self._warning_title.setText(
                get_export_copy("dock.chrome.warning_title", tr("What would you like to edit?"))
            )
            self._add_layer_btn.setText(get_export_copy("dock.chrome.add_layer_btn", tr("Add a layer…")))
            self._add_layer_btn.setToolTip(
                get_export_copy(
                    "dock.chrome.add_layer_btn_data_tooltip",
                    tr("Open QGIS's Data Source Manager to add data"),
                )
            )
            if not self._warning_error_text_active:
                self._warning_text.setText(get_export_copy(
                    "dock.chrome.warning_text_no_layer",
                    tr("Add a layer, or start with a sample."),
                ))

    def _reveal_topmost_layer(self) -> None:
        """Re-check the topmost raster in the layer tree (topmost layer of any
        kind when no raster exists), plus any unchecked ancestor group so the
        map actually shows pixels. The layerTreeRoot's visibilityChanged
        binding then re-runs _update_layer_warning and the hero retires on
        its own; nothing here touches the dock state directly."""
        from qgis.core import QgsProject, QgsRasterLayer

        root = QgsProject.instance().layerTreeRoot()
        nodes = [n for n in root.findLayers() if n.layer() is not None]
        if not nodes:
            return
        node = next(
            (n for n in nodes if isinstance(n.layer(), QgsRasterLayer)),
            nodes[0],
        )
        node.setItemVisibilityChecked(True)
        parent = node.parent()
        while parent is not None and parent is not root:
            parent.setItemVisibilityChecked(True)
            parent = parent.parent()

    def _on_add_layer_clicked(self) -> None:
        """The hero's secondary button: reveal the topmost hidden layer when
        the project already has layers (see _sync_warning_actions), else open
        QGIS's own Data Source Manager. QgisInterface exposes no action for
        that dialog, and its ``openDataSourceManagerPage`` only landed in QGIS
        3.30, below which this plugin still runs; the main window's
        ``mActionDataSourceManager`` has carried it since QGIS 3.0, so it goes
        first and the newer call is the fallback. When neither exists the
        click does nothing: the example button below stays the way out."""
        if self._warning_show_layers_mode:
            self._reveal_topmost_layer()
            return
        try:
            from qgis.utils import iface

            action = iface.mainWindow().findChild(
                QtC.QAction, "mActionDataSourceManager"
            )
            if action is not None:
                action.trigger()
                return
            iface.openDataSourceManagerPage(None)
        except Exception:
            pass  # nosec B110  An old or headless QGIS must not raise here.

    def _on_try_example_clicked(self):
        """One-click unblock for the empty-canvas gate. The heavy lifting (add
        a basemap, frame the demo scene) lives in the plugin, which owns the
        canvas; the dock only asks for it and stays a pure state machine. The
        user still draws their own zone and prompt from there.

        Behind the server kill switch: the demo pulls tiles from an outside
        source, so it has to be switchable off without a release when that
        source is down."""
        if self._feature_blocked("demo"):
            return
        self.try_example_requested.emit()

    def _sync_demo_button(self) -> None:
        """Hide the example button when the demo is switched off server-side,
        so the first-run card offers only what actually works."""
        from ...core.auth.activation_manager import is_feature_enabled

        if getattr(self, "_basemap_btn", None) is not None:
            self._basemap_btn.setVisible(is_feature_enabled("demo"))

    def show_basemap_error(self):
        """Surface a load failure in the warning box (called by the plugin when
        neither the demo nor the fallback basemap could be added). The flag
        keeps _sync_warning_actions from overwriting it while the no-layer
        state lasts."""
        self._warning_error_text_active = True
        self._warning_text.setText(get_export_copy(
            "dock.chrome.basemap_load_error",
            tr("Couldn't load the example basemap. Check your internet "
               "connection, or add your own layer (GeoTIFF, WMS, XYZ)."),
        ))

    def _place_reference_widget(self, target: str) -> None:
        """Inject the shared refs strip into the active prompt container.

        ``target`` is "prompt" or "result". The strip lives above the textbox
        inside the bordered container, so the whole input area reads as a
        single ChatGPT-style attachment block.
        """
        if self._reference_widget is None:
            return
        container = (
            self._prompt_container if target == "prompt" else self._result_prompt_container
        )
        container.insert_refs_widget(self._reference_widget)
        # Visibility tracks the store: hidden when 0 refs, shown when ≥1.
        self._reference_widget.setVisible(self._reference_widget.count() > 0)
        self._reference_widget.setEnabled(True)
        # `set_generating(True)` flips this flag on every run, but the
        # generation-done path (set_generation_complete / set_initial_state)
        # never calls set_generating(False), so without this reset the flag
        # stays True and silently blocks +/paste/drop on every subsequent
        # attempt.
        self._reference_widget.set_readonly(False)
        # The Reference panel is a second view over the same store and is
        # locked in step with the strip, so it has to be released in step too.
        # It was only released in set_generating(False), which that same
        # generation-done path never reaches: both its import buttons stayed
        # greyed from the first successful generation to the end of the
        # session, with no way back short of restarting QGIS.
        panel = getattr(self, "_reference_panel", None)
        if panel is not None:
            panel.set_readonly(False)
        self._sync_attach_buttons()

    def _place_version_strip(self, target: str) -> None:
        """Re-home the version strip so the lineage stays visible across states.

        ``target``:

        - "result": its home, under the next-change prompt and its action
          row, right above the result tools (Compare, Vectorize);
        - "generating": under the progress bar, so the user keeps seeing the
          versions while the next edit renders;
        - "launch": the home screen. The strip goes back to its result home
          and hides whatever it holds. The in-flight slot lives inside
          ``_main_widget``, which the home screen keeps on screen, so a strip
          left there outlived the flow it belonged to: Exit or the dock's X
          during a run landed on "Edit your map with AI" with a dead lineage
          still listed under it, tiles and heading included.

        Moving between layouts reparents the single strip instance - it is
        never rebuilt, so tiles and selection survive the move.
        """
        self._main_layout.removeWidget(self._version_strip)
        self._result_prompt_layout.removeWidget(self._version_strip)
        if target == "generating":
            # Sit between the prompt and the progress bar (above it), not below.
            idx = self._main_layout.indexOf(self._progress_widget)
            self._main_layout.insertWidget(idx, self._version_strip)
        else:
            # Under the prompt, right above the result tools: the prompt leads
            # the result screen (Yvann, 2026-09-18).
            tools = getattr(self, "_result_tools_row", None)
            idx = (
                self._result_prompt_layout.indexOf(tools) if isinstance(tools, QWidget) else -1
            )
            if idx < 0:
                self._result_prompt_layout.addWidget(self._version_strip)
            else:
                self._result_prompt_layout.insertWidget(idx, self._version_strip)
        self._version_strip.setVisible(
            target != "launch" and self._version_strip.count() > 0
        )

    def _sync_attach_buttons(self) -> None:
        """Mirror the reference count onto both prompt containers, and hide
        the Reference chip when the server switches references off.

        Capacity no longer hides the chip (2026-08-03): it opens the
        Reference panel now, a management surface where a full store is
        exactly when the user wants in (edit notes, remove one). The
        capacity ceiling itself is enforced at add time by the store."""
        if self._reference_widget is None:
            return
        from ...core.auth.activation_manager import is_feature_enabled

        enabled = is_feature_enabled("references")
        count = self._reference_widget.count()
        if count > 0:
            self._mark_guide_ai_touched()
        for container in (self._prompt_container, self._result_prompt_container):
            container.set_attach_enabled(enabled)
            container.set_reference_count(count)
