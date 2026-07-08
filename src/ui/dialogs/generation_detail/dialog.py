
from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QDialog, QPushButton

from ....core import telemetry
from ....core import telemetry_events as te
from ....core.config_store import get_export_copy, get_export_dial_ratio
from ....core.i18n import tr
from ....core.prompts import prompt_history
from ...keyboard_focus import settle_dialog_default_button
from ...panel_helpers import screen_for_dialog
from .build import _DIALOG_MARGIN_PX, _PANE_GAP_PX, BuildUiMixin
from .images import ImageLoadMixin
from .styles import _DETAIL_DIALOG_QSS, _PRIMARY_BTN, _STAR_FILLED_SVG, _STAR_OUTLINE_SVG
from .widgets import _AspectBox




_SCREEN_WIDTH_RATIO = 0.96
_SCREEN_HEIGHT_RATIO = 0.92


class GenerationDetailDialog(BuildUiMixin, ImageLoadMixin, QDialog):




    favorite_toggled = pyqtSignal(str, bool)


    prompt_favorite_toggled = pyqtSignal(str, bool, str, str)

    def __init__(
        self,
        parent=None,
        *,
        job: dict | None = None,
        preset: dict | None = None,
        client=None,
        demo_loader=None,
        absolute_url=None,
        on_action=None,
        on_favorite=None,
        browse_only: bool = False,
        session_entry: dict | None = None,
    ):




        super().__init__(parent)
        self.setObjectName("generationDetail")
        self.setStyleSheet(_DETAIL_DIALOG_QSS)
        self._job = job
        self._preset = preset
        self._session = session_entry
        self._is_generation = job is not None
        self._client = client
        self._demo_loader = demo_loader
        self._absolute_url = absolute_url
        self._on_action = on_action
        self._on_favorite = on_favorite
        self._browse_only = browse_only

        self._outcome: str | None = None
        self._fullscreen = False
        self._is_favorite = bool((job or {}).get("is_favorite"))

        src = job or preset or {}
        self._title_text = self._resolve_title(src)
        self.setWindowTitle(
            self._title_text or get_export_copy("dialogs.dialog.details_title", tr("Details")))
        self.setMinimumSize(560, 420)
        self.setSizeGripEnabled(True)





        self._aspect_locked = False
        self._aspect = self._compute_aspect()
        self._aspect_box: _AspectBox | None = None





        if self._is_generation:
            self._thumb_key = str(job.get("request_id") or "")
            self._full_key = self._thumb_key + "_full"
        else:
            self._thumb_key = str((preset or {}).get("id") or "")
            self._full_key = self._thumb_key + "_preview"
        self._full_done: set[str] = set()

        self._loader_hooked = False
        self._has_images = self._build_ui()



        primary = next(
            (
                b for b in self.findChildren(QPushButton)
                if b.styleSheet() == _PRIMARY_BTN and b.isEnabled()
            ),
            None,
        )
        settle_dialog_default_button(self, primary)
        if self._has_images:
            self._apply_image_size()
        else:
            self._apply_text_only_size()
        self._start_image_loads()


        self.finished.connect(self._cleanup_loader)

    def _compute_aspect(self) -> float:
        if self._is_generation:
            w, h = self._job.get("output_w"), self._job.get("output_h")
            if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
                self._aspect_locked = True
                return w / h
            ar = str(self._job.get("aspect_ratio") or "")
            if ":" in ar:
                try:
                    a, b = ar.split(":")
                    fa, fb = float(a), float(b)
                    if fa > 0 and fb > 0:
                        self._aspect_locked = True
                        return fa / fb
                except ValueError:
                    pass
        return 1.0

    def _apply_text_only_size(self) -> None:




        height = 560
        screen = screen_for_dialog(self.parentWidget())
        if screen is not None:
            height = min(height, int(screen.availableGeometry().height() * get_export_dial_ratio(
                "dialogs.dialog.screen_height_ratio", _SCREEN_HEIGHT_RATIO)))
        self.resize(520, height)

    def _apply_image_size(self) -> None:


        ar = self._aspect if self._aspect > 0 else 1.0


        info_w = 380
        disp_h = 600.0
        disp_w = disp_h * ar
        max_w = 900.0
        if disp_w > max_w:
            disp_w = max_w
            disp_h = disp_w / ar
        if disp_h < 380.0:
            disp_h = 380.0
            disp_w = min(disp_h * ar, max_w)



        width = int(disp_w) + info_w + _PANE_GAP_PX + 2 * _DIALOG_MARGIN_PX
        height = int(disp_h) + 2 * _DIALOG_MARGIN_PX







        screen = screen_for_dialog(self.parentWidget())
        if screen is not None:
            avail = screen.availableGeometry()
            width = min(width, int(avail.width() * get_export_dial_ratio(
                "dialogs.dialog.screen_width_ratio", _SCREEN_WIDTH_RATIO)))
            height = min(height, int(avail.height() * get_export_dial_ratio(
                "dialogs.dialog.screen_height_ratio", _SCREEN_HEIGHT_RATIO)))
        self.resize(max(width, 560), max(height, 420))



    def outcome(self) -> str | None:




        return self._outcome

    def payload(self) -> dict | None:
        return self._job if self._is_generation else self._preset



    def _on_use(self) -> None:
        self._outcome = "use"
        self.accept()



    def _on_resume_session(self) -> None:
        self._outcome = "resume"
        self.accept()

    def _on_rename_session(self) -> None:
        self._outcome = "rename"
        self.accept()

    def _on_delete_session(self) -> None:
        self._outcome = "delete"
        self.accept()

    def _on_download(self, side: str) -> None:
        if self._on_action and self._job:
            self._on_action(f"download_{side}", self._job)

    def _refresh_star(self) -> None:
        icon = _STAR_FILLED_SVG if self._is_favorite else _STAR_OUTLINE_SVG
        self._star_btn.setIcon(QIcon(icon))
        self._star_btn.setToolTip(
            get_export_copy("dialogs.dialog.favorite_remove_tooltip", tr("Remove from favorites"))
            if self._is_favorite
            else get_export_copy("dialogs.dialog.favorite_add_tooltip", tr("Add to favorites"))
        )
        self._star_btn.setAccessibleName(self._star_btn.toolTip())

    def _on_star(self) -> None:
        self._is_favorite = not self._is_favorite
        self._job["is_favorite"] = self._is_favorite
        self._refresh_star()
        rid = str(self._job.get("request_id") or "")
        if self._on_favorite and rid:
            self._on_favorite(rid, self._is_favorite)
        self.favorite_toggled.emit(rid, self._is_favorite)

    def _refresh_prompt_star(self) -> None:
        icon = _STAR_FILLED_SVG if self._prompt_is_favorite else _STAR_OUTLINE_SVG
        self._prompt_star_btn.setIcon(QIcon(icon))
        self._prompt_star_btn.setToolTip(
            get_export_copy("dialogs.dialog.favorite_remove_tooltip", tr("Remove from favorites"))
            if self._prompt_is_favorite
            else get_export_copy("dialogs.dialog.favorite_add_tooltip", tr("Add to favorites"))
        )
        self._prompt_star_btn.setAccessibleName(self._prompt_star_btn.toolTip())

    def _on_prompt_star(self) -> None:
        src = self._preset or {}
        prompt = str(src.get("prompt") or "")
        label = src.get("label")
        source_cat = src.get("source_category")


        self._prompt_is_favorite = prompt_history.toggle_favorite(
            prompt, label, source_cat
        )
        telemetry.track(te.FAVORITE_TOGGLED, {
            "now_favorited": self._prompt_is_favorite,
            "source": "detail_dialog",
        })
        telemetry.flush()
        self._refresh_prompt_star()
        self.prompt_favorite_toggled.emit(
            prompt, self._prompt_is_favorite, label or "", source_cat or ""
        )



    def _toggle_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        self._info_panel.setVisible(not self._fullscreen)
        if self._fs_btn is not None:


            self._fs_btn.blockSignals(True)
            self._fs_btn.setChecked(self._fullscreen)
            self._fs_btn.blockSignals(False)
            self._fs_btn.setToolTip(
                get_export_copy("dialogs.dialog.leave_fullscreen_tooltip", tr("Leave fullscreen"))
                if self._fullscreen
                else get_export_copy("dialogs.dialog.fullscreen_tooltip", tr("Fullscreen"))
            )




        if self._fullscreen:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _cleanup_loader(self) -> None:
        if self._demo_loader is None:
            return
        for slot in (self._on_image_loaded, self._on_ref_loaded):
            try:
                self._demo_loader.loaded.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        try:
            self._demo_loader.failed.disconnect(self._on_image_failed)
        except (RuntimeError, TypeError):
            pass

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self._fullscreen:
            self._toggle_fullscreen()
            return
        super().keyPressEvent(event)
