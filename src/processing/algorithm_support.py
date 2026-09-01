"""Shared pieces every AI Edit Processing algorithm needs.

The algorithms are thin adapters onto ``src/mcp_api.py``. Everything the three
of them have to agree on lives here: how the facade is found, how a facade
error becomes a Processing error, and the flag that keeps the run on the main
thread.
"""
from __future__ import annotations

from qgis.core import Qgis, QgsProcessingAlgorithm, QgsProcessingException

from ..core.i18n import tr
from ..mcp_api import AI_EDIT_KEYS, _find_plugin

# Same shape as the other plan links in the plugin, tagged for this surface so
# a visit that started in the Processing Toolbox can be told apart.
PROCESSING_PRICING_URL = (
    "https://terra-lab.ai/pricing?utm_source=qgis&utm_medium=processing&utm_campaign=toolbox"
)

# The page a Toolbox reader opens for the product itself.
PROCESSING_PRODUCT_URL = (
    "https://terra-lab.ai/ai-edit?utm_source=qgis&utm_medium=processing&utm_campaign=toolbox"
)

# The published algorithm ids. Callers hardcode them, so they never change,
# and every help text that names one reads it from here rather than quoting a
# label, which does change.
STATUS_ALGORITHM_ID = "terraedit:editstatus"
GENERATE_ALGORITHM_ID = "terraedit:generate"
VECTORIZE_ALGORITHM_ID = "terraedit:vectorize"


# The one plan line every help text ends on. Kept in one place so the wording
# cannot drift between algorithms. It states a limit of the tool, which a
# caller has to know before it spends anything. It is deliberately not a pitch:
# a description read by an AI assistant should say what the tool does and what
# stops it, and nothing about how the assistant ought to behave.
# A function, not a constant: a constant is built at import, and the locale is
# not settled then, so every reader would get the English line for the session.
def plan_help_line() -> str:
    """The plan sentence every help text ends on, in the user's language."""
    return tr(
        "Every generation is billed to the signed-in account, and a free account may only "
        "ask for the smallest output size. Run '{status_id}' to read the balance and the "
        "plan before anything is spent. Plan limits: {pricing_url}"
    ).format(status_id=STATUS_ALGORITHM_ID, pricing_url=PROCESSING_PRICING_URL)


# Shown when the build exposes neither name for the "run me on the main thread"
# flag. The algorithms drive Qt widgets, so a background run is a crash, not a
# slowdown: refusing is the safe answer.
def main_thread_flag_missing_message() -> str:
    """Why a build with no main-thread flag has its run refused."""
    return tr(
        "This QGIS build exposes no way to keep the run on the main thread. These "
        "algorithms drive the AI Edit panel, and driving it from a background thread would "
        "take QGIS down, so the run is refused. Update QGIS, or use the panel itself."
    )


class _ServedSearchTags(list):
    """Read-through list: ``+`` also appends the served extra tags.

    The algorithms build their tags as ``EDIT_SEARCH_TAGS + [...]`` and
    import the name once, so the union has to happen inside the object at use
    time. Union-only, through ``get_export_dial_seq``: a deploy can teach the
    fleet a new search word and can never take a shipped one away. Plain
    iteration sees the shipped entries only, like ``ServerDialSet``.
    """

    def _with_served_extras(self) -> list[str]:
        try:
            from ..core.config_store import get_export_dial_seq

            return list(get_export_dial_seq("processing.search_tags_extra", tuple(self)))
        except Exception:  # nosec B110 - the shipped words always work
            return list(self)

    def __add__(self, other):
        return self._with_served_extras() + list(other)


# The words a person types in the Toolbox filter, and the words a model reads
# when it lists algorithms through a generic MCP server.
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


# The message shown when the plugin object itself is missing, so the provider
# and the algorithms report the same cause with the same words.
def facade_missing_message() -> str:
    """Why an algorithm cannot find the plugin, and how to put that right."""
    return tr(
        "The AI Edit plugin is not loaded. Enable it in Plugins > Manage and Install "
        "Plugins, then reopen this algorithm. (Looked for: {keys})"
    ).format(keys=", ".join(AI_EDIT_KEYS))


def edit_facade():
    """Return the plugin's stable facade object, or None when it is absent.

    Reuses mcp_api's own lookup: QGIS registers a plugin under its install
    folder name, which differs between a release install and a checkout.
    """
    plugin = _find_plugin()
    if plugin is None:
        return None
    return getattr(plugin, "mcp_api", None)


