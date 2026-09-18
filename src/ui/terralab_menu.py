"""Cooperative TerraLab menu management for QGIS plugins.

SHARED: keep in sync with the copy in the sibling TerraLab plugin. TERRALAB_URL
stays spelled out here for that reason; the dock's own link is DOCK_BRANDING_URL
in ui/dock/style.py.
"""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QMenu

from ..core.config_store import get_export_copy
from ..core.i18n import tr

TERRALAB_URL = "https://terra-lab.ai?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=menu_more"
# This plugin's own metadata.txt name=, so the Upgradeable tab can land on its
# row rather than on a list the reader has to search.
_THIS_PLUGIN_NAME = "AI Edit by TerraLab"
_UTILITY_SEPARATOR = "_terralab_utility_sep"
_PLUGINS_MENU_NAME = "TerraLab"
# Stable identity for the shared menus, robust to translation or to a
# third-party QMenu("TerraLab") colliding on display text (the toolbar already
# keys on an objectName; the menus now match). The text match stays as a
# fallback so a sibling plugin from an older release is still found.
_MENU_OBJECT_NAME = "TerraLabMenu"
_SUBMENU_OBJECT_NAME = "TerraLabPluginsSubmenu"


def _find_terralab_logo():
    ui_dir = os.path.dirname(__file__)
    plugin_dir = os.path.dirname(os.path.dirname(ui_dir))
    logo_path = os.path.join(plugin_dir, "resources", "icons", "terralab-logo.png")
    if os.path.isfile(logo_path):
        return logo_path
    return None


def open_plugin_manager_updates(fallback_url: str = TERRALAB_URL,
                                plugin_name: str = _THIS_PLUGIN_NAME) -> str:
    """Open the Plugin Manager on its Upgradeable tab.

    It lands on ``plugin_name``'s row (this plugin's by default, a sibling's
    from the More plugins page), and the list behind it is fetched first,
    so the tab is populated rather than empty on a cold repository cache.

    Never a silent no-op: the manager interface can be absent or refuse to
    open, and the menu entry then swallowed the click with nothing on screen.
    The generic Manage Plugins action is tried next, and a web page last, so
    the user always lands somewhere they can act. Returns which way in was
    taken, so a caller can report it.
    """
    try:
        from qgis.PyQt.QtCore import QTimer
        from qgis.utils import iface

        from .cross_plugin_discovery import reveal_in_open_manager, show_plugin_manager
        manager = iface.pluginManagerInterface()
        if manager is None:
            raise RuntimeError("plugin manager unavailable")
        # Queued before the call that shows the window: showing it is an
        # exec() that does not return until the user closes it, so a timer
        # started after it would fire into an empty screen. And the manager is
        # opened through the installer, which fetches the repository list
        # first: without that the Upgradeable tab is empty however many
        # updates are waiting (Yvann, 2026-09-09).
        QTimer.singleShot(0, lambda: reveal_in_open_manager(plugin_name, tab=3))
        show_plugin_manager(3)
        return "plugin_manager"
    except Exception:  # nosec B110 - fall through to the next way in
        pass
    try:
        from qgis.utils import iface
        iface.actionManagePlugins().trigger()
        return "plugin_manager"
    except Exception:  # nosec B110 - the web page below is the last resort
        pass
    QDesktopServices.openUrl(QUrl(fallback_url))
    return "marketplace_page"


def _open_plugin_manager_updates():
    """Menu slot. QAction.triggered passes a checked flag, so it stays argless."""
    open_plugin_manager_updates()


def _open_terralab_plugins(_checked=False):
    """The other TerraLab plugins, as cards, without leaving QGIS.

    Its own entry, next to More from TerraLab rather than instead of it: that
    one is the website and stays the website (Yvann, 2026-09-09). This one is
    the way in, where Install goes to the QGIS Plugin Manager on that plugin's
    own row. The site is still the fallback for a QGIS that cannot build the
    window at all, and a link inside it.
    """
    try:
        from qgis.utils import iface

        from .siblings_dialog import show_siblings_dialog

        show_siblings_dialog(iface.mainWindow() if iface is not None else None)
        return
    except Exception:  # noqa: BLE001 - the website below is the last resort
        pass  # nosec B110
    QDesktopServices.openUrl(QUrl(TERRALAB_URL))


def get_or_create_terralab_menu(main_window) -> QMenu:
    menu_bar = main_window.menuBar()
    for action in menu_bar.actions():
        menu = action.menu()
        if menu and (menu.objectName() == _MENU_OBJECT_NAME or action.text() == "TerraLab"):
            return menu
    menu = QMenu("TerraLab", main_window)
    menu.setObjectName(_MENU_OBJECT_NAME)
    menu_bar.addMenu(menu)
    sep = menu.addSeparator()
    sep.setObjectName(_UTILITY_SEPARATOR)
    update_icon = QIcon(":/images/themes/default/mActionRefresh.svg")
    check_update = menu.addAction(
        update_icon, get_export_copy("widgets.terralab_menu.check_for_updates", tr("Check for Updates"))
    )
    check_update.triggered.connect(_open_plugin_manager_updates)
    logo_path = _find_terralab_logo()
    website_icon = QIcon(logo_path) if logo_path else QIcon()
    plugins_icon = QIcon(":/images/themes/default/mActionShowPluginManager.svg")
    other_action = menu.addAction(
        plugins_icon,
        get_export_copy("widgets.terralab_menu.other_terralab_plugins", tr("Other TerraLab plugins...")),
    )
    other_action.triggered.connect(_open_terralab_plugins)
    more_action = menu.addAction(
        website_icon, get_export_copy("widgets.terralab_menu.more_from_terralab", tr("More from TerraLab..."))
    )
    more_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(TERRALAB_URL)))
    return menu


