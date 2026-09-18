
















from __future__ import annotations

import os

from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QPainter, QPen, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ...core import qt_compat as QtC
from ...core.auth.activation_manager import (
    get_dashboard_url,
    get_privacy_url,
    get_server_url,
    get_terms_url,
)
from ...core.i18n import tr
from ..external_url import open_external
from ..panel_helpers import is_dark_palette, make_hidpi_pixmap

DIALOG_W = 500
SIDE_PAD = 24
MARK_PX = 52
BADGE_PX = 34
GLYPH_PX = 19




ROW_TEXT_W = DIALOG_W - 2 * SIDE_PAD - BADGE_PX - 14
FOOTER_TEXT_W = 320

_MUTED = "rgba(128, 128, 128, 0.95)"
_FINE = "rgba(128, 128, 128, 0.85)"
_RULE = "rgba(128, 128, 128, 0.28)"


def _accent_ink(widget) -> str:

    from ..dock.style import BRAND_GREEN, BRAND_GREEN_TEXT

    return BRAND_GREEN if is_dark_palette(widget) else BRAND_GREEN_TEXT


def _load_mark(widget, size: int) -> QPixmap | None:






    from ..dock.style import ICONS_DIR

    pixmap = QPixmap(os.path.join(ICONS_DIR, "icon.png"))
    if pixmap.isNull():
        return None
    ratio = widget.devicePixelRatioF() or 1.0
    scaled = pixmap.scaled(
        int(size * ratio),
        int(size * ratio),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(ratio)
    return scaled


def _glyph(kind: str, color: str) -> QPixmap:




    pixmap = make_hidpi_pixmap(GLYPH_PX)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    n = GLYPH_PX

    if kind == "place":

        painter.drawEllipse(QRectF(2.2, 2.2, n - 4.4, n - 4.4))
        painter.drawLine(int(2.2), int(n / 2), int(n - 2.2), int(n / 2))
        painter.drawEllipse(QRectF(n / 2 - 3.4, 2.2, 6.8, n - 4.4))
    elif kind == "clock":
        painter.drawEllipse(QRectF(2.2, 2.2, n - 4.4, n - 4.4))
        painter.drawLine(int(n / 2), int(n / 2), int(n / 2), int(n / 2 - 4))
        painter.drawLine(int(n / 2), int(n / 2), int(n / 2 + 3), int(n / 2 + 2))
    else:

        painter.drawLine(int(2.5), int(n / 2 - 3.5), int(n - 2.5), int(n / 2 - 3.5))
        painter.drawLine(int(2.5), int(n / 2 + 3.5), int(n - 2.5), int(n / 2 + 3.5))
        painter.setBrush(QColor(color))
        painter.drawEllipse(QRectF(n - 8.6, n / 2 - 6.1, 5.2, 5.2))
        painter.drawEllipse(QRectF(3.4, n / 2 + 0.9, 5.2, 5.2))
    painter.end()
    return pixmap


class PrivacyNoticeDialog(QDialog):


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("privacyNoticeDialog")
        self.setWindowTitle(tr("Before you start"))
        self.setModal(True)



        self.setFixedWidth(DIALOG_W)
        self._setup_ui()

    def _setup_ui(self):


        from ..dock.style import _BTN_GREEN

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._build_header())

        body = QVBoxLayout()
        body.setContentsMargins(SIDE_PAD, 4, SIDE_PAD, 16)
        body.setSpacing(14)
        for kind, lead, rest in self._rows():
            body.addWidget(self._build_row(kind, lead, rest))
        layout.addLayout(body)

        rule = QFrame(self)
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"border: none; background: {_RULE};")
        layout.addWidget(rule)

        footer = QHBoxLayout()
        footer.setContentsMargins(SIDE_PAD, 13, SIDE_PAD, 15)
        footer.setSpacing(14)
        self._fill_footer(footer, _BTN_GREEN)
        layout.addLayout(footer)



    def _build_header(self) -> QHBoxLayout:

        header = QHBoxLayout()
        header.setContentsMargins(SIDE_PAD, 22, SIDE_PAD, 6)
        header.setSpacing(14)

        mark = QLabel(self)
        mark.setObjectName("privacyNoticeMark")
        mark.setFixedSize(MARK_PX, MARK_PX)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = _load_mark(self, MARK_PX)
        if pixmap is not None:
            mark.setPixmap(pixmap)
        header.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)

        heading = QLabel(tr("AI Edit and your data"), self)
        heading.setWordWrap(True)
        heading.setStyleSheet(
            "font-size: 17px; font-weight: 600; color: palette(text);")
        header.addWidget(heading, 1, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _rows(self):

        return (
            (
                "place",
                tr("Generated in the USA."),
                tr("The map area you select and your prompt go to our image "
                   "generation partner only to produce the result, and it "
                   "deletes them within 30 days."),
            ),
            (
                "clock",
                tr("Kept in France."),



                tr("Your generations stay in your history until you delete "
                   "them. On {pro} you can set 30 days, 90 days or 1 year in "
                   "Settings."),
            ),
            (
                "sliders",
                tr("Usage stats are on."),
                tr("They help us fix bugs. You can switch them off in "
                   "Settings whenever you want."),
            ),
        )

    def _build_row(self, kind: str, lead: str, rest: str) -> QFrame:
        row = QFrame(self)
        row.setObjectName("privacyNoticeRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(14)

        accent = _accent_ink(self)
        badge = QLabel(row)
        badge.setFixedSize(BADGE_PX, BADGE_PX)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            "background: rgba(139, 172, 39, 0.18);"
            f"border-radius: {BADGE_PX // 2}px;"
        )
        badge.setPixmap(_glyph(kind, accent))
        line.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)



        text = QLabel(row)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)


        pro_url = get_server_url("upgrade_url", get_dashboard_url())
        pro_link = f'<a style="color:{accent};" href="{pro_url}">{tr("Pro")}</a>'
        text.setText(
            f'<span style="font-weight:600;color:palette(text);">{lead}</span> '
            f'<span style="color:{_MUTED};">{rest.replace("{pro}", pro_link)}</span>'
        )
        text.setStyleSheet("font-size: 13px;")
        text.setFixedWidth(ROW_TEXT_W)
        text.setTextInteractionFlags(QtC.LinksAccessibleByMouse)
        text.setOpenExternalLinks(False)
        text.linkActivated.connect(open_external)
        line.addWidget(text, 1)
        return row

    def _fill_footer(self, footer: QHBoxLayout, button_qss: str) -> None:



        terms_link = f'<a href="{get_terms_url()}">{tr("Terms")}</a>'
        privacy_link = f'<a href="{get_privacy_url()}">{tr("Privacy Policy")}</a>'




        body = tr("Continuing accepts the {terms} and the {privacy}.")
        for token, value in (("{terms}", terms_link), ("{privacy}", privacy_link)):
            body = body.replace(token, value)
        accept_line = QLabel(body, self)
        accept_line.setObjectName("privacyNoticeAcceptLine")
        accept_line.setWordWrap(True)
        accept_line.setTextFormat(Qt.TextFormat.RichText)
        accept_line.setStyleSheet(f"font-size: 11px; color: {_FINE};")
        accept_line.setFixedWidth(FOOTER_TEXT_W)


        accept_line.setTextInteractionFlags(QtC.LinksAccessibleByMouse)


        accept_line.setOpenExternalLinks(False)
        accept_line.linkActivated.connect(open_external)
        footer.addWidget(accept_line, 1)

        self._continue_btn = QPushButton(tr("Continue"), self)
        self._continue_btn.setObjectName("privacyNoticeContinue")
        self._continue_btn.setStyleSheet(button_qss)
        self._continue_btn.setCursor(QtC.PointingHandCursor)
        self._continue_btn.setDefault(True)
        self._continue_btn.clicked.connect(self.accept)
        footer.addWidget(self._continue_btn, 0, Qt.AlignmentFlag.AlignVCenter)
