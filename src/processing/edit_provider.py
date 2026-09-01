






from __future__ import annotations

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm_edit_status import EditStatusAlgorithm
from .algorithm_generate_imagery import GenerateImageryAlgorithm
from .algorithm_support import edit_facade, facade_missing_message
from .algorithm_vectorize_color import VectorizeColorAlgorithm




TERRAEDIT_PROVIDER_ID = "terraedit"


class TerraEditProcessingProvider(QgsProcessingProvider):


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







        return edit_facade() is not None

    def warningMessage(self):

        if edit_facade() is None:
            return facade_missing_message()
        return ""
