"""Second half of the AIEditDockWidget widget-tree build (see build.py).

Result section, trial info box, side panels, footer, and
the scroll-area wrap. Called only from build.build_ui, in a fixed order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...core.logger import log_warning
from ...core.qt_compat import QAction, QShortcut
from ..input_decisions import pick_dock_shortcuts
from ..panels.markup_panel import MarkupPanel
from ..panels.reference_panel import ReferencePanel
from ..panels.vectorize_panel import VectorizePanel
from ..version_strip import VersionStrip
from .design_tokens import (
    BTN_GHOST_QSS,
    BTN_PRIMARY_WIDE_PX,
    BTN_PRIMARY_WIDE_QSS,
    FONT_HINT,
    HINT_QSS,
    INK_2,
    INSET,
    LINE,
    RADIUS_CARD,
    RADIUS_PILL_WIDE,
    SCROLL_AREA_QSS,
)
from .prompt_container import _PromptContainer
from .quota_card import QuotaCard
from .tool_bar import build_result_tools_row, build_tool_toggles, tool_available
from .widgets import _SubmitTextEdit

if TYPE_CHECKING:
    from .widget import AIEditDockWidget


class _RetiredSavedLine(QLabel):
    """The retired "Saved as <layer>" row of the result screen.

    The fact moved into the newest version's details card, where it sits with
    the version's other facts instead of eating a row above the pictures. The
    generation states still write to this label, so it stays a live widget
    with a parent (a parentless label that is shown becomes a floating
    window), and it answers every request to show itself with "no"."""

    def setVisible(self, visible: bool) -> None:  # noqa: N802 - Qt override
        del visible
        super().setVisible(False)


def _build_result_section(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # --- Result section (shown after generation complete, iteration flow) ---
    # A single prompt screen. The version strip under the prompt is the base
    # picker: Original is pinned left, each result appends to the right, and
    # the selected tile is what the next edit builds on. Lives inside
    # _result_section so every state transition that hides it hides together.
    dock._result_section = QWidget()
    dock._result_layout = QVBoxLayout(dock._result_section)
    dock._result_layout.setContentsMargins(0, 0, 0, 0)
    dock._result_layout.setSpacing(6)

    # --- The next prompt, then versions + tools -------------------------
    # Order (Yvann, 2026-09-18: "the prompt must be on top"): the empty box
    # for the next change with its Generate and New edit, then the compact
    # row of versions (which one the next edit starts from), then the two
    # tools that act on the result. The 2026-09-17 order put the versions
    # and tools above the prompt, and the screen read as a gallery.
    dock._result_prompt_widget = QWidget()
    dock._result_prompt_layout = QVBoxLayout(dock._result_prompt_widget)
    dock._result_prompt_layout.setContentsMargins(0, 0, 0, 0)
    dock._result_prompt_layout.setSpacing(6)

    # "Saved as <layer>" used to open this screen on a row of its own, which
    # read as noise next to the picture of the same result (Yvann,
    # 2026-09-17). The layer name and its click now live in the newest
    # version's details card (see DockVersionsMixin.saved_layer_probe). The
    # label stays as the sink the generation states still write to, parented
    # so it can never float as a window, and it never shows again.
    dock._layer_saved_label = _RetiredSavedLine(dock._result_section)
    dock._saved_layer_id: str | None = None

    # Version strip: the base picker. Original pinned left, results append
    # right, the picked tile drives the next edit. Hidden until seeded. Its
    # home is under the prompt's action row, right above the result tools
    # (_place_version_strip); built here so the tiles exist before any state
    # method runs.
    dock._version_strip = VersionStrip()
    dock._version_strip.version_selected.connect(dock._on_version_selected)
    dock._version_strip.set_saved_layer_probe(dock.saved_layer_probe)

    # Editable prompt (edit and retry)
    dock._result_prompt_input = _SubmitTextEdit()
    dock._result_prompt_input.setPlaceholderText(
        get_export_copy(
            "dock.build_result.next_change_placeholder", tr("Describe the next change")
        )
    )
    dock._result_prompt_input.document().setDocumentMargin(0)
    dock._result_prompt_input.setMinimumHeight(50)
    dock._result_prompt_input.setMaximumHeight(50)
    dock._result_prompt_input.submitted.connect(dock._on_retry_clicked)
    dock._result_prompt_input.textChanged.connect(dock._on_result_prompt_changed)
    dock._result_prompt_input.document().documentLayout().documentSizeChanged.connect(
        dock._adjust_result_prompt_height
    )
    dock._result_prompt_container = _PromptContainer(
        dock._result_prompt_input, dock._result_section
    )
    dock._result_prompt_container.templates_clicked.connect(
        dock._on_browse_templates_clicked
    )
    dock._result_prompt_container.resolution_changed.connect(
        dock._on_resolution_selected
    )
    dock._result_prompt_container.markup_clicked.connect(dock.markup_clicked.emit)
    if dock._reference_widget is not None:
        # Behind the same click-time kill switch as the idle container: the
        # five ways in must not diverge just because the user has a result
        # on screen.
        def _gated(handler):
            return dock._gated_by_feature("references", handler)

        dock._result_prompt_container.files_dropped.connect(
            _gated(dock._reference_widget.add_paths)
        )
        dock._result_prompt_container.layers_dropped.connect(
            _gated(dock._reference_widget.add_layers)
        )
        dock._result_prompt_container.reference_clicked.connect(
            _gated(dock.reference_panel_requested.emit)
        )
        dock._result_prompt_input.images_pasted.connect(
            _gated(dock._reference_widget.add_paths)
        )
    dock._result_prompt_layout.addWidget(dock._result_prompt_container)

    # Same soft off-rails hint as the first-run prompt, so iterating on a
    # v1/v2 gets the same guidance (vector / measure / chatbot).
    dock._result_guidance_hint = QLabel()
    dock._result_guidance_hint.setWordWrap(True)
    # A quiet notice: the inset step on a hairline, 10 px corners.
    dock._result_guidance_hint.setStyleSheet(
        f"QLabel {{ background: {INSET}; border: 1px solid {LINE};"
        f" border-radius: {RADIUS_CARD}px; padding: 8px 10px;"
        f" font-size: {FONT_HINT}px; color: {INK_2}; }}"
    )
    # Same AI Segmentation link handling as the first-run hint.
    dock._result_guidance_hint.setTextInteractionFlags(
        Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    dock._result_guidance_hint.setOpenExternalLinks(False)
    dock._result_guidance_hint.linkActivated.connect(dock._on_guidance_link_activated)
    dock._result_guidance_hint.setVisible(False)
    dock._result_prompt_layout.addWidget(dock._result_guidance_hint)

    # Action row: Generate from <version> (the one filled primary) + New edit
    # (ghost pill). New edit keeps the same handler as Cancel: the layer stays
    # in the project and the session stays in Sessions, which the notice after
    # it says out loud. It stays a ghost, because iterating again is still the
    # primary action. A separate Keep BUTTON read as a mystery third choice
    # (Yvann 2026-07-31), and the per-version keep mark that replaced it was
    # removed too (owner call 2026-08-03).
    result_actions_row = QHBoxLayout()
    result_actions_row.setContentsMargins(0, 6, 0, 0)
    result_actions_row.setSpacing(8)

    dock._result_regenerate_btn = QPushButton(get_export_copy("dock.build_result.generate_btn", tr("Generate")))
    dock._result_regenerate_btn.setToolTip(
        get_export_copy(
            "dock.build_result.generate_from_tooltip",
            tr("Apply the next change to the picked version, on the same zone"),
        )
    )
    dock._result_regenerate_btn.setCursor(QtC.PointingHandCursor)
    dock._result_regenerate_btn.setStyleSheet(BTN_PRIMARY_WIDE_QSS)
    dock._result_regenerate_btn.clicked.connect(dock._on_retry_clicked)
    result_actions_row.addWidget(dock._result_regenerate_btn, 1)

    dock._result_exit_btn = QPushButton(
        get_export_copy("dock.build_result.new_edit_btn", tr("New edit"))
    )
    dock._result_exit_btn.setToolTip(
        get_export_copy(
            "dock.build_result.new_edit_sessions_tooltip",
            tr("Keep this result on your map and start over on a new zone. The session stays in Sessions."),
        )
    )
    dock._result_exit_btn.setCursor(QtC.PointingHandCursor)
    # A minimum, never a fixed width: the label grows in a longer language.
    dock._result_exit_btn.setMinimumWidth(88)
    dock._result_exit_btn.setMinimumHeight(BTN_PRIMARY_WIDE_PX)
    # The ghost pill at the wide primary's height, so the row reads as one.
    dock._result_exit_btn.setStyleSheet(
        BTN_GHOST_QSS
        + f"QPushButton {{ border-radius: {RADIUS_PILL_WIDE}px;"
        f" min-height: {BTN_PRIMARY_WIDE_PX - 2}px; }}"
    )
    dock._result_exit_btn.clicked.connect(dock._on_exit_clicked)
    result_actions_row.addWidget(dock._result_exit_btn, 0)

    dock._result_prompt_layout.addLayout(result_actions_row)

    # Under the prompt: the versions, then Compare and Vectorize
    # (tool_bar.py). Of 226 people with one generation, 72 vectorized and 32
    # compared, and a pill anchored to the zone is easy to never notice. The
    # row mirrors the hidden toggles, which keep owning the gating.
    dock._result_prompt_layout.addSpacing(4)
    dock._result_prompt_layout.addWidget(dock._version_strip)
    dock._result_prompt_layout.addWidget(build_result_tools_row(dock))

    # A Free account's first result of the month: what Pro adds, and a link
    # (DockProNudgesMixin._maybe_show_after_success). Last, under the actions.
    dock._after_success_label = QLabel()
    dock._after_success_label.setWordWrap(True)
    dock._after_success_label.setTextFormat(QtC.RichText)
    dock._after_success_label.setStyleSheet(HINT_QSS + " padding: 2px 2px 0 2px;")
    dock._after_success_label.setCursor(QtC.ArrowCursor)
    dock._after_success_label.linkActivated.connect(dock._on_after_success_link)
    dock._after_success_label.setVisible(False)
    dock._result_prompt_layout.addWidget(dock._after_success_label)

    dock._result_layout.addWidget(dock._result_prompt_widget)
    dock._result_prompt_widget.setVisible(False)

    dock._result_section.setVisible(False)
    main_layout.addWidget(dock._result_section)


def _build_prewall_banner(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # The low-balance row (quota_card.QuotaCard, AI Agent's pattern): a free
    # account near the end of its month, or a subscriber running low. Pinned
    # above the tool bar so it never competes with the result area.
    # Visibility is state-driven (DockAccountMixin.set_credits /
    # show_prewall_info). Its quiet dismiss hides it for the rest of the QGIS
    # session only (the shared Pro nudge rule), so a later session shows it again.
    dock._prewall_banner = QuotaCard(object_name="aiEditLowCreditCard")
    dock._prewall_banner.primary_clicked.connect(dock._on_prewall_cta_clicked)
    dock._prewall_banner.ghost_clicked.connect(dock._on_prewall_cta_clicked)
    dock._prewall_banner.dismissed.connect(dock._on_prewall_dismissed)
    dock._prewall_url = ""
    main_layout.addWidget(dock._prewall_banner)


def _build_trial_info_box(dock: AIEditDockWidget, main_layout: QVBoxLayout) -> None:
    # The wall: a free account spent its month. The fact, when it comes back,
    # one line of what Pro adds and one wide primary to the plans page
    # (DockAccountMixin.show_trial_exhausted_info fills it).
    dock._trial_info_box = QuotaCard(object_name="aiEditWallCard")
    dock._trial_info_box.primary_clicked.connect(dock._on_trial_info_subscribe_clicked)
    # One filled button per screen: Generate and Launch step down to an
    # outline while the wall's offer shows.
    dock._trial_info_box.shown_changed.connect(lambda _up: dock._update_generate_style())
    dock._trial_info_box.shown_changed.connect(lambda _up: dock._update_launch_style())
    dock._trial_info_url = ""
    main_layout.addWidget(dock._trial_info_box)


def _build_side_panels(dock: AIEditDockWidget, layout: QVBoxLayout) -> None:
    # Mark up panel - full-dock workflow opened via Tools menu, hidden
    # by default; swaps with _main_widget while the user is annotating.
    dock._markup_panel = MarkupPanel(dock)
    dock._markup_panel.setVisible(False)
    dock._markup_panel.tool_changed.connect(dock.markup_tool_changed.emit)
    dock._markup_panel.color_changed.connect(dock.markup_color_changed.emit)
    dock._markup_panel.clear_clicked.connect(dock.markup_clear_clicked.emit)
    dock._markup_panel.undo_clicked.connect(dock.markup_undo_clicked.emit)
    dock._markup_panel.done_clicked.connect(dock.markup_done_clicked.emit)
    layout.addWidget(dock._markup_panel)

    # Reference panel - same swap pattern; the dedicated import/notes home.
    # None without the strip (no reference store): the chip is hidden then too.
    dock._reference_panel = None
    if dock._reference_widget is not None:
        dock._reference_panel = ReferencePanel(
            dock._reference_store, dock._reference_widget, dock
        )
        dock._reference_panel.setVisible(False)
        dock._reference_panel.done_clicked.connect(dock.reference_done_clicked.emit)
        dock._reference_panel.map_capture_requested.connect(
            dock.reference_capture_requested.emit
        )
        layout.addWidget(dock._reference_panel)

    # Vectorize panel - same swap pattern as Mark up.
    dock._vectorize_panel = VectorizePanel(dock)
    dock._vectorize_panel.setVisible(False)
    dock._vectorize_panel.done_clicked.connect(dock.vectorize_done_clicked.emit)
    layout.addWidget(dock._vectorize_panel)


def _build_footer(dock: AIEditDockWidget, layout: QVBoxLayout) -> None:
    # No footer any more: Vectorize and Before / after show on the result
    # screen only (tool_bar.py), the balance lives in Settings, and Tutorial,
    # Settings and Help sit in the header (dock_header.py). What stays is the
    # dock's key shortcuts. ``layout`` is kept for the call order of
    # build_ui; nothing is added to it.
    del layout
    # Keys picked so none collides with a QGIS menu mnemonic or action
    # shortcut (Alt+M is &Mesh, Alt+V is &View in English QGIS on Windows).
    keys = pick_dock_shortcuts(_taken_key_sequences())
    dock._markup_key_seq = QKeySequence(keys["markup"])
    dock._vectorize_key_seq = QKeySequence(keys["vectorize"])
    dock._swipe_key_seq = QKeySequence(keys["swipe"])

    # Mark up is reachable via the pencil chip next to the prompt; the
    # shortcut below still opens it.
    dock._markup_shortcut = _make_dock_shortcut(
        dock, dock._markup_key_seq, dock.markup_clicked.emit
    )

    build_tool_toggles(dock, dock._vectorize_key_seq, dock._swipe_key_seq)
    dock._vectorize_shortcut = _make_dock_shortcut(
        dock,
        dock._vectorize_key_seq,
        lambda btn=dock._vectorize_btn: _click_if_available(btn),
    )
    dock._swipe_shortcut = _make_dock_shortcut(
        dock,
        dock._swipe_key_seq,
        lambda btn=dock._swipe_btn: _click_if_available(btn),
    )


def _taken_key_sequences() -> set[str]:
    """Portable key strings QGIS already uses in its main window: menu-bar
    mnemonics (Alt+letter on Windows and Linux) and every action shortcut."""
    taken: set[str] = set()
    portable = QKeySequence.SequenceFormat.PortableText
    try:
        from qgis.utils import iface

        main_window = iface.mainWindow() if iface is not None else None
        if main_window is None:
            return taken
        for action in main_window.menuBar().actions():
            mnemonic = QKeySequence.mnemonic(action.text()).toString(portable)
            if mnemonic:
                taken.add(mnemonic)
        for action in main_window.findChildren(QAction):
            for seq in action.shortcuts():
                text = seq.toString(portable)
                if text:
                    taken.add(text)
    except Exception as err:  # nosec B110 - fall back to the first candidates
        log_warning(f"Shortcut scan failed: {err}")
    return taken


def _make_dock_shortcut(dock: AIEditDockWidget, seq: QKeySequence, slot) -> QShortcut:
    shortcut = QShortcut(seq, dock)
    shortcut.setContext(QtC.WindowShortcut)
    shortcut.activated.connect(slot)
    # A plugin loaded after us can still claim the same key; Qt then emits
    # only activatedAmbiguously, so run the action from there too.
    shortcut.activatedAmbiguously.connect(slot)
    return shortcut


def _click_if_available(btn) -> None:
    """Shortcut target for a hidden tool toggle: the key works on any screen
    while the tool is available (signed in, feature on) and enabled."""
    if tool_available(btn) and btn.isEnabled():
        btn.click()


def _wrap_in_scroll_area(dock: AIEditDockWidget, main_widget: QWidget) -> None:
    # The dock body: the required-update card (hidden unless an update is
    # required, then it replaces everything) and the scrolling content.
    scroll_area = QScrollArea()
    scroll_area.setWidget(main_widget)
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QtC.FrameNoFrame)
    scroll_area.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
    scroll_area.setStyleSheet(SCROLL_AREA_QSS)
    root = QWidget()
    root_layout = QVBoxLayout(root)
    root_layout.setContentsMargins(0, 0, 0, 0)
    root_layout.setSpacing(0)
    root_layout.addWidget(dock._build_update_gate(), 1)
    root_layout.addWidget(scroll_area, 1)
    dock.setWidget(root)
    # Kept so the state machine scrolls the content, and so the update gate
    # can hide it.
    dock._scroll_area = scroll_area
