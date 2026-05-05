





from __future__ import annotations

from qgis.PyQt.QtWidgets import QDockWidget, QWidget


def show_layers_panel(tree_view: QWidget) -> bool:


    widget = tree_view
    while widget is not None and not isinstance(widget, QDockWidget):
        widget = widget.parentWidget()
    if widget is None:
        return False
    widget.show()
    widget.raise_()
    return True
