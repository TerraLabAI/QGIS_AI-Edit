"""Processing algorithm: trace one flat color of a result image into polygons.

A thin adapter onto EditMCPAPI.vectorize. The tracing itself lives in the
plugin's vectorization service, and none of it should move here.

The plugin adds the traced layer to the project itself, so this algorithm
declares no vector destination: a Processing sink copy of it would be a second
layer of the same geometry, without the style the plugin put on the first one.
"""
from __future__ import annotations

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsProcessingParameterColor,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProject,
)
from qgis.PyQt.QtGui import QColor

from ..core.i18n import tr
from .algorithm_support import (
    EDIT_SEARCH_TAGS,
    GENERATE_ALGORITHM_ID,
    PROCESSING_PRODUCT_URL,
    integer_parameter_type,
    loaded_edit_facade,
    main_thread_run_refusal,
    no_threading_algorithm_flags,
    raise_on_facade_error,
)

# What the plugin's own tracer uses when no tolerance is given. Repeated here
# only to show the user the number they are overriding.
DEFAULT_COLOR_TOLERANCE = 40


class VectorizeColorAlgorithm(QgsProcessingAlgorithm):
    """One flat color of an AI Edit result image, as a polygon layer."""

    SOURCE = "SOURCE"
    TARGET_COLOR = "TARGET_COLOR"
    TOLERANCE = "TOLERANCE"
    CLASS_LABEL = "CLASS_LABEL"
    FEATURE_COUNT = "FEATURE_COUNT"
    LAYER_NAME = "LAYER_NAME"
    STATUS = "STATUS"

    def createInstance(self):
        return VectorizeColorAlgorithm()

    # Published interface. Callers hardcode "terraedit:vectorize", so this
    # string never changes.
    def name(self):
        return "vectorize"

    # A generic MCP server searches algorithms by plain substring over the id
    # and this label, and nothing else. So the words a person actually types
    # have to be in here, not only in tags or in the help text.
    def displayName(self):
        return tr(
            "Vectorize a color of an AI result into polygons (land cover classes, raster to "
            "vector)"
        )

    # What a generic MCP server hands back as this algorithm's description, in
    # full. One dense sentence, carrying the nouns that make it findable.
    def shortDescription(self):
        return (
            "Trace one flat color of an AI Edit result image into a polygon layer, turning a "
            "classified land cover picture into editable vector data. Local and free."
        )

    def group(self):
        return tr("AI Edit")

    def groupId(self):
        return "aiedit"

    def tags(self):
        return EDIT_SEARCH_TAGS + [
            "polygonize", "raster to vector", "digitize", "trace", "color", "colour",
            "class", "extract", "polygons",
        ]

    # Reads the project through the plugin facade, which is Qt-bound, so this
    # stays on the main thread like the other two.
    def flags(self):
        return no_threading_algorithm_flags(super().flags())

    # Refuse rather than run on a build where that flag does not exist.
    def canExecute(self):
        return main_thread_run_refusal()

    def shortHelpString(self):
        return "\n\n".join([
            tr(
                "Turns one flat color of an AI Edit result image into polygons you can edit, "
                "measure and export. It runs on your machine, spends nothing, and usually "
                "takes a few seconds."
            ),
            tr(
                "No account is needed for this one. The AI Edit plugin has to be loaded, and "
                "nothing else: the tracing reads the picture on this computer and sends "
                "nothing."
            ),
            tr(
                "Use it after '{generate_id}' has painted a class in one color, for example "
                "'color every building red and everything else grey'. Point this algorithm "
                "at red and you get one polygon per building."
            ).format(generate_id=GENERATE_ALGORITHM_ID),
            tr(
                "Source image: leave it empty to use the newest AI Edit result in the "
                "project, or name a raster layer to trace a different one. The layer has to "
                "be in the project, not a file picked from disk."
            ),
            tr(
                "Color to trace: the exact color of the class in the picture. Widen the "
                "tolerance when the color is not perfectly flat; narrow it when two classes "
                "bleed into each other. The tracer uses {tolerance} when you leave it empty."
            ).format(tolerance=DEFAULT_COLOR_TOLERANCE),
            tr(
                "Class name: written on every polygon, so several runs over the same image "
                "stay apart once they are merged."
            ),
            tr(
                "What it returns: no file output. The plugin adds one styled polygon layer "
                "to the project beside the image it came from, and this algorithm reports "
                "LAYER_NAME, FEATURE_COUNT and STATUS. That layer lives in memory until you "
                "save it, so use 'Make permanent' on it, or export it, before closing the "
                "project."
            ),
            tr(
                "The imagery is never sent anywhere for this: the tracing is local, and it "
                "is not billed to any plan."
            ),
        ])

    def helpUrl(self):
        return PROCESSING_PRODUCT_URL

    def initAlgorithm(self, config=None):
        # Optional: the newest AI Edit result is what a caller almost always
        # means, so naming a layer is the exception, not the rule.
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.SOURCE, tr("Result image to trace (leave empty for the newest one)"),
            optional=True))
        self.addParameter(QgsProcessingParameterColor(
            self.TARGET_COLOR, tr("Color to trace"),
            defaultValue=QColor(255, 0, 0), opacityEnabled=False))
        # Left empty on purpose: the tracer has its own default, and mirroring
        # it as a parameter default would freeze it here.
        self.addParameter(QgsProcessingParameterNumber(
            self.TOLERANCE,
            tr("Color tolerance 0 to 255 (leave empty for {tolerance})").format(
                tolerance=DEFAULT_COLOR_TOLERANCE),
            type=integer_parameter_type(),
            minValue=0,
            maxValue=255,
            defaultValue=None,
            optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.CLASS_LABEL, tr("Class name written on every polygon (optional)"),
            defaultValue="", optional=True))

        self.addOutput(QgsProcessingOutputNumber(
            self.FEATURE_COUNT, tr("Number of polygons traced")))
        self.addOutput(QgsProcessingOutputString(
            self.LAYER_NAME, tr("Name of the layer added to the project")))
        self.addOutput(QgsProcessingOutputString(
            self.STATUS, tr("Status")))

    def processAlgorithm(self, parameters, context, feedback):
        # The tracing runs on this computer and spends nothing, so this one asks
        # only that the plugin is loaded. Demanding a signed-in account here
        # refused a job that needs no account.
        api = loaded_edit_facade(feedback)

        rgb = self._target_rgb(parameters, context)
        if rgb is None:
            message = tr("Pick the color to trace, as a color or as '#rrggbb'.")
            feedback.reportError(message, fatalError=True)
            raise QgsProcessingException(message)

        source = self.parameterAsRasterLayer(parameters, self.SOURCE, context)
        # By id from here on. The facade matches a name on any part of it, so
        # "result" also matches "result 2", and a file picked in this dialog
        # loads into the Processing context rather than into the project, where
        # a name lookup finds nothing at all.
        source_id = source.id() if source is not None else ""
        if source_id and QgsProject.instance().mapLayer(source_id) is None:
            message = tr(
                "That image is not in the project, so there is nothing to trace beside. Add "
                "the layer to the project first, then run this again."
            )
            feedback.reportError(message, fatalError=True)
            raise QgsProcessingException(message)
        tolerance = None
        if parameters.get(self.TOLERANCE) is not None:
            tolerance = self.parameterAsInt(parameters, self.TOLERANCE, context)
        class_label = (self.parameterAsString(parameters, self.CLASS_LABEL, context) or "").strip()

        feedback.pushInfo(tr("Tracing color {color} on {image}.").format(
            color=rgb, image=source.name() if source else tr("the newest result")))
        result = api.vectorize(
            target_rgb=list(rgb),
            layer_id=source_id or None,
            tolerance=tolerance,
            class_label=class_label,
        )
        raise_on_facade_error(feedback, result, "Vectorize")

        # The facade hands back the layer's id, so the lookup is by id and never
        # by name: two runs can produce the same friendly name in one project.
        traced = QgsProject.instance().mapLayer(str(result.get("layer_id") or ""))
        if traced is None:
            message = tr("The tracing finished but its layer is not in the project.")
            feedback.reportError(message, fatalError=True)
            raise QgsProcessingException(message)

        counted = traced.featureCount()
        feedback.setProgress(100)
        feedback.pushInfo(tr("Traced {count} polygon(s) from {image}.").format(
            count=counted, image=result.get("source_raster")))
        return {
            self.FEATURE_COUNT: counted,
            self.LAYER_NAME: traced.name(),
            self.STATUS: tr("completed, {count} polygon(s) in one layer").format(count=counted),
        }

    def _target_rgb(self, parameters, context):
        """The color to trace as (r, g, b), or None when it cannot be read.

        A bridge passes this parameter as text, so a value Processing cannot
        decode as a color is tried again as '#rrggbb' or as 'r,g,b'.
        """
        color = self.parameterAsColor(parameters, self.TARGET_COLOR, context)
        if color is not None and color.isValid():
            return (color.red(), color.green(), color.blue())

        raw = str(parameters.get(self.TARGET_COLOR) or "").strip()
        if not raw:
            return None
        named = QColor(raw)
        if named.isValid():
            return (named.red(), named.green(), named.blue())
        parts = [piece.strip() for piece in raw.replace(";", ",").split(",")]
        if len(parts) < 3:
            return None
        try:
            channels = [max(0, min(255, int(float(piece)))) for piece in parts[:3]]
        except (TypeError, ValueError):
            return None
        return tuple(channels)
