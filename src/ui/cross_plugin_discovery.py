"""Cross-plugin discovery: expose sibling TerraLab plugins in the UI to boost
conversion (#47).

If the sibling is installed, show its dock; if not, open the QGIS Plugin
Manager *on that plugin's own card*, which is where QGIS's own Install Plugin
button is. The product page on the website is the last resort only, for a QGIS
built without the plugin manager: a person who clicked through to install wants
the install, not a marketing page (Yvann, 2026-09-09).

``sibling_state`` reads where a sibling stands (running, running with an
update QGIS can install, installed but switched off, switched on but failed to
start, absent) and ``run_sibling_action`` takes the one step that state calls
for: open its panel, update it, switch it on, or install it. Same rules as
AI Agent's ``cross_plugin_discovery`` and ``tools/sibling_setup``.
"""
from __future__ import annotations

import os

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon

from ..core.config_store import get_export_dial
from ..core.logger import log_warning
from ..core.qt_compat import QAction

_AI_SEG_KEYS = ("AI_Segmentation", "QGIS_AI-Segmentation")
_AI_SEG_PRODUCT_URL = (
    "https://terra-lab.ai/ai-segmentation"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai_edit_cross_promo"
)

_UTM = "utm_source=qgis&utm_medium=plugin&utm_campaign=ai_edit_cross_promo"

# The other two TerraLab plugins, which is what the More from TerraLab dialog
# renders. ``name`` has to be the sibling's metadata.txt name= or the Plugin
# Manager filter lands on no row at all; ``keys`` are the folders QGIS may have
# registered it under, the released one first (a development checkout such as
# ``QGIS_AI-Agent-Team`` matches by prefix); ``folder`` is the released folder
# the QGIS repository installs, which is the installer's key for the
# Upgradeable check; ``icon`` is the logo we bundle,
# the same mark the manager shows a click later. ``thumbnail_url`` is the
# written guide's own picture rather than the product page's, so the card and
# the page its guide link opens are visibly the same thing.
SIBLINGS = {
    "ai-agent": {
        "keys": ("AI_Agent", "QGIS_AI-Agent"),
        "folder": "AI_Agent",
        "name": "AI Agent by TerraLab",
        "label": "AI Agent",
        "icon": "ai_agent_icon.png",
        "url": f"https://terra-lab.ai/ai-agent?{_UTM}",
        "tutorial_url": f"https://terra-lab.ai/blog/ai-agent-complete-guide?{_UTM}_guide",
        "thumbnail_url": "https://terra-lab.ai/blog/ai-agent-complete-guide/og.jpg",
    },
    "ai-segmentation": {
        "keys": _AI_SEG_KEYS,
        "folder": "AI_Segmentation",
        "name": "AI Segmentation by TerraLab",
        "label": "AI Segmentation",
        "icon": "ai_segmentation_icon.png",
        "url": _AI_SEG_PRODUCT_URL,
        "tutorial_url": (
            f"https://terra-lab.ai/blog/ai-segmentation-complete-guide?{_UTM}_guide"),
        "thumbnail_url": "https://terra-lab.ai/blog/ai-segmentation-complete-guide/og.jpg",
    },
}


def _find_installed_plugin(keys: tuple[str, ...]):
    """The sibling's plugin object, whichever folder QGIS registered it under.

    The released folder is matched first, then any folder that starts with one
    of the keys: the same plugin checked out as a development tree carries a
    suffix, and a card that says Install for a plugin already on screen is
    worse than no card.
    """
    try:
        import qgis.utils
        for key in keys:
            plugin = qgis.utils.plugins.get(key)
            if plugin is not None:
                return plugin
        for name, plugin in qgis.utils.plugins.items():
            if plugin is not None and name.startswith(keys):
                return plugin
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    return None


# What a sibling panel needs to show its sign-in view rather than a sliver.
DOCK_MIN_HEIGHT = 420


def _give_room(dock) -> None:
    """Grow a panel squeezed to nothing by the docks above it.

    Stacked under another panel, a sibling opened at 75 px showed its title
    bar and nothing else, so the button the user came for was off screen
    (AI Agent, measured 2026-09-17).
    """
    try:
        if dock.height() >= DOCK_MIN_HEIGHT:
            return
        window = dock.parent()
        if not callable(getattr(window, "resizeDocks", None)):
            from qgis.utils import iface
            window = iface.mainWindow() if iface is not None else None
        resize = getattr(window, "resizeDocks", None)
        if not callable(resize):
            return
        from qgis.PyQt.QtCore import Qt
        wanted = max(DOCK_MIN_HEIGHT, dock.sizeHint().height())
        resize([dock], [min(wanted, max(200, window.height() - 200))], Qt.Orientation.Vertical)
    except Exception:  # noqa: BLE001 - a panel that will not grow still opened
        return


