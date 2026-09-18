
from __future__ import annotations

from qgis.core import QgsApplication

from ...core.logger import log_warning


class ProcessingRegistrationMixin:
    def _register_processing_provider(self):






        from ...processing.edit_provider import TerraEditProcessingProvider

        provider = TerraEditProcessingProvider()
        provider_id = provider.id()




        registry = QgsApplication.processingRegistry()
        if not registry.addProvider(provider):









            registry.removeProvider(provider_id)
            provider = TerraEditProcessingProvider()
            if not registry.addProvider(provider):
                self._processing_provider = None
                log_warning(
                    f"Processing provider '{provider_id}' was not registered: "
                    "the id is already taken."
                )
                return
        self._processing_provider = provider

    def _unregister_processing_provider(self):

        provider = getattr(self, "_processing_provider", None)



        self._processing_provider = None
        if provider is None:
            return
        from ...processing.edit_provider import TERRAEDIT_PROVIDER_ID




        QgsApplication.processingRegistry().removeProvider(TERRAEDIT_PROVIDER_ID)
