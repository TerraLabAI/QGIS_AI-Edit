"""The GenerationDetailDialog class: state, sizing, actions, fullscreen."""
from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QDialog

from ....core import telemetry
from ....core import telemetry_events as te
from ....core.i18n import tr
from ....core.prompts import prompt_history
from .build import BuildUiMixin
from .images import ImageLoadMixin
from .styles import _STAR_FILLED_SVG, _STAR_OUTLINE_SVG
from .widgets import _AspectBox


class GenerationDetailDialog(BuildUiMixin, ImageLoadMixin, QDialog):
    """Larger detail view for a template or a past generation."""

    # request_id, now_favorited - relayed from the inline star so the library
    # keeps its Favorites list and the server in sync.
    favorite_toggled = pyqtSignal(str, bool)
    # prompt, now_favorited, label, source_category - emitted when a template's
    # favorite is toggled from this popup (favoriting moved out of the grid).
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
    ):
        super().__init__(parent)
        self._job = job
        self._preset = preset
        self._is_generation = job is not None
        self._client = client
        self._demo_loader = demo_loader
        self._absolute_url = absolute_url
        self._on_action = on_action
        self._on_favorite = on_favorite
        self._browse_only = browse_only
        self._outcome: str | None = None  # None | "use" | "close"
        self._fullscreen = False
        self._is_favorite = bool((job or {}).get("is_favorite"))

        src = job or preset or {}
        self._title_text = self._resolve_title(src)
        self.setWindowTitle(self._title_text or tr("Details"))
        self.setMinimumSize(560, 420)
        self.setSizeGripEnabled(True)

        # Image aspect ratio (w/h) drives the slider shape + the window size, so
        # a portrait generation opens portrait and a wide one opens wide. For a
        # template the dimensions are unknown until the demo loads, so we adopt
        # the loaded pixmap's ratio then (``_aspect_locked`` guards that).
        self._aspect_locked = False
        self._aspect = self._compute_aspect()
        self._aspect_box: _AspectBox | None = None

        # Image cache keys. The card grid caches the small thumbnail under the
        # request_id; the popup reuses that for an instant first paint, then
        # upgrades to the full image under a distinct "_full" key so the two
        # sizes never collide in the shared on-disk cache.
        if self._is_generation:
            self._thumb_key = str(job.get("request_id") or "")
            self._full_key = self._thumb_key + "_full"
        else:
            self._thumb_key = str((preset or {}).get("id") or "")
            self._full_key = self._thumb_key + "_preview"
        self._full_done: set[str] = set()

        self._loader_hooked = False
        self._has_images = self._build_ui()
        if self._has_images:
            self._apply_image_size()
        else:
            self.resize(520, 560)
        self._start_image_loads()
        # Drop the shared loader connections when the dialog closes so a late
        # network reply never paints into a destroyed dialog (Qt6 crash guard).
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

    def _apply_image_size(self) -> None:
        """Size the window so the slider area matches the image aspect (so the
        whole generation is visible without cropping or letterboxing)."""
        ar = self._aspect if self._aspect > 0 else 1.0
        # A touch wider than the pane's 330 minimum so a typical prompt opens
        # with breathing room; the user can still widen further (up to 560).
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
        # 12px spacing between panes + 12px margins on each side. The image now
        # spans the full pane height (the toolbar row moved onto the image).
        width = int(disp_w) + info_w + 12 + 24
        height = int(disp_h) + 24
        self.resize(width, height)

    # -- public --------------------------------------------------------------

    def outcome(self) -> str | None:
        """``"use"`` (apply the payload), ``"close"`` (just close the library),
        or None (do nothing). Read by the library dialog after exec()."""
        return self._outcome

    def payload(self) -> dict | None:
        return self._job if self._is_generation else self._preset

    # -- actions -------------------------------------------------------------

    def _on_use(self) -> None:
        self._outcome = "use"
        self.accept()

    def _on_download(self, side: str) -> None:
        if self._on_action and self._job:
            self._on_action(f"download_{side}", self._job)

    def _refresh_star(self) -> None:
        icon = _STAR_FILLED_SVG if self._is_favorite else _STAR_OUTLINE_SVG
        self._star_btn.setIcon(QIcon(icon))
        self._star_btn.setToolTip(
            tr("Remove from favorites") if self._is_favorite
            else tr("Add to favorites")
        )

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
            tr("Remove from favorites") if self._prompt_is_favorite
            else tr("Add to favorites")
        )

    def _on_prompt_star(self) -> None:
        src = self._preset or {}
        prompt = str(src.get("prompt") or "")
        label = src.get("label")
        source_cat = src.get("source_category")
        # Mutate + record here (mirrors the old inline star); the parent picks up
        # the signal to sync the favorite to the server.
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

    # -- fullscreen ----------------------------------------------------------

    def _toggle_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        self._info_panel.setVisible(not self._fullscreen)
        if self._fs_btn is not None:
            self._fs_btn.setText("⤡" if self._fullscreen else "⤢")
            self._fs_btn.setToolTip(
                tr("Exit fullscreen") if self._fullscreen else tr("Fullscreen")
            )
        # Maximize rather than true fullscreen: on macOS, showFullScreen() moves
        # the dialog to its own Space so the (modal) prompt library ends up
        # covering it. Maximized stays a normal window we can raise above the
        # library. raise_/activateWindow forces it to the front either way.
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

    def keyPressEvent(self, event):  # noqa: N802 - Qt signature
        if event.key() == Qt.Key.Key_Escape and self._fullscreen:
            self._toggle_fullscreen()
            return
        super().keyPressEvent(event)