def _activate_dock(plugin) -> bool:
    """Ensure a sibling plugin's dock widget is visible.

    AI Edit calls it ``_dock_widget`` and AI Segmentation ``dock_widget``, so
    the attribute is looked for rather than assumed, and a plugin whose dock is
    built lazily is opened through the callable that builds it. ``show()`` on a
    QDockWidget that was closed is what re-opens it, and ``raise_()`` brings it
    to the front of a tab group it shares with another dock.
    """
    for attr in ("dock_widget", "_dock_widget", "dock"):
        dock = getattr(plugin, attr, None)
        if dock is None:
            continue
        try:
            dock.show()
            dock.raise_()
            # A deleted C++ widget answers show() and stays invisible; that is
            # a stale dock from a reload, not an opened one.
            if dock.isVisible():
                _give_room(dock)
                return True
        except Exception:  # noqa: BLE001
            continue  # nosec B112
    for attr in ("toggle_dock_widget", "show_dock_widget", "_toggle_dock", "run"):
        fn = getattr(plugin, attr, None)
        if callable(fn):
            try:
                fn()
                return True
            except Exception:  # noqa: BLE001
                continue  # nosec B112
    return False


def open_plugin_manager(plugin_name: str, fallback_url: str) -> str:
    """Open the QGIS Plugin Manager on ``plugin_name``'s own card.

    Two things have to be true for that card to be reachable, and neither is
    free. The repository list has to have been fetched, or a plugin nobody has
    installed is in no model at all; and the row has to be selected, or the
    right-hand pane shows the "no plugin selected" placeholder over a list of
    sixty. ``showPluginManagerWhenReady`` is the installer's own entry point for
    the first (it is what the Plugins menu calls) and ``_reveal_plugin`` does
    the second.

    The order of the next two lines is the whole fix. Both ``showPluginManager``
    and the fetching dialog before it are ``exec()``, so they block here until
    the user closes the window: a timer started *after* the call fires when the
    dialog is already gone, which is why the filter this replaces never once
    reached a live dialog. Queued first, it runs inside the modal's own event
    loop.

    Returns ``"manager"`` or, when QGIS has no plugin manager to open,
    ``"website"`` after opening the product page.
    """
    try:
        from qgis.PyQt.QtCore import QTimer
        from qgis.utils import iface

        if iface.pluginManagerInterface() is None:
            raise RuntimeError("plugin manager unavailable")
        QTimer.singleShot(0, lambda: _reveal_plugin(plugin_name))
        show_plugin_manager(0)
        return "manager"
    except Exception:  # noqa: BLE001
        QDesktopServices.openUrl(QUrl(fallback_url))
        return "website"


def show_plugin_manager(tab: int = 0) -> None:
    """Show the manager on ``tab``, fetching the repository list first.

    ``pyplugin_installer`` is what the Plugins menu goes through, and its
    ``showPluginManagerWhenReady`` fetches whatever the repositories have not
    served yet before showing the window. Without that step a plugin the user
    has not installed is missing from the model and there is no card to land
    on, and the Upgradeable tab is empty however many updates are waiting. If
    the installer is not importable, the interface still opens the window, only
    without the fetch.

    Tabs: 0 All, 1 Installed, 2 Not installed, 3 Upgradeable. Cross-promotion
    asks for All rather than Not installed, because the same call has to work
    for a person who already has the plugin, and Not installed would then show
    them an empty list.

    This blocks: the window is exec()'d, and so is the fetching dialog in front
    of it when the repositories need checking.
    """
    from qgis.utils import iface

    try:
        import pyplugin_installer

        pyplugin_installer.instance().showPluginManagerWhenReady(int(tab))
        return
    except Exception:  # noqa: BLE001
        pass  # nosec B110 - fall through to the plain window
    iface.pluginManagerInterface().showPluginManager(int(tab))


def _plugin_manager_dialog():
    from qgis.PyQt.QtWidgets import QApplication

    return next(
        (w for w in QApplication.instance().topLevelWidgets()
         if w.metaObject().className() == "QgsPluginManager" and w.isVisible()),
        None,
    )


def _filter_edit(dialog):
    """The manager's search field. Named ``leFilter``, with fallbacks."""
    from qgis.PyQt.QtWidgets import QLineEdit

    for name in ("leFilter", "mLeFilter", "mFilterLineEdit"):
        edit = dialog.findChild(QLineEdit, name)
        if edit is not None:
            return edit
    try:
        from qgis.gui import QgsFilterLineEdit

        edit = next((e for e in dialog.findChildren(QgsFilterLineEdit) if e.isVisible()), None)
        if edit is not None:
            return edit
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    return next((e for e in dialog.findChildren(QLineEdit) if e.isVisible()), None)


