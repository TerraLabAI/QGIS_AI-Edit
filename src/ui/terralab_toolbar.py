"""Cooperative TerraLab toolbar management for QGIS plugins.

SHARED: keep in sync with the copy in the sibling TerraLab plugin.
"""
from __future__ import annotations

from qgis.PyQt.QtWidgets import QToolBar

_TOOLBAR_OBJECT_NAME = "TerraLabToolbar"
_TOOLBAR_TITLE = "TerraLab Toolbar"


def get_or_create_terralab_toolbar(iface):
    """Find existing TerraLab toolbar or create one via iface.addToolBar()."""
    main_window = iface.mainWindow()
    for tb in main_window.findChildren(QToolBar):
        if tb.objectName() == _TOOLBAR_OBJECT_NAME:
            return tb
    toolbar = QToolBar(_TOOLBAR_TITLE)
    toolbar.setObjectName(_TOOLBAR_OBJECT_NAME)
    iface.addToolBar(toolbar)
    return toolbar


def add_action_to_toolbar(toolbar, action, product_id, is_cross_promo=False):
    """Add a plugin action alphabetically to the shared toolbar.

    Cross-promo actions never replace a real plugin's action.
    Real plugin actions replace cross-promo placeholders.
    """
    action.setProperty("terralab_product_id", product_id)
    action.setProperty("terralab_is_cross_promo", is_cross_promo)
    for existing in toolbar.actions():
        if existing.property("terralab_product_id") == product_id and existing is not action:
            if is_cross_promo and not existing.property("terralab_is_cross_promo"):
                return
            toolbar.removeAction(existing)
            break
    for existing in toolbar.actions():
        if existing.text() > action.text():
            toolbar.insertAction(existing, action)
            return
    toolbar.addAction(action)


def is_terralab_toolbar_alive(toolbar) -> bool:
    """False once Qt has deleted the C++ half under the Python wrapper.

    Both TerraLab plugins share one toolbar and the last one out deletes it,
    so a wrapper that was valid at startup is routinely dead by unload.
    """
    if toolbar is None:
        return False
    try:
        toolbar.objectName()
    except RuntimeError:
        return False
    return True


def remove_action_from_toolbar(toolbar, action, main_window) -> bool:
    """Remove the action, and delete the toolbar once nothing is left on it.

    Returns True while the toolbar is still usable, so a caller removing two
    actions can stop after the first one emptied it. Removing an action from a
    toolbar whose C++ half is gone raised "wrapped C/C++ object of type QToolBar
    has been deleted" for 41 users a month, twice per unload.
    """
    if not is_terralab_toolbar_alive(toolbar):
        return False
    try:
        toolbar.removeAction(action)
        remaining = [a for a in toolbar.actions() if not a.isSeparator()]
        if remaining:
            return True
        main_window.removeToolBar(toolbar)
        toolbar.deleteLater()
    except RuntimeError:
        pass
    return False
