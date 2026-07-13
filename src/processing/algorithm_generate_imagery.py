






from __future__ import annotations

import time

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputBoolean,
    QgsProcessingOutputString,
    QgsProcessingParameterExtent,
    QgsProcessingParameterString,
)

from ..core.i18n import tr
from .algorithm_support import (
    EDIT_SEARCH_TAGS,
    PROCESSING_PRODUCT_URL,
    STATUS_ALGORITHM_ID,
    VECTORIZE_ALGORITHM_ID,
    main_thread_run_refusal,
    no_threading_algorithm_flags,
    plan_help_line,
    raise_on_facade_error,
    ready_edit_facade,
    sleep_while_events_run,
    status_algorithm_label,
    vectorize_algorithm_label,
)




GENERATION_TIMEOUT_SECONDS = 300



GENERATION_POLL_MILLISECONDS = 500


def generation_timeout_seconds() -> int:


    try:
        from ..core.config_store import get_export_dial

        value = get_export_dial("processing.generation_timeout_s", GENERATION_TIMEOUT_SECONDS)
    except Exception:  # nosec B110
        return GENERATION_TIMEOUT_SECONDS
    return value if 30 <= value <= 3600 else GENERATION_TIMEOUT_SECONDS


def generation_poll_milliseconds() -> int:


    try:
        from ..core.config_store import get_export_dial

        value = get_export_dial("processing.generation_poll_ms", GENERATION_POLL_MILLISECONDS)
    except Exception:  # nosec B110
        return GENERATION_POLL_MILLISECONDS
    return value if 100 <= value <= 5000 else GENERATION_POLL_MILLISECONDS


def offered_resolution_labels() -> str:





    try:
        from ..core.resolution_labels import resolution_tiers
        return ", ".join(resolution_tiers())
    except Exception:  # nosec B110
        return ""