def main_thread_only_flag():
    """The "run me on the main thread" flag, or None on a build carrying neither name.

    The flag moved from QgsProcessingAlgorithm.Flag to Qgis.ProcessingAlgorithmFlag,
    so resolve it by name. Never a bare int: QGIS 4 rejects arithmetic on one.
    """
    scope = getattr(Qgis, "ProcessingAlgorithmFlag", None)
    flag = getattr(scope, "NoThreading", None) if scope is not None else None
    if flag is None:
        flag = getattr(QgsProcessingAlgorithm, "FlagNoThreading", None)
    return flag


def no_threading_algorithm_flags(base_flags):
    """Add the "run me on the main thread" flag to an algorithm's own flags.

    The facade drives Qt widgets, so a background thread is not an option. When
    the flag is missing the base flags come back unchanged, and
    ``main_thread_run_refusal`` is what stops the run instead: raising here
    would break plugin load on a build nobody has tested yet.
    """
    flag = main_thread_only_flag()
    if flag is None:
        return base_flags
    return base_flags | flag


def main_thread_run_refusal() -> tuple[bool, str]:
    """Processing's canExecute answer: False when the main-thread flag is missing."""
    if main_thread_only_flag() is None:
        return False, main_thread_flag_missing_message()
    return True, ""


def refuse_when_threading_unsafe(feedback) -> None:
    """Stop a run that could land on a background thread.

    canExecute already refuses in the Toolbox, but a caller reaching
    processAlgorithm another way has to hit the same wall.
    """
    if main_thread_only_flag() is not None:
        return
    message = main_thread_flag_missing_message()
    feedback.reportError(message, fatalError=True)
    raise QgsProcessingException(message)


def generate_algorithm_label() -> str:
    """The generate algorithm's live label, read from the class so no help text quotes a stale one."""
    from .algorithm_generate_imagery import GenerateImageryAlgorithm
    return GenerateImageryAlgorithm().displayName()


def vectorize_algorithm_label() -> str:
    """The vectorize algorithm's live label, read from the class so no help text quotes a stale one."""
    from .algorithm_vectorize_color import VectorizeColorAlgorithm
    return VectorizeColorAlgorithm().displayName()


def status_algorithm_label() -> str:
    """The status algorithm's live label, read from the class so no help text quotes a stale one."""
    from .algorithm_edit_status import EditStatusAlgorithm
    return EditStatusAlgorithm().displayName()


def loaded_edit_facade(feedback):
    """Return the facade as soon as the plugin is loaded, with no account check.

    For work that runs on this machine and spends nothing. Asking for a signed-in
    account here would refuse a job the plugin can do offline.
    """
    refuse_when_threading_unsafe(feedback)
    api = edit_facade()
    if api is None:
        message = facade_missing_message()
        feedback.reportError(message, fatalError=True)
        raise QgsProcessingException(message)
    return api


def ready_edit_facade(feedback):
    """Return the facade only when the plugin says it can work right now.

    Raises rather than starting work the panel would refuse, and repeats the
    facade's own action_required text so the user reads one instruction.
    """
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
    """Turn the facade's ``_error`` key into a Processing failure.

    Facade calls never raise: a failure comes back as a plain dict. Nothing
    downstream would notice, so every call site passes its result through here.
    """
    if not isinstance(result, dict):
        message = tr("{action} returned nothing usable.").format(action=action)
        feedback.reportError(message, fatalError=True)
        raise QgsProcessingException(message)
    error = result.get("_error")
    if error:
        message = tr("{action} failed: {error}").format(action=action, error=error)
        feedback.reportError(message, fatalError=True)
        raise QgsProcessingException(message)
    return result


def integer_parameter_type():
    """The "whole number" flavour of QgsProcessingParameterNumber, on QGIS 3 and 4."""
    from qgis.core import QgsProcessingParameterNumber

    scope = getattr(Qgis, "ProcessingNumberParameterType", None)
    value = getattr(scope, "Integer", None) if scope is not None else None
    if value is None:
        value = getattr(QgsProcessingParameterNumber, "Integer", None)
    return value


def sleep_while_events_run(milliseconds: int) -> None:
    """Wait, without blocking the signals the plugin needs to finish its work.

    A generation runs in a background task and reports back through Qt signals
    delivered on the main thread. This algorithm holds that thread, so a plain
    sleep would stop the run it is waiting for. A nested event loop with a
    one-shot timer waits and lets those signals through.
    """
    from qgis.PyQt.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(max(1, int(milliseconds)), loop.quit)
    loop.exec()
