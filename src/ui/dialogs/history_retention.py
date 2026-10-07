





from __future__ import annotations

from qgis.PyQt.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from ...core.i18n import tr
from ...workers.generic_request_task import GenericRequestTask

_CHOICES = (0, 30, 90, 180, 365, None)


def _label(days) -> str:
    if days is None:
        return tr("Until I delete it")
    if days == 0:
        return tr("No copies (0 days)")
    if days == 30:
        return tr("30 days")
    if days == 90:
        return tr("90 days")
    if days == 180:
        return tr("6 months")
    if days == 365:
        return tr("1 year")
    if days == 1:
        return tr("1 day")
    return tr("{n} days").format(n=days)


def _rank(days) -> float:

    return float("inf") if days in (None, 0) else float(days)


class HistoryRetentionRow:


    def __init__(self, dialog, group: QWidget):
        from .settings.widgets import SettingRow

        self._dialog = dialog
        self._task = None
        self._current = None
        self._box = QWidget(group)
        self._layout = QHBoxLayout(self._box)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._combo = None
        self.row = SettingRow(tr("History retention"), "", self._box, group)
        self.row.setVisible(False)
        try:
            dialog.finished.connect(self._cancel)
        except (AttributeError, TypeError):
            pass
        if getattr(dialog, "_signed_in", False):
            self._start(lambda c, a: c.get_data_retention(auth=a), self._paint, self._hide)



    def _start(self, call, on_ok, on_fail) -> None:
        from qgis.core import QgsApplication

        self._cancel()
        client, auth = self._dialog._client, self._dialog._auth
        self._task = GenericRequestTask(
            "AI Edit history retention", lambda: call(client, auth), silent=True)
        self._task.succeeded.connect(on_ok)
        self._task.failed.connect(on_fail)
        QgsApplication.taskManager().addTask(self._task)

    def _cancel(self, *_args) -> None:
        if self._task is None:
            return
        try:
            self._task.succeeded.disconnect()
            self._task.failed.disconnect()
        except (RuntimeError, TypeError):  # nosec B110
            pass
        try:
            self._task.cancel()
        except Exception:  # nosec B110
            pass
        self._task = None



    def _hide(self, *_args) -> None:
        self._task = None
        self.row.setVisible(False)

    def _clear(self) -> None:
        self._combo = None
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _paint(self, data) -> None:
        self._task = None
        if not isinstance(data, dict) or data.get("tier") not in ("free", "pro", "zero"):
            self._hide()
            return
        from ..dock.design_tokens import BTN_GHOST_QSS
        from .settings.widgets import make_button

        self._clear()
        tier = data["tier"]
        days = data.get("history_retention_days")
        self._current = days
        if tier == "pro":
            combo = QComboBox(self._box)
            for value in _CHOICES:
                combo.addItem(_label(value), value)
            if days not in _CHOICES:

                combo.addItem(_label(days), days)
                item = combo.model().item(combo.count() - 1)
                if item is not None:
                    item.setEnabled(False)
            combo.setCurrentIndex(combo.findData(days) if days is not None else len(_CHOICES) - 1)
            combo.setAccessibleName(tr("History retention"))
            combo.currentIndexChanged.connect(self._on_combo_changed)
            self._combo = combo
            self._layout.addWidget(combo)
            self.row.set_note(self._pro_note(days))
        elif tier == "zero":
            self._layout.addWidget(QLabel(tr("Set by your contract"), self._box))
            self.row.set_note(tr("Zero data retention: no copy of your work is kept "
                                 "beyond what your contract allows."))
        elif days is None:
            self._layout.addWidget(QLabel(tr("Until you delete it"), self._box))
            self.row.set_note(tr("Free plan. Pro lets you choose 30 days to 1 year."))
        else:
            self._layout.addWidget(QLabel(_label(days), self._box))
            keep = make_button(tr("Keep until I delete it"), BTN_GHOST_QSS, self._box)
            keep.setEnabled(bool(data.get("can_change", True)))
            keep.clicked.connect(self._on_keep_clicked)
            self._layout.addWidget(keep)
            self.row.set_note("")
        self.row.setVisible(True)

    @staticmethod
    def _pro_note(days=None) -> str:
        if days == 0:
            return tr("No copy of your work is written. Your AI Edit generations "
                      "and saved outlines stay until you delete them.")
        return tr("How long your history stays on our servers, for all TerraLab "
                  "plugins. Seen by you only, never used to improve anything.")



    def _select(self, days) -> None:
        if self._combo is None:
            return
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(self._combo.findData(days) if days is not None
                                    else self._combo.findText(_label(None)))
        self._combo.blockSignals(False)

    def _on_combo_changed(self, _index: int) -> None:
        new = self._combo.currentData()
        if new == self._current:
            return
        if new not in _CHOICES:
            self._select(self._current)
            return
        if _rank(new) < _rank(self._current):
            from .confirm_dialog import question

            if not question(self._dialog, tr("History retention"),
                            tr("Older history will be deleted for good, files "
                               "included, within two days. Continue?"),
                            default_yes=False, destructive=True,
                            yes_label=tr("Continue")):
                self._select(self._current)
                return
        self._combo.setEnabled(False)
        self.row.set_note(self._pro_note(new))
        self._start(lambda c, a: c.set_data_retention(auth=a, days=new),
                    self._on_saved, self._on_save_failed)

    def _on_keep_clicked(self) -> None:
        for i in range(self._layout.count()):
            widget = self._layout.itemAt(i).widget()
            if widget is not None:
                widget.setEnabled(False)
        self._start(lambda c, a: c.set_data_retention(auth=a, days=None),
                    self._on_saved, self._on_save_failed)

    def _on_saved(self, data) -> None:
        self._paint(data)

    def _on_save_failed(self, message: str, *_rest) -> None:
        self._task = None
        if self._combo is not None:
            self._select(self._current)
            self._combo.setEnabled(True)
            self.row.set_note(self._pro_note(self._current))
        else:
            for i in range(self._layout.count()):
                widget = self._layout.itemAt(i).widget()
                if widget is not None:
                    widget.setEnabled(True)
        self.row.set_note(message or tr("Could not save. Try again."))
