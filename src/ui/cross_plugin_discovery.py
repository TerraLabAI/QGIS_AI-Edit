














from __future__ import annotations

import os

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon

from ..core.config_store import get_export_dial
from ..core.logger import log_warning
from ..core.qt_compat import QAction

_AI_SEG_KEYS = ("AI_Segmentation", "QGIS_AI-Segmentation")

_SITE = "https://terra-lab.ai"










SIBLINGS = {
    "ai-agent": {
        "keys": ("AI_Agent", "QGIS_AI-Agent"),
        "folder": "AI_Agent",
        "name": "AI Agent by TerraLab",
        "label": "AI Agent",
        "icon": "ai_agent_icon.png",
    },
    "ai-segmentation": {
        "keys": _AI_SEG_KEYS,
        "folder": "AI_Segmentation",
        "name": "AI Segmentation by TerraLab",
        "label": "AI Segmentation",
        "icon": "ai_segmentation_icon.png",
    },
}

_SIBLING_LINK_FIELDS = ("url", "tutorial_url", "thumbnail_url")


def sibling_link(product_id: str, field: str) -> str:







    if field not in _SIBLING_LINK_FIELDS or product_id not in SIBLINGS:
        return ""
    fallback = {
        "url": f"{_SITE}/{product_id}",
        "tutorial_url": f"{_SITE}/blog/{product_id}-complete-guide",
        "thumbnail_url": f"{_SITE}/blog/{product_id}-complete-guide/og.jpg",
    }[field]
    from ..core.auth.activation_manager import get_server_config, is_safe_link

    try:
        links = get_server_config().get("sibling_links")
        value = links.get(product_id, {}).get(field) if isinstance(links, dict) else None
    except Exception:  # noqa: BLE001
        value = None
    return value if is_safe_link(value) else fallback


def _find_installed_plugin(keys: tuple[str, ...]):







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



DOCK_MIN_HEIGHT = 420


def _give_room(dock) -> None:






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
    except Exception:  # noqa: BLE001
        return


def _activate_dock(plugin) -> bool:








    for attr in ("dock_widget", "_dock_widget", "dock"):
        dock = getattr(plugin, attr, None)
        if dock is None:
            continue
        try:
            dock.show()
            dock.raise_()


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


















    from qgis.utils import iface

    try:
        import pyplugin_installer

        pyplugin_installer.instance().showPluginManagerWhenReady(int(tab))
        return
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    iface.pluginManagerInterface().showPluginManager(int(tab))


def _plugin_manager_dialog():
    from qgis.PyQt.QtWidgets import QApplication

    return next(
        (w for w in QApplication.instance().topLevelWidgets()
         if w.metaObject().className() == "QgsPluginManager" and w.isVisible()),
        None,
    )


def _filter_edit(dialog):

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







_REVEAL_ATTEMPTS = 90
_REVEAL_MS = 140

_REVEAL_CONFIRM_STREAK = 2


def reveal_in_open_manager(plugin_name: str, tab: int = -1, filter_text: bool = False,
                           attempts: int | None = None, confirmed: int = 0) -> None:












    from qgis.PyQt.QtCore import QTimer
    from qgis.PyQt.QtWidgets import QListWidget

    if attempts is None:
        attempts = get_export_dial("widgets.cross_plugin_discovery.reveal_attempts_count", _REVEAL_ATTEMPTS)
    try:
        dialog = _plugin_manager_dialog()
        if dialog is not None:


            if confirmed == 0 and tab >= 0:
                tabs = dialog.findChild(QListWidget, "mOptionsListWidget")
                if tabs is not None and tabs.currentRow() != tab:
                    tabs.setCurrentRow(tab)
            if confirmed == 0 and not filter_text:


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
        pass  # nosec B110
    confirm_streak = get_export_dial(
        "widgets.cross_plugin_discovery.reveal_confirm_streak_count", _REVEAL_CONFIRM_STREAK
    )
    if confirmed < confirm_streak and attempts > 0:
        reveal_ms = get_export_dial("widgets.cross_plugin_discovery.reveal_interval_ms", _REVEAL_MS)
        QTimer.singleShot(reveal_ms, lambda: reveal_in_open_manager(
            plugin_name, tab, filter_text, attempts - 1, confirmed))


