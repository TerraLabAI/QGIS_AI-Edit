"""Advanced card of the settings dialog's Account page.

Local settings only: the telemetry opt-out, guidance tips, output folder.
Nothing here touches the account or the network. Split out so the dialog
itself stays about the account and the plan.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QDir
from qgis.PyQt.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QWidget

from ...core.i18n import tr
from ..onboarding_hint import reset_hints
from ..raster_writer import _documents_default_dir, get_output_dir, set_output_dir


class AccountPreferencesMixin:
    """Builds the Advanced card; the dialog owns the page."""

    def _build_advanced_group(self, parent: QWidget):
        """The Account page's Advanced card: statistics, tips, output folder.

        The statistics switch flips the shared TerraLab/telemetry_enabled flag
        (core/telemetry.py), so turning it off here also silences the sibling
        AI Segmentation plugin. It exists because the published privacy policy
        had to tell AI Edit users to email us to object.
        """
        from ...core.telemetry import is_telemetry_enabled
        from ..dock.design_tokens import BTN_GHOST_QSS, INPUT_QSS
        from .settings.widgets import SettingGroup, SettingRow, Switch, make_button

        group = SettingGroup(parent)

        self._telemetry_switch = Switch(group, is_telemetry_enabled())
        self._telemetry_switch.setToolTip(
            tr("Errors, versions and features used, linked to your account. "
               "Never your imagery, layers or coordinates. On Pro, counts only.")
        )
        self._telemetry_switch.setAccessibleName(tr("Share usage statistics"))
        self._telemetry_switch.toggled.connect(self._on_telemetry_toggled)
        stats_row = SettingRow(tr("Usage statistics"), tr("Linked to your account, no imagery"),
                               self._telemetry_switch, group)
        stats_row.setToolTip(self._telemetry_switch.toolTip())
        group.add_row(stats_row)

        self._guidance_btn = make_button(tr("Show again"), BTN_GHOST_QSS, group)
        self._guidance_btn.setToolTip(tr("Bring back the tips you closed"))
        self._guidance_btn.clicked.connect(self._on_reset_guidance)
        group.add_row(SettingRow(
            tr("Tips"),
            tr("Hints you closed in the panel"),
            self._guidance_btn, group))

        field = QWidget(group)
        field_row = QHBoxLayout(field)
        field_row.setContentsMargins(0, 0, 0, 0)
        field_row.setSpacing(8)
        from qgis.core import QgsSettings

        self._output_dir_edit = QLineEdit(field)
        self._output_dir_edit.setStyleSheet(INPUT_QSS)
        current = QgsSettings().value("AIEdit/output_dir", "", type=str)
        self._output_dir_edit.setText(QDir.toNativeSeparators(current or ""))
        # The folder an empty field stands for (next to a saved project, else
        # Documents), not "Default (auto)", which did not say where files go.
        self._output_dir_edit.setPlaceholderText(QDir.toNativeSeparators(
            _documents_default_dir() if current else get_output_dir()))
        # The real folder, not "~/Documents": that means nothing on Windows,
        # where Documents may also sit in OneDrive or carry a local name.
        self._output_dir_edit.setToolTip(
            tr(
                "Where AI Edit writes its generated GeoTIFFs. "
                "Leave empty to use {folder} (or the saved project folder)."
            ).format(folder=QDir.toNativeSeparators(_documents_default_dir()))
        )
        self._output_dir_edit.editingFinished.connect(self._on_output_dir_edited)
        # Show the start of a long path, not its tail.
        self._output_dir_edit.setCursorPosition(0)
        field_row.addWidget(self._output_dir_edit, 1)
        browse_btn = make_button(tr("Browse..."), BTN_GHOST_QSS, field)
        browse_btn.clicked.connect(self._on_output_dir_browse)
        field_row.addWidget(browse_btn)
        group.add_row(SettingRow(
            tr("Output folder"),
            tr("Leave empty for the default folder"),
            field, group, control_below=True))
        return group

    def _on_telemetry_toggled(self, enabled: bool) -> None:
        from ...core.telemetry import set_telemetry_enabled

        set_telemetry_enabled(enabled)

    def _on_reset_guidance(self) -> None:
        reset_hints()
        self._guidance_btn.setText(tr("Restored"))
        self._guidance_btn.setEnabled(False)

    def _on_output_dir_edited(self) -> None:
        self._store_output_dir(self._output_dir_edit.text())

    def _store_output_dir(self, text: str) -> None:
        """Save the folder, then show what was kept (quotes from "Copy as
        path" stripped, a relative path dropped) with the OS separators."""
        from qgis.core import QgsSettings

        set_output_dir(text.strip())
        stored = QgsSettings().value("AIEdit/output_dir", "", type=str) or ""
        shown = QDir.toNativeSeparators(stored)
        if self._output_dir_edit.text() != shown:
            self._output_dir_edit.blockSignals(True)
            self._output_dir_edit.setText(shown)
            self._output_dir_edit.setCursorPosition(0)
            self._output_dir_edit.blockSignals(False)

    def _on_output_dir_browse(self) -> None:
        current = self._output_dir_edit.text().strip() or get_output_dir()
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Choose output folder"), current
        )
        if chosen:
            self._store_output_dir(chosen)
