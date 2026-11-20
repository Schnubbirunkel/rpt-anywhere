"""Reusable widgets: flow layout, game cards, friend rows, toasts."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSize,
                            Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import style as S
from .images import letter_avatar, placeholder_cover, rounded
from ..i18n import N_, _


# --------------------------------------------------------------------------- #
class FlowLayout(QLayout):
    """Wrapping grid that stretches items evenly across the row."""

    def __init__(self, parent=None, spacing=18, item_w=250):
        super().__init__(parent)
        self.items = []
        self._spacing = spacing
        self.item_w = item_w

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        return QSize(self.item_w, 100)

    def _do(self, rect: QRect, test: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        visible = [it for it in self.items if not it.widget() or not it.widget().isHidden()]
        sp = self._spacing
        cols = max(1, (r.width() + sp) // (self.item_w + sp))
        w = (r.width() - sp * (cols - 1)) // cols
        y, row_h = r.y(), 0
        for i, it in enumerate(visible):
            c = i % cols
            if c == 0 and i:
                y += row_h + sp
                row_h = 0
            h = it.widget().heightForWidth(w) if it.widget() and it.widget().hasHeightForWidth() \
                else it.sizeHint().height()
            if not test:
                it.setGeometry(QRect(r.x() + c * (w + sp), y, w, h))
            row_h = max(row_h, h)
        return y + row_h - rect.y() + m.bottom()


# --------------------------------------------------------------------------- #
class Badge(QLabel):
    def __init__(self, text: str, fg: str, bg: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("Badge")
        self.setStyleSheet(f"color:{fg}; background:{bg};")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


class Dot(QWidget):
    def __init__(self, color: str = S.MUTED, size: int = 10, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(size, size)

    def set_color(self, c: str):
        self.color = QColor(c)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(self.rect())


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    return f


def label(text: str, obj: str = "", wrap: bool = False) -> QLabel:
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    l.setWordWrap(wrap)
    return l


# --------------------------------------------------------------------------- #
RUNNER_BADGE = {
    "steam": (N_("STEAM"), "#cfe3ff", "#1d3557"),
    "native": (N_("LINUX"), "#d9f99d", "#26361a"),
    "proton": (N_("PROTON"), "#fde68a", "#3b2f12"),
    "wine": (N_("WINE"), "#fecaca", "#3b1717"),
    "command": (N_("BEFEHL"), "#e9d5ff", "#2e1a47"),
}


class GameCard(QWidget):
    play = Signal(str)
    menu = Signal(str, QPoint)

    RATIO = 215 / 460

    def __init__(self, game_id: str, name: str, runner: str, subtitle: str = "",
                 rpt_native: bool | None = None, favorite: bool = False, parent=None):
        super().__init__(parent)
        self.game_id, self.name, self.runner = game_id, name, runner
        self.subtitle = subtitle
        self.rpt_native = rpt_native
        self.favorite = favorite
        self.cover: QPixmap | None = None
        self._placeholder = placeholder_cover(name)
        self.hover = 0.0
        self.running = False
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self._timer = QTimer(self, interval=16, timeout=self._tick)
        self._target = 0.0
        self.setToolTip(name + (f"\n{subtitle}" if subtitle else ""))

    # sizing
    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int(w * self.RATIO) + 64

    def sizeHint(self):
        return QSize(250, self.heightForWidth(250))

    def set_cover(self, pm: QPixmap):
        self.cover = pm
        self.update()

    def set_running(self, r: bool):
        self.running = r
        self.update()

    # hover animation
    def enterEvent(self, e):
        self._target = 1.0
        self._timer.start()

    def leaveEvent(self, e):
        self._target = 0.0
        self._timer.start()

    def _tick(self):
        d = self._target - self.hover
        if abs(d) < 0.02:
            self.hover = self._target
            self._timer.stop()
        else:
            self.hover += d * 0.25
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.play.emit(self.game_id)
        elif e.button() == Qt.RightButton:
            self.menu.emit(self.game_id, e.globalPosition().toPoint())

    def contextMenuEvent(self, e):
        e.accept()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w = self.width()
        lift = 3 * self.hover
        img_h = int(w * self.RATIO)
        card = QRectF(0.5, 0.5 + 3 - lift, w - 1, self.height() - 4)

        # card background
        path = QPainterPath()
        path.addRoundedRect(card, 14, 14)
        p.fillPath(path, QColor(S.SURFACE))
        border = QColor(S.ACCENT) if self.running else QColor(S.BORDER)
        if self.hover > 0 and not self.running:
            border = QColor(S.BORDER).lighter(100 + int(60 * self.hover))
        p.setPen(QPen(border, 1.5 if self.running else 1))
        p.drawPath(path)

        # cover
        src = self.cover or self._placeholder
        pm = rounded(src, int(card.width()) - 2, img_h, 13, top_only=True)
        p.drawPixmap(int(card.x()) + 1, int(card.y()) + 1, pm)

        # hover veil + play button
        if self.hover > 0.01:
            veil = QColor(0, 0, 0, int(110 * self.hover))
            vp = QPainterPath()
            vp.addRoundedRect(QRectF(card.x() + 1, card.y() + 1, card.width() - 2, img_h), 13, 13)
            p.fillPath(vp, veil)
            r = 26 + 4 * self.hover
            cx, cy = card.center().x(), card.y() + img_h / 2
            col = QColor(S.ACCENT)
            col.setAlphaF(min(1.0, self.hover * 1.2))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
            tri = QPainterPath()
            s = r * 0.42
            tri.moveTo(cx - s * 0.7, cy - s)
            tri.lineTo(cx + s * 1.05, cy)
            tri.lineTo(cx - s * 0.7, cy + s)
            tri.closeSubpath()
            p.fillPath(tri, QColor("#1a0f08"))

        # badges on cover
        bx = card.x() + 10
        by = card.y() + 10
        f = QFont()
        f.setPixelSize(10)
        f.setWeight(QFont.ExtraBold)
        p.setFont(f)
        t, fg, bg = RUNNER_BADGE.get(self.runner, ("?", S.TEXT, S.SURFACE_3))
        badges = [(_(t), fg, bg)]
        if self.running:
            badges.insert(0, (_('LÄUFT'), "#1a0f08", S.ACCENT))
        for text, fg, bg in badges:
            tw = p.fontMetrics().horizontalAdvance(text) + 14
            br = QRectF(bx, by, tw, 18)
            bp = QPainterPath()
            bp.addRoundedRect(br, 6, 6)
            bgc = QColor(bg)
            bgc.setAlpha(235)
            p.fillPath(bp, bgc)
            p.setPen(QColor(fg))
            p.drawText(br, Qt.AlignCenter, text)
            bx += tw + 6
        if self.favorite:
            p.setPen(QColor(S.ACCENT_2))
            f2 = QFont()
            f2.setPixelSize(16)
            p.setFont(f2)
            p.drawText(QRectF(card.right() - 30, card.y() + 6, 24, 24), Qt.AlignCenter, "★")

        # text
        tx = card.x() + 14
        f = QFont()
        f.setPixelSize(14)
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.setPen(QColor(S.TEXT))
        name = p.fontMetrics().elidedText(self.name, Qt.ElideRight, int(card.width() - 28))
        p.drawText(QRectF(tx, card.y() + img_h + 10, card.width() - 28, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, name)
        f.setPixelSize(11)
        f.setWeight(QFont.Medium)
        p.setFont(f)
        p.setPen(QColor(S.MUTED))
        sub = self.subtitle
        if self.rpt_native:
            sub = _('Steam · RPT nativ ✓') if self.runner == "steam" else _('RPT nativ ✓')
        sub = p.fontMetrics().elidedText(sub, Qt.ElideRight, int(card.width() - 28))
        p.drawText(QRectF(tx, card.y() + img_h + 30, card.width() - 28, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, sub)
        p.end()


# --------------------------------------------------------------------------- #
class FriendRow(QWidget):
    invite = Signal(object)

    def __init__(self, steamid: int, name: str, status: str, online: bool, in_game: bool,
                 parent=None):
        super().__init__(parent)
        self.steamid = steamid
        self.name = name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)
        self.avatar = QLabel()
        self.avatar.setFixedSize(36, 36)
        self.avatar.setPixmap(letter_avatar(name, 36))
        lay.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(0)
        n = QLabel(name)
        n.setStyleSheet("font-weight:700;" + ("" if online else f"color:{S.MUTED};"))
        st_color = S.OK if in_game else (S.INFO if online else S.MUTED)
        st = QLabel(status)
        st.setStyleSheet(f"color:{st_color}; font-size:11px;")
        col.addWidget(n)
        col.addWidget(st)
        lay.addLayout(col, 1)
        self.btn = QPushButton(_('Einladen'))
        self.btn.setObjectName("Invite")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(lambda: self.invite.emit(self.steamid))
        lay.addWidget(self.btn)
        self.setOpacityDim(not online)

    def setOpacityDim(self, dim: bool):
        if dim:
            eff = QGraphicsOpacityEffect(self.avatar)
            eff.setOpacity(0.45)
            self.avatar.setGraphicsEffect(eff)

    def set_avatar(self, pm: QPixmap):
        self.avatar.setPixmap(pm)

    def mark_invited(self):
        self.btn.setText(_('Gesendet ✓'))
        self.btn.setEnabled(False)
        QTimer.singleShot(8000, lambda: (self.btn.setText(_('Erneut')), self.btn.setEnabled(True)))


# --------------------------------------------------------------------------- #
class Toast(QLabel):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setWordWrap(True)
        self.setMaximumWidth(520)
        self.hide()
        sh = QGraphicsDropShadowEffect(self, blurRadius=30, offset=QPoint(0, 8),
                                       color=QColor(0, 0, 0, 160))
        self.setGraphicsEffect(sh)
        self._t = QTimer(self, singleShot=True, timeout=self.hide)

    def show_msg(self, text: str, kind: str = "info", ms: int = 3800):
        col = {"ok": S.OK, "err": S.ERR, "warn": S.WARN}.get(kind, S.ACCENT)
        self.setStyleSheet(f"#Toast {{ border-left: 4px solid {col}; }}")
        self.setText(text)
        fm = self.fontMetrics()
        self.setFixedWidth(min(520, fm.horizontalAdvance(text) + 48))
        self.adjustSize()
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._t.start(ms)
