"""Reveal QGIS's own Layers panel.

The dock's "Saved as <layer>" link selects the new layer in the Layers panel.
With that panel closed the selection had nothing to show, so the link read as
dead; this opens the panel that holds the layer tree view first.
"""
from __future__ import annotations

from qgis.PyQt.QtWidgets import QDockWidget, QWidget


def show_layers_panel(tree_view: QWidget) -> bool:
    """Show and raise the dock holding ``tree_view``. False when it sits in
    no dock (a QGIS layout that embeds the tree elsewhere)."""
    widget = tree_view
    while widget is not None and not isinstance(widget, QDockWidget):
        widget = widget.parentWidget()
    if widget is None:
        return False
    widget.show()
    widget.raise_()
    return True
