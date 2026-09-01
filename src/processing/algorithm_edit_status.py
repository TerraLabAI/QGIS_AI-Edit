"""Processing algorithm: report whether AI Edit can run right now.

The cheap one. It answers in a moment, spends nothing, and names the two
algorithms that do the work. A caller that can run an algorithm but cannot
list one still learns their ids from here.
"""
from __future__ import annotations

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputBoolean,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
)

from ..core.i18n import tr
from .algorithm_support import (
    EDIT_SEARCH_TAGS,
    GENERATE_ALGORITHM_ID,
    PROCESSING_PRODUCT_URL,
    VECTORIZE_ALGORITHM_ID,
    edit_facade,
    facade_missing_message,
    generate_algorithm_label,
    main_thread_run_refusal,
    no_threading_algorithm_flags,
    plan_help_line,
    refuse_when_threading_unsafe,
    vectorize_algorithm_label,
)


class EditStatusAlgorithm(QgsProcessingAlgorithm):
    """Readiness check: what is signed in, what is missing, what to run next."""

    INSTALLED = "INSTALLED"
    READY = "READY"
    STATE = "STATE"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    PLAN = "PLAN"
    CREDITS_REMAINING = "CREDITS_REMAINING"
    BUSY = "BUSY"
    NEXT_ALGORITHMS = "NEXT_ALGORITHMS"

    def createInstance(self):
        return EditStatusAlgorithm()

    # Published interface. Callers hardcode "terraedit:editstatus", so this
    # string never changes.
    def name(self):
        return "editstatus"

    # A generic MCP server searches algorithms by plain substring over the id
    # and this label, and nothing else. So the words a person actually types
    # have to be in here, not only in tags or in the help text.
    def displayName(self):
        return tr(
            "Check AI Edit status, credits and plan before you generate or edit satellite "
            "imagery"
        )

    # What a generic MCP server hands back as this algorithm's description, in
    # full. One dense sentence, carrying the nouns that make it findable.
    def shortDescription(self):
        return (
            "Report whether AI Edit is signed in and ready to generate or edit satellite and "
            "aerial imagery, how many credits are left, and what to run next."
        )

    def group(self):
        return tr("AI Edit")

    def groupId(self):
        return "aiedit"

    def tags(self):
        return EDIT_SEARCH_TAGS + [
            "status", "ready", "check", "credits", "plan", "setup", "account", "health",
        ]

    # Reads the live plugin panel, so it belongs on the main thread like the
    # two algorithms it introduces.
    def flags(self):
        return no_threading_algorithm_flags(super().flags())

    # Refuse rather than run on a build where that flag does not exist.
    def canExecute(self):
        return main_thread_run_refusal()

    def shortHelpString(self):
        return "\n\n".join([
            tr(
                "Run this first. It says whether AI Edit can work right now, it spends "
                "nothing, and it answers in a moment."
            ),
            tr(
                "AI Edit rewrites aerial and satellite imagery from a text prompt and gives "
                "the result back as a georeferenced image on the map. Land cover "
                "classification, building footprints, cloud removal, object removal, super "
                "resolution upscaling, flood simulation, coloring an old map. One prompt, "
                "one picture back."
            ),
            tr(
                "Two algorithms do the work, and you can run either by id:\n"
                "  {generate_id} - '{generate_label}'. Give it a map area and a prompt. It "
                "returns a new image layer over that area. It runs on the AI service, takes "
                "30 to 120 seconds, and QGIS stays busy until it ends. Never start it "
                "twice: a second run costs the user money.\n"
                "  {vectorize_id} - '{vectorize_label}'. Give it one flat color of a result "
                "image and it traces that color into a polygon layer. Free, local, needs no "
                "account, and usually a few seconds."
            ).format(
                generate_id=GENERATE_ALGORITHM_ID,
                generate_label=generate_algorithm_label(),
                vectorize_id=VECTORIZE_ALGORITHM_ID,
                vectorize_label=vectorize_algorithm_label(),
            ),
            tr(
                "What this one returns: INSTALLED and READY (true or false), STATE (READY, "
                "NEEDS_ACTIVATION or NO_PANEL), ACTION_REQUIRED (what a person has to do "
                "when READY is false), PLAN ('free', 'pro' or empty when unknown), "
                "CREDITS_REMAINING, BUSY (true while a generation is already running) and "
                "NEXT_ALGORITHMS. CREDITS_REMAINING is -1 when the account did not report a "
                "number, which usually means nobody is signed in yet."
            ),
            tr(
                "When READY is false, read ACTION_REQUIRED out to the user and stop. "
                "Signing in happens in the AI Edit panel in QGIS, not from here, and no "
                "algorithm can do it for the user."
            ),
            tr(
                "When BUSY is true, a generation is already running. Wait for it and poll "
                "this algorithm again rather than starting another one."
            ),
            plan_help_line(),
        ])

    def helpUrl(self):
        return PROCESSING_PRODUCT_URL

    def initAlgorithm(self, config=None):
        # No inputs on purpose: the answer is about the plugin, not about a
        # layer, so a caller can run this with an empty parameter dict.
        self.addOutput(QgsProcessingOutputBoolean(
            self.INSTALLED, tr("Plugin installed")))
        self.addOutput(QgsProcessingOutputBoolean(
            self.READY, tr("Ready to run")))
        self.addOutput(QgsProcessingOutputString(
            self.STATE, tr("State")))
        self.addOutput(QgsProcessingOutputString(
            self.ACTION_REQUIRED, tr("What the user has to do")))
        self.addOutput(QgsProcessingOutputString(
            self.PLAN, tr("Plan on the signed-in account")))
        self.addOutput(QgsProcessingOutputNumber(
            self.CREDITS_REMAINING, tr("Credits left on the plan")))
        self.addOutput(QgsProcessingOutputBoolean(
            self.BUSY, tr("A generation is already running")))
        self.addOutput(QgsProcessingOutputString(
            self.NEXT_ALGORITHMS, tr("Algorithms that do the work")))

    def processAlgorithm(self, parameters, context, feedback):
        refuse_when_threading_unsafe(feedback)
        api = edit_facade()
        if api is None:
            missing = facade_missing_message()
            feedback.reportError(missing, fatalError=True)
            raise QgsProcessingException(missing)

        status = api.get_status()
        if not isinstance(status, dict):
            message = tr("The status call returned nothing usable.")
            feedback.reportError(message, fatalError=True)
            raise QgsProcessingException(message)

        state = str(status.get("state") or "")
        ready = bool(status.get("ready"))
        action = str(status.get("action_required") or "")
        busy = bool(status.get("busy"))

        feedback.pushInfo(tr("State: {state}. Ready: {ready}. Busy: {busy}.").format(
            state=state or tr("unknown"), ready=ready, busy=busy))
        if action:
            feedback.pushInfo(action)

        remaining, credits_error = self._credits_remaining(api) if ready else (-1, "")
        if remaining >= 0:
            feedback.pushInfo(tr("Credits left: {count}.").format(count=remaining))
        elif ready:
            # READY plus a negative balance means the account read failed, not
            # that nobody is signed in. Say which, or the help text's own
            # reading of -1 sends the caller after the wrong problem.
            feedback.pushWarning(tr(
                "The balance could not be read, so CREDITS_REMAINING is -1 rather than a "
                "count. Reason: {reason}."
            ).format(reason=credits_error or tr("the account returned no number")))

        return {
            self.INSTALLED: bool(status.get("installed")),
            self.READY: ready,
            self.STATE: state,
            self.ACTION_REQUIRED: action,
            self.PLAN: str(status.get("plan") or ""),
            self.CREDITS_REMAINING: remaining,
            self.BUSY: busy,
            self.NEXT_ALGORITHMS: f"{GENERATE_ALGORITHM_ID}, {VECTORIZE_ALGORITHM_ID}",
        }

    @staticmethod
    def _credits_remaining(api) -> tuple[int, str]:
        """Credits left, and why they are unknown when they are.

        The account reports what it has used against a limit, so the number a
        caller acts on is the subtraction, done once here. Returns -1 and the
        reason when there is no number to subtract.
        """
        credits = api.get_credits()
        if not isinstance(credits, dict):
            return -1, tr("the account call returned nothing usable")
        failure = credits.get("_error") or credits.get("error")
        if failure:
            return -1, str(failure)
        try:
            used = int(credits.get("used"))
            limit = int(credits.get("limit"))
        except (TypeError, ValueError):
            return -1, tr("the account reported no usage counts")
        return max(0, limit - used), ""
