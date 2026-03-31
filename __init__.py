import os

PLUGIN_DIR = os.path.dirname(__file__)


def classFactory(iface):
    from .src.ui.plugin import AIEditPlugin
    return AIEditPlugin(iface)