def _select_row(dialog, plugin_name: str) -> bool:
    """Select the row named ``plugin_name`` in the manager's plugin list.

    The list is ``vwPlugins``, a QListView over a sort/filter proxy, so the row
    number is whatever the filter left; the name is matched instead. Selecting
    it is what paints the card on the right, which is the whole point of
    sending someone here.
    """
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QListView

    view = dialog.findChild(QListView, "vwPlugins")
    if view is None:
        return False
    model = view.model()
    if model is None:
        return False
    wanted = plugin_name.strip().casefold()
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        label = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "").strip().casefold()
        if label != wanted:
            continue
        if view.currentIndex() == index and view.selectionModel().isSelected(index):
            return True
        view.setCurrentIndex(index)
        view.scrollTo(index)
        return True
    return False


# The window is not there the instant the click returns: the installer may exec
# a fetching dialog over the network first, and the manager rebuilds its model
# across several event-loop turns, each rebuild clearing the filter. So this
# polls rather than setting the text once, and the budget is a fetch's worth of
# seconds rather than a repaint's.
_REVEAL_ATTEMPTS = 90
_REVEAL_MS = 140
# Consecutive successful row-selections required before polling stops.
_REVEAL_CONFIRM_STREAK = 2


def reveal_in_open_manager(plugin_name: str, tab: int = -1, filter_text: bool = False,
                           attempts: int | None = None, confirmed: int = 0) -> None:
    """Select ``plugin_name``'s row in the Plugin Manager, once it is up.

    Queue this *before* the call that shows the manager: that call is an
    ``exec()`` and does not return until the window closes, so a timer started
    after it fires into an empty screen.

    ``tab`` is the rail row to land on, or -1 to leave whichever the caller
    asked the manager for. ``filter_text`` types the name into the search field,
    which is right when the point is to install that one plugin and wrong for
    the Upgradeable tab: a filter there empties the list whenever the plugin is
    not in fact upgradeable, and an empty list is worse than the real one.
    """
    from qgis.PyQt.QtCore import QTimer
    from qgis.PyQt.QtWidgets import QListWidget

    if attempts is None:
        attempts = get_export_dial("widgets.cross_plugin_discovery.reveal_attempts_count", _REVEAL_ATTEMPTS)
    try:
        dialog = _plugin_manager_dialog()
        if dialog is not None:
            # Once, not on every poll: re-selecting would drag a user who has
            # moved to another tab back out of it.
            if confirmed == 0 and tab >= 0:
                tabs = dialog.findChild(QListWidget, "mOptionsListWidget")
                if tabs is not None and tabs.currentRow() != tab:
                    tabs.setCurrentRow(tab)
            if confirmed == 0 and not filter_text:
                # QGIS reuses the manager window, and a search typed by an
                # earlier Install click would still hide the Upgradeable rows.
                stale = _filter_edit(dialog)
                if stale is not None and stale.text():
                    stale.clear()
            edit = _filter_edit(dialog) if filter_text else None
            if edit is not None and edit.text() != plugin_name:
                edit.setText(plugin_name)
                confirmed = 0
            elif _select_row(dialog, plugin_name):
                confirmed += 1
            else:
                confirmed = 0
    except Exception:  # noqa: BLE001
        pass  # nosec B110 - landing on the card is a nicety, the window is open
    confirm_streak = get_export_dial(
        "widgets.cross_plugin_discovery.reveal_confirm_streak_count", _REVEAL_CONFIRM_STREAK
    )
    if confirmed < confirm_streak and attempts > 0:
        reveal_ms = get_export_dial("widgets.cross_plugin_discovery.reveal_interval_ms", _REVEAL_MS)
        QTimer.singleShot(reveal_ms, lambda: reveal_in_open_manager(
            plugin_name, tab, filter_text, attempts - 1, confirmed))


def _reveal_plugin(plugin_name: str) -> None:
    """Filter the manager to ``plugin_name`` on the All tab and select its row."""
    reveal_in_open_manager(plugin_name, tab=0, filter_text=True)


def open_ai_segmentation() -> bool:
    """Open the AI Segmentation dock if installed, else the Plugin Manager
    pre-filtered to it (product page as last resort). Returns True when the
    sibling plugin was already installed and its dock came up."""
    from ..core.auth.activation_manager import get_cross_promo_url

    plugin = _find_installed_plugin(_AI_SEG_KEYS)
    if plugin is not None and _activate_dock(plugin):
        return True
    open_plugin_manager(
        "AI Segmentation by TerraLab", get_cross_promo_url(_AI_SEG_PRODUCT_URL)
    )
    return False


