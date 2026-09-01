"""The QGIS Processing provider that publishes AI Edit to everything.

Registering here is what makes the plugin reachable from the Processing
Toolbox, the Graphical Modeler, batch mode, PyQGIS scripts and every
third-party MCP server that exposes a generic "run a processing algorithm"
tool. Nothing else has to be written on either side.
"""
from __future__ import annotations

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm_edit_status import EditStatusAlgorithm
from .algorithm_generate_imagery import GenerateImageryAlgorithm
from .algorithm_support import edit_facade, facade_missing_message
from .algorithm_vectorize_color import VectorizeColorAlgorithm

# Published interface: an algorithm is addressed as "terraedit:<name>". Callers
# hardcode it, so this string never changes. It is deliberately not "terralab":
# AI Segmentation owns that id, and two providers sharing one id collide.
TERRAEDIT_PROVIDER_ID = "terraedit"


class TerraEditProcessingProvider(QgsProcessingProvider):
    """Holds the AI Edit algorithms and their shared identity."""

    def id(self):
        return TERRAEDIT_PROVIDER_ID

    def name(self):
        return "TerraLab AI Edit"

    def longName(self):
        return "TerraLab AI Edit for QGIS"

    def icon(self):
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "resources", "icons", "icon.png",
        )
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        return super().icon()

    def loadAlgorithms(self):
        self.addAlgorithm(EditStatusAlgorithm())
        self.addAlgorithm(GenerateImageryAlgorithm())
        self.addAlgorithm(VectorizeColorAlgorithm())

    def canBeActivated(self):
        """Grey the whole provider out when the plugin is gone.

        canBeActivated and warningMessage are the two QgsProcessingProvider
        actually calls. canExecute belongs to QgsProcessingAlgorithm, so the
        method that used to sit here overrode nothing and the provider was
        never greyed out.
        """
        return edit_facade() is not None

    def warningMessage(self):
        """The reason the Toolbox shows beside a greyed-out provider."""
        if edit_facade() is None:
            return facade_missing_message()
        return ""