def _reveal_plugin(plugin_name: str) -> None:

    reveal_in_open_manager(plugin_name, tab=0, filter_text=True)


def open_ai_segmentation() -> bool:



    from ..core.auth.activation_manager import get_cross_promo_url

    plugin = _find_installed_plugin(_AI_SEG_KEYS)
    if plugin is not None and _activate_dock(plugin):
        return True
    open_plugin_manager(
        "AI Segmentation by TerraLab", get_cross_promo_url(sibling_link("ai-segmentation", "url"))
    )
    return False


def make_ai_seg_action(parent, iface, label: str, tooltip: str,
                       icon: QIcon | None = None) -> QAction:

    action = QAction(icon or QIcon(), label, parent)
    action.setToolTip(tooltip)
    action.triggered.connect(open_ai_segmentation)
    return action


def make_sibling_action(parent, iface, product_id: str, label: str, tooltip: str,
                        icon: QIcon | None = None) -> QAction:


    del iface
    action = QAction(icon or QIcon(), label, parent)
    action.setToolTip(tooltip)
    action.triggered.connect(lambda _checked=False: run_sibling_action(product_id))
    return action


def make_ai_agent_action(parent, iface, label: str, tooltip: str,
                         icon: QIcon | None = None) -> QAction:
    return make_sibling_action(parent, iface, "ai-agent", label, tooltip, icon)


def is_sibling_installed(product_id: str) -> bool:

    sibling = SIBLINGS.get(product_id)
    return bool(sibling and _find_installed_plugin(sibling["keys"]) is not None)


def open_sibling(product_id: str) -> str:





    sibling = SIBLINGS.get(product_id)
    if not sibling:
        return ""
    plugin = _find_installed_plugin(sibling["keys"])
    if plugin is not None and _activate_dock(plugin):
        return "opened"
    return open_plugin_manager(sibling["name"], sibling_link(product_id, "url"))


def open_sibling_page(product_id: str) -> None:

    sibling = SIBLINGS.get(product_id)
    if sibling:
        QDesktopServices.openUrl(QUrl(sibling_link(product_id, "url")))


def open_sibling_tutorial(product_id: str) -> None:

    sibling = SIBLINGS.get(product_id)
    if sibling:
        QDesktopServices.openUrl(QUrl(sibling_link(product_id, "tutorial_url")))





STATE_OPEN = "open"
STATE_UPDATE = "update"
STATE_ENABLE = "enable"
STATE_RESTART = "restart"
STATE_INSTALL = "install"


def _installed_key(keys: tuple[str, ...]) -> str:

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
        pass  # nosec B110
    return dirs


def _enabled_in_plugin_manager(folder: str) -> bool:

    try:
        from qgis.core import QgsSettings
        return bool(QgsSettings().value("PythonPlugins/" + folder, False, type=bool))
    except Exception:  # noqa: BLE001
        return False


def sibling_presence(product_id: str) -> dict:








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







    if not folder:
        return ""
    try:
        from pyplugin_installer.installer_data import plugins

        data = plugins.all().get(folder)
        if data and data.get("status") == "upgradeable":
            return str(data.get("version_available") or "")
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    return ""


def sibling_state(product_id: str) -> str:

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
        pass  # nosec B110
    return True, ""


def update_sibling(product_id: str) -> str:

    sibling = SIBLINGS.get(product_id)
    if not sibling:
        return ""
    from .terralab_menu import open_plugin_manager_updates

    return open_plugin_manager_updates(sibling_link(product_id, "url"), plugin_name=sibling["name"])


def run_sibling_action(product_id: str) -> str:








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