def make_ai_seg_action(parent, iface, label: str, tooltip: str,
                       icon: QIcon | None = None) -> QAction:
    """Create a QAction that opens AI Segmentation if installed, else the Plugin Manager."""
    action = QAction(icon or QIcon(), label, parent)
    action.setToolTip(tooltip)
    action.triggered.connect(open_ai_segmentation)
    return action


def make_sibling_action(parent, iface, product_id: str, label: str, tooltip: str,
                        icon: QIcon | None = None) -> QAction:
    """A QAction that takes the sibling's one step (open, update, switch on,
    install), the same rule as its More plugins card."""
    del iface  # kept for signature parity with the sibling plugins
    action = QAction(icon or QIcon(), label, parent)
    action.setToolTip(tooltip)
    action.triggered.connect(lambda _checked=False: run_sibling_action(product_id))
    return action


def make_ai_agent_action(parent, iface, label: str, tooltip: str,
                         icon: QIcon | None = None) -> QAction:
    return make_sibling_action(parent, iface, "ai-agent", label, tooltip, icon)


def is_sibling_installed(product_id: str) -> bool:
    """Whether QGIS has this sibling loaded, which decides what its button says."""
    sibling = SIBLINGS.get(product_id)
    return bool(sibling and _find_installed_plugin(sibling["keys"]) is not None)


def open_sibling(product_id: str) -> str:
    """Open the sibling if it is installed, else the Plugin Manager on its card.

    Returns what happened, so a caller with a button can say so: ``"opened"``,
    ``"manager"`` or ``"website"``.
    """
    sibling = SIBLINGS.get(product_id)
    if not sibling:
        return ""
    plugin = _find_installed_plugin(sibling["keys"])
    if plugin is not None and _activate_dock(plugin):
        return "opened"
    return open_plugin_manager(sibling["name"], sibling["url"])


def open_sibling_page(product_id: str) -> None:
    """The sibling's product page in the browser, always the website."""
    sibling = SIBLINGS.get(product_id)
    if sibling:
        QDesktopServices.openUrl(QUrl(sibling["url"]))


def open_sibling_tutorial(product_id: str) -> None:
    """The sibling's written guide on the blog."""
    sibling = SIBLINGS.get(product_id)
    if sibling:
        QDesktopServices.openUrl(QUrl(sibling["tutorial_url"]))


# --- where a sibling stands, and the one step that moves it on ---------------

# The states ``sibling_state`` answers, in the order a card checks them.
STATE_OPEN = "open"            # running: the button shows its panel
STATE_UPDATE = "update"        # running, and QGIS lists a newer version
STATE_ENABLE = "enable"        # installed, switched off in the Plugin Manager
STATE_RESTART = "restart"      # switched on, but it did not start this session
STATE_INSTALL = "install"      # not on this machine


def _installed_key(keys: tuple[str, ...]) -> str:
    """The key QGIS registered a running sibling under, or ""."""
    try:
        import qgis.utils
        for key in keys:
            if qgis.utils.plugins.get(key) is not None:
                return key
        for name, plugin in qgis.utils.plugins.items():
            if plugin is not None and name.startswith(keys):
                return name
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    return ""