def _sort_key(action) -> str:
    """Order menu entries by product id, never by their displayed label.

    The label is translated, so the same two plugins landed in a different
    order in every language, and the insertion point moved with the UI
    language rather than staying put.
    """
    return str(action.property("terralab_product_id") or action.text())


def add_plugin_to_menu(menu: QMenu, action, product_id: str, is_cross_promo: bool = False):
    action.setProperty("terralab_product_id", product_id)
    action.setProperty("terralab_is_cross_promo", is_cross_promo)
    # A real plugin replaces a cross-promo placeholder, but a placeholder must
    # never replace a real plugin's action (mirrors add_action_to_toolbar), or
    # load order could let our cross-promo entry clobber the sibling's real one.
    for a in menu.actions():
        if a.objectName() == _UTILITY_SEPARATOR:
            break
        if a.property("terralab_product_id") == product_id and a is not action:
            if is_cross_promo and not a.property("terralab_is_cross_promo"):
                return
            menu.removeAction(a)
            break
    sep_action = None
    plugin_actions = []
    for a in menu.actions():
        if a.objectName() == _UTILITY_SEPARATOR:
            sep_action = a
            break
        if not a.isSeparator():
            plugin_actions.append(a)
    insert_before = sep_action
    action_key = _sort_key(action)
    for existing in plugin_actions:
        if _sort_key(existing) > action_key:
            insert_before = existing
            break
    if insert_before:
        menu.insertAction(insert_before, action)
    else:
        menu.addAction(action)


def _has_plugin_entries(menu: QMenu) -> bool:
    """Whether a plugin row is left above the utility separator."""
    for a in menu.actions():
        if a.objectName() == _UTILITY_SEPARATOR:
            return False
        if not a.isSeparator():
            return True
    return False


def remove_plugin_from_menu(menu: QMenu, action, main_window):
    menu.removeAction(action)
    if not _has_plugin_entries(menu):
        main_window.menuBar().removeAction(menu.menuAction())
        # Removing the action only unhooks the menu from the bar. Without this
        # the QMenu stays alive and a re-enable builds a second one.
        menu.deleteLater()


def _get_or_create_plugins_submenu(iface) -> QMenu:
    plugin_menu = iface.pluginMenu()
    for a in plugin_menu.actions():
        sub = a.menu()
        if sub and (sub.objectName() == _SUBMENU_OBJECT_NAME
                    or a.text() == _PLUGINS_MENU_NAME):
            return sub
    logo_path = _find_terralab_logo()
    logo_icon = QIcon(logo_path) if logo_path else QIcon()
    submenu = plugin_menu.addMenu(logo_icon, _PLUGINS_MENU_NAME)
    submenu.setObjectName(_SUBMENU_OBJECT_NAME)
    sep = submenu.addSeparator()
    sep.setObjectName(_UTILITY_SEPARATOR)
    update_icon = QIcon(":/images/themes/default/mActionRefresh.svg")
    check_update = submenu.addAction(
        update_icon, get_export_copy("widgets.terralab_menu.check_for_updates", tr("Check for Updates"))
    )
    check_update.triggered.connect(_open_plugin_manager_updates)
    website_icon = QIcon(logo_path) if logo_path else QIcon()
    plugins_icon = QIcon(":/images/themes/default/mActionShowPluginManager.svg")
    other_action = submenu.addAction(
        plugins_icon,
        get_export_copy("widgets.terralab_menu.other_terralab_plugins", tr("Other TerraLab plugins...")),
    )
    other_action.triggered.connect(_open_terralab_plugins)
    more_action = submenu.addAction(
        website_icon, get_export_copy("widgets.terralab_menu.more_from_terralab", tr("More from TerraLab..."))
    )
    more_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(TERRALAB_URL)))
    return submenu


def add_to_plugins_menu(iface, action):
    submenu = _get_or_create_plugins_submenu(iface)
    product_id = action.property("terralab_product_id")
    if product_id:
        # Same rule as the menu bar entry: a cross-promo placeholder never
        # replaces the sibling's real action, whichever plugin loaded first.
        is_cross_promo = bool(action.property("terralab_is_cross_promo"))
        for a in submenu.actions():
            if a.objectName() == _UTILITY_SEPARATOR:
                break
            if a.property("terralab_product_id") == product_id and a is not action:
                if is_cross_promo and not a.property("terralab_is_cross_promo"):
                    return
                submenu.removeAction(a)
                break
    sep_action = None
    plugin_actions = []
    for a in submenu.actions():
        if a.objectName() == _UTILITY_SEPARATOR:
            sep_action = a
            break
        if not a.isSeparator():
            plugin_actions.append(a)
    insert_before = sep_action
    action_key = _sort_key(action)
    for existing in plugin_actions:
        if _sort_key(existing) > action_key:
            insert_before = existing
            break
    if insert_before:
        submenu.insertAction(insert_before, action)
    else:
        submenu.addAction(action)


def remove_from_plugins_menu(iface, action):
    plugin_menu = iface.pluginMenu()
    for a in plugin_menu.actions():
        submenu = a.menu()
        if submenu and (submenu.objectName() == _SUBMENU_OBJECT_NAME
                        or a.text() == _PLUGINS_MENU_NAME):
            submenu.removeAction(action)
            # The utility rows (Check for Updates, Other TerraLab plugins,
            # More from TerraLab) always stay, so "no actions left" never
            # happened and the submenu outlived the last TerraLab plugin.
            if not _has_plugin_entries(submenu):
                plugin_menu.removeAction(submenu.menuAction())
                submenu.deleteLater()
            break
