






from __future__ import annotations

import os

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QMenu

from ..core.config_store import get_export_copy
from ..core.i18n import tr

TERRALAB_URL = "https://terra-lab.ai?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=menu_more"


_THIS_PLUGIN_NAME = "AI Edit by TerraLab"
_UTILITY_SEPARATOR = "_terralab_utility_sep"
_PLUGINS_MENU_NAME = "TerraLab"




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












    try:
        from qgis.PyQt.QtCore import QTimer
        from qgis.utils import iface

        from .cross_plugin_discovery import reveal_in_open_manager, show_plugin_manager
        manager = iface.pluginManagerInterface()
        if manager is None:
            raise RuntimeError("plugin manager unavailable")






        QTimer.singleShot(0, lambda: reveal_in_open_manager(plugin_name, tab=3))
        show_plugin_manager(3)
        return "plugin_manager"
    except Exception:  # nosec B110
        pass
    try:
        from qgis.utils import iface
        iface.actionManagePlugins().trigger()
        return "plugin_manager"
    except Exception:  # nosec B110
        pass
    QDesktopServices.openUrl(QUrl(fallback_url))
    return "marketplace_page"


def _open_plugin_manager_updates():

    open_plugin_manager_updates()


def _open_terralab_plugins(_checked=False):








    try:
        from qgis.utils import iface

        from .siblings_dialog import show_siblings_dialog

        show_siblings_dialog(iface.mainWindow() if iface is not None else None)
        return
    except Exception:  # noqa: BLE001
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






    return str(action.property("terralab_product_id") or action.text())


def add_plugin_to_menu(menu: QMenu, action, product_id: str, is_cross_promo: bool = False):
    action.setProperty("terralab_product_id", product_id)
    action.setProperty("terralab_is_cross_promo", is_cross_promo)



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



            if not _has_plugin_entries(submenu):
                plugin_menu.removeAction(submenu.menuAction())
                submenu.deleteLater()
            break
