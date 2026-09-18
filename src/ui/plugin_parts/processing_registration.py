"""Registration of the AI Edit Processing provider."""
from __future__ import annotations

from qgis.core import QgsApplication

from ...core.logger import log_warning


class ProcessingRegistrationMixin:
    def _register_processing_provider(self):
        """Add the AI Edit provider to the Processing registry.

        Imported here rather than at module level so plugin load stays light,
        and so a QGIS build without the Processing plugin enabled fails on this
        one call instead of on the import of the whole controller.
        """
        from ...processing.edit_provider import TerraEditProcessingProvider

        provider = TerraEditProcessingProvider()
        provider_id = provider.id()
        # addProvider returns False AND deletes the provider it was given when
        # the id is already taken, which leaves a Python wrapper around a dead
        # C++ object. Holding that would make the matching removeProvider raise
        # on unload, so drop the reference instead of keeping a corpse.
        registry = QgsApplication.processingRegistry()
        if not registry.addProvider(provider):
            # Reloading the plugin can leave the previous provider behind with
            # its Python half collected: it answers to no id and lists no
            # algorithm, and it holds the name against us. Whoever reloaded
            # would have no algorithms until they restart QGIS, so take the id
            # back rather than stopping here. By id, never by object: the
            # object overload calls provider->id(), the pure virtual whose
            # Python override is exactly what a half-collected provider has
            # lost. A fresh instance is needed because the one above is
            # already deleted.
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
        """Remove the provider, so a reload does not leave two of them registered."""
        provider = getattr(self, "_processing_provider", None)
        # Dropped before the call, never after: a raise on an already-deleted
        # provider would otherwise leave the attribute set and the next unload
        # would retry the same dead object.
        self._processing_provider = None
        if provider is None:
            return
        from ...processing.edit_provider import TERRAEDIT_PROVIDER_ID

        # By id, never by object: the object overload calls provider->id() in
        # C++, and that override is gone the moment the Python half is
        # collected. The id is a constant, so it survives.
        QgsApplication.processingRegistry().removeProvider(TERRAEDIT_PROVIDER_ID)