def _plugin_dirs() -> list:
    try:
        import qgis.utils
        dirs = [p for p in (getattr(qgis.utils, "plugin_paths", None) or []) if isinstance(p, str)]
    except Exception:  # noqa: BLE001
        dirs = []
    try:
        from qgis.core import QgsApplication
        dirs.append(os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins"))
    except Exception:  # noqa: BLE001
        pass  # nosec B110 - no QGIS application (a unit test)
    return dirs


def _enabled_in_plugin_manager(folder: str) -> bool:
    """The Plugin Manager's own tick for this folder (``PythonPlugins/<folder>``)."""
    try:
        from qgis.core import QgsSettings
        return bool(QgsSettings().value("PythonPlugins/" + folder, False, type=bool))
    except Exception:  # noqa: BLE001 - no settings means no tick
        return False


def sibling_presence(product_id: str) -> dict:
    """``{"state", "folder", "plugin"}`` for a sibling.

    ``state`` is ``loaded`` (``plugin`` is the live object), ``disabled`` (the
    folder is installed and switched off), ``not_started`` (switched on, no
    plugin object: it failed at startup) or ``absent``. A plugin switched off
    is still on the disk and importable, which is why this reads QGIS's own
    lists rather than trying an import.
    """
    sibling = SIBLINGS.get(product_id) or {}
    keys = tuple(sibling.get("keys") or ())
    if not keys:
        return {"state": "absent", "folder": "", "plugin": None}
    key = _installed_key(keys)
    if key:
        import qgis.utils
        return {"state": "loaded", "folder": key, "plugin": qgis.utils.plugins.get(key)}
    try:
        import qgis.utils
        available = set(getattr(qgis.utils, "available_plugins", None) or [])
        active = set(getattr(qgis.utils, "active_plugins", None) or [])
    except Exception:  # noqa: BLE001
        available, active = set(), set()
    folder = next((k for k in keys if k in available), "")
    if not folder:
        folder = next((k for base in _plugin_dirs() for k in keys
                       if os.path.isfile(os.path.join(base, k, "metadata.txt"))), "")
    if not folder:
        return {"state": "absent", "folder": "", "plugin": None}
    enabled = folder in active or _enabled_in_plugin_manager(folder)
    return {"state": "not_started" if enabled else "disabled", "folder": folder, "plugin": None}


def installer_upgradeable_version(folder: str) -> str:
    """The version QGIS's installer lists as installable for ``folder``, or "".

    The installer's own verdict (``status == "upgradeable"``) and nothing else:
    an Update button that opens an empty Upgradeable tab is a dead end. A
    development checkout is filed under its own folder name and is never
    listed, so it never shows an update.
    """
    if not folder:
        return ""
    try:
        from pyplugin_installer.installer_data import plugins

        data = plugins.all().get(folder)
        if data and data.get("status") == "upgradeable":
            return str(data.get("version_available") or "")
    except Exception:  # noqa: BLE001
        pass  # nosec B110 - no repository metadata yet
    return ""


def sibling_state(product_id: str) -> str:
    """One of the ``STATE_*`` values: what the sibling's button should do now."""
    found = sibling_presence(product_id)
    state = found["state"]
    if state == "loaded":
        if installer_upgradeable_version(found["folder"]):
            return STATE_UPDATE
        return STATE_OPEN
    if state == "disabled":
        return STATE_ENABLE
    if state == "not_started":
        return STATE_RESTART
    return STATE_INSTALL


def enable_sibling(product_id: str) -> tuple[bool, str]:
    """Switch a sibling on, the way the Plugin Manager's tick does.

    Load it, start it, and write the same ``PythonPlugins/<folder>`` setting
    the manager writes, so it survives the next QGIS start. Returns whether it
    runs afterwards and, when it does not, a short reason for the log.
    """
    found = sibling_presence(product_id)
    if found["state"] == "loaded":
        return True, ""
    folder = found["folder"]
    if found["state"] != "disabled" or not folder:
        return False, found["state"]
    try:
        import qgis.utils
        if not qgis.utils.loadPlugin(folder):
            return False, "load_failed"
        if not qgis.utils.startPlugin(folder):
            return False, "start_failed"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__
    try:
        from qgis.core import QgsSettings
        QgsSettings().setValue("PythonPlugins/" + folder, True)
    except Exception:  # noqa: BLE001
        pass  # nosec B110 - it runs now; it may just not come back next start
    return True, ""


def update_sibling(product_id: str) -> str:
    """The Plugin Manager on its Upgradeable tab, on the sibling's own row."""
    sibling = SIBLINGS.get(product_id)
    if not sibling:
        return ""
    from .terralab_menu import open_plugin_manager_updates

    return open_plugin_manager_updates(sibling["url"], plugin_name=sibling["name"])


def run_sibling_action(product_id: str) -> str:
    """Take the one step ``sibling_state`` names, and say what happened.

    Returns ``"opened"``, ``"enabled"``, ``"enable_failed"``,
    ``"plugin_manager"`` / ``"marketplace_page"`` (update), ``"manager"`` /
    ``"website"`` (install), or ``"restart"`` when only a QGIS restart helps.
    A sibling that is switched on here opens its panel at once, which shows
    its own sign-in view when it holds no account yet.
    """
    state = sibling_state(product_id)
    if state == STATE_UPDATE:
        return update_sibling(product_id)
    if state == STATE_ENABLE:
        ok, reason = enable_sibling(product_id)
        if not ok:
            log_warning(f"Could not switch on {product_id}: {reason}")
            return "enable_failed"
        found = sibling_presence(product_id)
        if found["plugin"] is not None:
            _activate_dock(found["plugin"])
        return "enabled"
    if state == STATE_RESTART:
        return "restart"
    return open_sibling(product_id)
