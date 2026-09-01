






from __future__ import annotations

from qgis.core import Qgis, QgsProcessingAlgorithm, QgsProcessingException

from ..core.i18n import tr
from ..mcp_api import AI_EDIT_KEYS, _find_plugin



PROCESSING_PRICING_URL = (
    "https://terra-lab.ai/pricing?utm_source=qgis&utm_medium=processing&utm_campaign=toolbox"
)


PROCESSING_PRODUCT_URL = (
    "https://terra-lab.ai/ai-edit?utm_source=qgis&utm_medium=processing&utm_campaign=toolbox"
)




STATUS_ALGORITHM_ID = "terraedit:editstatus"
GENERATE_ALGORITHM_ID = "terraedit:generate"
VECTORIZE_ALGORITHM_ID = "terraedit:vectorize"









def plan_help_line() -> str:

    return tr(
        "Every generation is billed to the signed-in account, and a free account may only "
        "ask for the smallest output size. Run '{status_id}' to read the balance and the "
        "plan before anything is spent. Plan limits: {pricing_url}"
    ).format(status_id=STATUS_ALGORITHM_ID, pricing_url=PROCESSING_PRICING_URL)





def main_thread_flag_missing_message() -> str:

    return tr(
        "This QGIS build exposes no way to keep the run on the main thread. These "
        "algorithms drive the AI Edit panel, and driving it from a background thread would "
        "take QGIS down, so the run is refused. Update QGIS, or use the panel itself."
    )


class _ServedSearchTags(list):









    def _with_served_extras(self) -> list[str]:
        try:
            from ..core.config_store import get_export_dial_seq

            return list(get_export_dial_seq("processing.search_tags_extra", tuple(self)))
        except Exception:  # nosec B110
            return list(self)

    def __add__(self, other):
        return self._with_served_extras() + list(other)




EDIT_SEARCH_TAGS = _ServedSearchTags([
    "ai",
    "edit",
    "generate",
    "imagery",
    "satellite",
    "aerial",
    "raster",
    "remote sensing",
    "land cover",
    "classify",
    "cloud removal",
    "super resolution",
    "upscale",
    "inpainting",
    "vectorize",
    "terralab",
])




def facade_missing_message() -> str:

    return tr(
        "The AI Edit plugin is not loaded. Enable it in Plugins > Manage and Install "
        "Plugins, then reopen this algorithm. (Looked for: {keys})"
    ).format(keys=", ".join(AI_EDIT_KEYS))


def edit_facade():





    plugin = _find_plugin()
    if plugin is None:
        return None
    return getattr(plugin, "mcp_api", None)


def main_thread_only_flag():





    scope = getattr(Qgis, "ProcessingAlgorithmFlag", None)
    flag = getattr(scope, "NoThreading", None) if scope is not None else None
    if flag is None:
        flag = getattr(QgsProcessingAlgorithm, "FlagNoThreading", None)
    return flag


def no_threading_algorithm_flags(base_flags):







    flag = main_thread_only_flag()
    if flag is None:
        return base_flags
    return base_flags | flag


def main_thread_run_refusal() -> tuple[bool, str]:

    if main_thread_only_flag() is None:
        return False, main_thread_flag_missing_message()
    return True, ""


def refuse_when_threading_unsafe(feedback) -> None:





    from qgis.PyQt.QtCore import QCoreApplication, QThread

    app = QCoreApplication.instance()
    if main_thread_only_flag() is not None and (app is None or QThread.currentThread() == app.thread()):
        return
    message = main_thread_flag_missing_message()
    feedback.reportError(message, fatalError=True)
    raise QgsProcessingException(message)


def generate_algorithm_label() -> str:

    from .algorithm_generate_imagery import GenerateImageryAlgorithm
    return GenerateImageryAlgorithm().displayName()


def vectorize_algorithm_label() -> str:

    from .algorithm_vectorize_color import VectorizeColorAlgorithm
    return VectorizeColorAlgorithm().displayName()


def status_algorithm_label() -> str:

    from .algorithm_edit_status import EditStatusAlgorithm
    return EditStatusAlgorithm().displayName()


def loaded_edit_facade(feedback):





    refuse_when_threading_unsafe(feedback)
    api = edit_facade()
    if api is None:
        message = facade_missing_message()
        feedback.reportError(message, fatalError=True)
        raise QgsProcessingException(message)
    return api


def ready_edit_facade(feedback):





    refuse_when_threading_unsafe(feedback)
    api = edit_facade()
    if api is None:
        message = facade_missing_message()
        feedback.reportError(message, fatalError=True)
        raise QgsProcessingException(message)

    status = api.get_status()
    if not isinstance(status, dict):
        message = tr("The AI Edit status call returned nothing usable.")
        feedback.reportError(message, fatalError=True)
        raise QgsProcessingException(message)
    if not status.get("installed") or not status.get("ready") or status.get("state") != "READY":
        message = status.get("action_required") or tr(
            "AI Edit is not ready. Open the AI Edit panel and finish the setup."
        )
        state = status.get("state")
        if state:
            message = tr("{message} (state: {state})").format(message=message, state=state)
        feedback.reportError(message, fatalError=True)
        raise QgsProcessingException(message)
    return api


def raise_on_facade_error(feedback, result, action: str) -> dict:





    if not isinstance(result, dict):
        message = tr("{action} returned nothing usable.").format(action=action)
        feedback.reportError(message, fatalError=True)
        raise QgsProcessingException(message)
    error = result.get("_error")
    if "_error" in result:
        message = tr("{action} failed: {error}").format(action=action, error=error or tr("Unknown error"))
        feedback.reportError(message, fatalError=True)
        raise QgsProcessingException(message)
    return result


def integer_parameter_type():

    from qgis.core import QgsProcessingParameterNumber

    scope = getattr(Qgis, "ProcessingNumberParameterType", None)
    value = getattr(scope, "Integer", None) if scope is not None else None
    if value is None:
        value = getattr(QgsProcessingParameterNumber, "Integer", None)
    return value


def sleep_while_events_run(milliseconds: int) -> None:







    from qgis.PyQt.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(max(1, int(milliseconds)), loop.quit)
    loop.exec()