class GenerateImageryAlgorithm(QgsProcessingAlgorithm):


    EXTENT = "EXTENT"
    PROMPT = "PROMPT"
    RESOLUTION = "RESOLUTION"
    TEMPLATE = "TEMPLATE"
    SUBMITTED = "SUBMITTED"
    STATE = "STATE"
    RESULT_LAYERS = "RESULT_LAYERS"
    STATUS = "STATUS"

    def createInstance(self):
        return GenerateImageryAlgorithm()



    def name(self):
        return "generate"




    def displayName(self):
        return tr(
            "Generate and edit satellite or aerial imagery with AI: classify land cover, "
            "remove clouds and objects, upscale to super resolution, simulate floods"
        )



    def shortDescription(self):
        return (
            "Generate or transform the satellite and aerial imagery over a map area from a text "
            "prompt: classify land cover, remove clouds and objects, upscale to super resolution, "
            "simulate a flood, and get a georeferenced image layer back."
        )

    def group(self):
        return tr("AI Edit")

    def groupId(self):
        return "aiedit"

    def tags(self):
        return EDIT_SEARCH_TAGS + [
            "prompt", "transform", "inpaint", "restore", "colorize", "flood", "simulate",
            "building footprint", "solar", "agriculture", "archaeology",
        ]



    def flags(self):
        return no_threading_algorithm_flags(super().flags())


    def canExecute(self):
        return main_thread_run_refusal()

    def shortHelpString(self):
        sizes = offered_resolution_labels()
        parts = [
            tr(
                "This run takes 30 to 120 seconds, and QGIS stays busy for the caller until "
                "it ends. Never start it again while it is running: a second run costs the "
                "user money."
            ),
            tr(
                "A bridge that calls this over a network usually gives up waiting after "
                "about a minute. The generation is NOT lost when that happens: it keeps "
                "running in QGIS and finishes on its own. Poll '{status_label}' until BUSY "
                "is false, then read the new layer in the project. Do not submit the run "
                "again, that is a second charge."
            ).format(status_label=status_algorithm_label()),
            tr(
                "Rewrites the imagery over a map area from a text prompt and puts the result "
                "on the map as a georeferenced image layer, lined up with the area you gave "
                "it."
            ),
            tr(
                "What it is for: classify land cover, extract building footprints as a "
                "colored picture, remove clouds, remove cars or buildings, upscale to super "
                "resolution, simulate a flood, color an old scanned map, restore a damaged "
                "photo."
            ),
            tr(
                "What to type in 'Prompt': plain words describing the picture you want back, "
                "for example 'color every building red and everything else grey' or 'remove "
                "the clouds' or 'upscale and sharpen'."
            ),
            tr(
                "The area is read from the map canvas view, so the imagery under it is what "
                "the AI sees. Zoom in far enough that the detail you are asking about is "
                "visible, and hide any layer you do not want sent."
            ),
        ]
        if sizes:
            parts.append(
                tr("Output sizes this build offers, smallest first: {sizes}.").format(sizes=sizes)
            )
        parts.extend([
            tr(
                "What it returns: SUBMITTED (whether the run started), STATE ('done', "
                "'generating', 'cancelled' or 'idle'), RESULT_LAYERS (the names of the image "
                "layers this run added to the project) and STATUS. There is no file output: "
                "the plugin adds the georeferenced result to the project itself, under its "
                "own layer group."
            ),
            tr(
                "To turn one flat color of the result into polygons afterwards, run "
                "'{vectorize_id}' ('{vectorize_label}')."
            ).format(
                vectorize_id=VECTORIZE_ALGORITHM_ID,
                vectorize_label=vectorize_algorithm_label(),
            ),
            tr(
                "Run '{status_label}' ('{status_id}') first. It answers in a moment, spends "
                "nothing, and tells you whether this one can run at all."
            ).format(status_label=status_algorithm_label(), status_id=STATUS_ALGORITHM_ID),
            tr(
                "This runs on the AI service, so it needs an internet connection and a "
                "signed-in TerraLab account. Open the AI Edit panel once to sign in."
            ),
            plan_help_line(),
        ])
        return "\n\n".join(parts)

    def helpUrl(self):
        return PROCESSING_PRODUCT_URL

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, tr("Map area to edit")))
        self.addParameter(QgsProcessingParameterString(
            self.PROMPT,
            tr("Prompt (what the picture should look like)"),
            multiLine=True))
        sizes = offered_resolution_labels()
        self.addParameter(QgsProcessingParameterString(
            self.RESOLUTION,
            tr("Output size, leave empty for the one selected in the panel")
            + (f" ({sizes})" if sizes else ""),
            defaultValue="",
            optional=True))


        self.addParameter(QgsProcessingParameterString(
            self.TEMPLATE,
            tr("Prompt preset id (optional)"),
            defaultValue="",
            optional=True))



        self.addOutput(QgsProcessingOutputBoolean(
            self.SUBMITTED, tr("Run started")))
        self.addOutput(QgsProcessingOutputString(
            self.STATE, tr("State")))
        self.addOutput(QgsProcessingOutputString(
            self.RESULT_LAYERS, tr("Names of the image layers added to the project")))
        self.addOutput(QgsProcessingOutputString(
            self.STATUS, tr("Status")))

    def processAlgorithm(self, parameters, context, feedback):
        if feedback.isCanceled():
            raise QgsProcessingException(tr("Cancelled before starting the run."))
        api = ready_edit_facade(feedback)

        prompt = (self.parameterAsString(parameters, self.PROMPT, context) or "").strip()
        if not prompt:
            message = tr("Type a prompt, for example 'remove the clouds'.")
            feedback.reportError(message, fatalError=True)
            raise QgsProcessingException(message)



        extent = self.parameterAsExtent(parameters, self.EXTENT, context, self._canvas_crs(context))
        if extent is None or extent.isEmpty():
            message = tr("The area is empty. Draw a rectangle over the imagery.")
            feedback.reportError(message, fatalError=True)
            raise QgsProcessingException(message)

        resolution = (self.parameterAsString(parameters, self.RESOLUTION, context) or "").strip()
        template = (self.parameterAsString(parameters, self.TEMPLATE, context) or "").strip()

        before = {layer_id for layer_id, _name in self._project_result_layers(api)}

        feedback.pushInfo(
            tr("Sending the area to the AI service. Prompt: {prompt}").format(prompt=prompt))
        feedback.pushInfo(tr(
            "This takes 30 to 120 seconds and QGIS stays busy until it ends. Do not start it "
            "again: a second run costs money."))

        if feedback.isCanceled():
            raise QgsProcessingException(tr("Cancelled before starting the run."))
        submitted = api.generate(
            prompt=prompt,
            bbox=[extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()],
            resolution=resolution or None,
            template_id=template or None,
        )
        raise_on_facade_error(feedback, submitted, "Generate")
        if not submitted.get("submitted"):
            message = tr("AI Edit did not take the run.")
            feedback.reportError(message, fatalError=True)
            raise QgsProcessingException(message)

        used_size = submitted.get("resolution")
        if used_size:
            feedback.pushInfo(tr("Output size: {size}.").format(size=used_size))

        timeout_s = generation_timeout_seconds()
        status, ending = self._wait_for_the_run(api, feedback, timeout_s)



        new_layers = [name for layer_id, name in self._layers_of(status) if layer_id not in before]
        if not new_layers:
            new_layers = [
                name for layer_id, name in self._project_result_layers(api)
                if layer_id not in before
            ]

        state = str(status.get("state") or "")
        if ending == "timeout":
            message = tr(
                "Still running after {seconds} seconds. The run was NOT cancelled and is "
                "still going. Poll '{status_id}' until BUSY is false, then read the new "
                "layer in the project. Do not submit it again."
            ).format(seconds=timeout_s, status_id=STATUS_ALGORITHM_ID)
            feedback.pushWarning(message)
            return {
                self.SUBMITTED: True,
                self.STATE: state or "generating",
                self.RESULT_LAYERS: ", ".join(new_layers),
                self.STATUS: message,
            }

        if ending == "cancelled":
            message = tr("Cancelled. You asked for it to stop, so the run was not finished.")
            feedback.pushWarning(message)
            return {
                self.SUBMITTED: True,
                self.STATE: "cancelled",
                self.RESULT_LAYERS: ", ".join(new_layers),
                self.STATUS: message,
            }

        failure = str(status.get("error") or "").strip()
        failure_code = str(status.get("error_code") or "").strip()
        if new_layers:
            feedback.pushInfo(
                tr("Added to the project: {layers}.").format(layers=", ".join(new_layers)))
            outcome = tr("completed, {count} layer(s) added").format(count=len(new_layers))
        elif failure:


            outcome = tr("failed: {reason}").format(reason=failure)
            if failure_code:
                outcome = tr("{outcome} (code: {code})").format(
                    outcome=outcome, code=failure_code)
            feedback.pushWarning(outcome)
        else:
            dock_line = str(status.get("dock_status") or "").strip()
            outcome = tr("finished with no new layer")
            if dock_line:
                outcome = tr("{outcome}: {panel_line}").format(
                    outcome=outcome, panel_line=dock_line)
            feedback.pushWarning(tr(
                "The run ended without adding a layer. Read the AI Edit panel for the "
                "reason."))

        return {
            self.SUBMITTED: True,
            self.STATE: state,
            self.RESULT_LAYERS: ", ".join(new_layers),
            self.STATUS: outcome,
        }

    def _wait_for_the_run(self, api, feedback, timeout_s: int):






        started = time.monotonic()
        last_told = 0.0
        poll_ms = generation_poll_milliseconds()
        status = api.generation_status()
        while True:
            raise_on_facade_error(feedback, status, "Generation status")
            if status.get("running") is False:
                feedback.setProgress(100)
                return status, "finished"

            if feedback.isCanceled():
                feedback.pushInfo(tr("Cancelling the run."))
                raise_on_facade_error(feedback, api.cancel(), "Cancel")
                final_status = api.generation_status()
                raise_on_facade_error(feedback, final_status, "Generation status")
                return final_status, "cancelled"

            elapsed = time.monotonic() - started
            if elapsed >= timeout_s:
                return status if isinstance(status, dict) else {}, "timeout"

            feedback.setProgress(min(95, int(100 * elapsed / timeout_s)))
            if elapsed - last_told >= 10:
                last_told = elapsed
                line = str(status.get("dock_status") or "").strip() if isinstance(status, dict) else ""
                feedback.pushInfo(tr("Still running after {seconds}s. {panel_line}").format(
                    seconds=int(elapsed), panel_line=line).strip())

            sleep_while_events_run(poll_ms)
            status = api.generation_status()

    @staticmethod
    def _canvas_crs(context):

        try:
            from qgis.utils import iface
            canvas = iface.mapCanvas() if iface is not None else None
            if canvas is not None:
                return canvas.mapSettings().destinationCrs()
        except Exception:  # nosec B110
            pass
        project = context.project()
        return project.crs() if project is not None else None

    @staticmethod
    def _layers_of(status) -> list[tuple[str, str]]:





        if not isinstance(status, dict):
            return []
        raw_names = status.get("result_layers") or []
        raw_ids = status.get("result_layer_ids") or []
        if not isinstance(raw_names, (list, tuple)) or not isinstance(raw_ids, (list, tuple)):
            return []
        names = [str(name) for name in raw_names]
        ids = [str(layer_id) for layer_id in raw_ids]
        if len(ids) != len(names):
            return [(name, name) for name in names]
        return list(zip(ids, names))

    def _project_result_layers(self, api) -> list[tuple[str, str]]:

        return self._layers_of(api.generation_status())
