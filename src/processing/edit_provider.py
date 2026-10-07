






from __future__ import annotations

import os

from qgis.core import QgsProcessingProvider

from .algorithm_edit_status import EditStatusAlgorithm
from .algorithm_generate_imagery import GenerateImageryAlgorithm
from .algorithm_support import edit_facade, facade_missing_message
from .algorithm_vectorize_color import VectorizeColorAlgorithm




TERRAEDIT_PROVIDER_ID = "terraedit"


_PROVIDER_ICON = None


class TerraEditProcessingProvider(QgsProcessingProvider):


    def id(self):
        return TERRAEDIT_PROVIDER_ID

    def name(self):
        return "TerraLab AI Edit"

    def longName(self):
        return "TerraLab AI Edit for QGIS"

    def icon(self):



        global _PROVIDER_ICON
        if _PROVIDER_ICON is None:
            from ..core.qimage_strips import icon_from_file
            icon_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "resources", "icons", "icon.png",
            )
            _PROVIDER_ICON = icon_from_file(icon_path) if os.path.exists(icon_path) else False
        if _PROVIDER_ICON is False or _PROVIDER_ICON.isNull():
            return super().icon()
        return _PROVIDER_ICON

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
