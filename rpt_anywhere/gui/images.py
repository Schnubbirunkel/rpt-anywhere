"""Asynchronous cover art loading with a disk cache + painted fallbacks."""
from __future__ import annotations

import hashlib
import ssl
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPixmap)

from ..config import COVER_DIR

COVER_W, COVER_H = 460, 215


class _Emitter(QObject):
    done = Signal(str, QImage)


class _Job(QRunnable):
    def __init__(self, key: str, path: str | None, url: str | None, emitter: _Emitter):
        super().__init__()
        self.key, self.path, self.url, self.emitter = key, path, url, emitter

    def run(self):
        img = QImage()
        if self.path and Path(self.path).exists():
            img.load(self.path)
        if img.isNull() and self.url:
            cache = COVER_DIR / (hashlib.sha1(self.url.encode()).hexdigest()[:16] + ".jpg")
            if not cache.exists():
                try:
                    COVER_DIR.mkdir(parents=True, exist_ok=True)
                    req = urllib.request.Request(self.url, headers={"User-Agent": "rpt-anywhere"})
                    with urllib.request.urlopen(req, timeout=8,
                                                context=ssl.create_default_context()) as r:
                        cache.write_bytes(r.read())
                except Exception:
                    pass
            if cache.exists():
                img.load(str(cache))
        self.emitter.done.emit(self.key, img)


class CoverLoader(QObject):
    loaded = Signal(str, QPixmap)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(6)
        self.cache: dict[str, QPixmap] = {}
        self.pending: set[str] = set()
        self._em = _Emitter()
        self._em.done.connect(self._on_done)

    def request(self, key: str, path: str | None = None, url: str | None = None) -> QPixmap | None:
        if key in self.cache:
            return self.cache[key]
        if key not in self.pending and (path or url):
            self.pending.add(key)
            self.pool.start(_Job(key, path, url, self._em))
        return None

    def _on_done(self, key: str, img: QImage):
        self.pending.discard(key)
        if img.isNull():
            return
        pm = QPixmap.fromImage(img)
        self.cache[key] = pm
        self.loaded.emit(key, pm)


_PALETTES = [
    ("#ff7a59", "#7c3aed"), ("#22d3ee", "#1e3a8a"), ("#f472b6", "#4c1d95"),
    ("#facc15", "#b91c1c"), ("#34d399", "#065f46"), ("#60a5fa", "#312e81"),
    ("#fb923c", "#831843"), ("#a3e635", "#14532d"),
]


def placeholder_cover(name: str, w: int = COVER_W, h: int = COVER_H) -> QPixmap:
    idx = int(hashlib.md5(name.encode()).hexdigest(), 16) % len(_PALETTES)
    c1, c2 = _PALETTES[idx]
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor(c1))
    g.setColorAt(1, QColor(c2))
    p.fillRect(0, 0, w, h, g)
    # soft circles for texture
    p.setPen(Qt.NoPen)
    for i, (x, y, r) in enumerate(((0.85, 0.2, 0.55), (0.1, 1.0, 0.45), (0.55, 0.9, 0.25))):
        col = QColor(255, 255, 255, 22 if i % 2 == 0 else 14)
        p.setBrush(col)
        p.drawEllipse(QRectF(x * w - r * h, y * h - r * h, 2 * r * h, 2 * r * h))
    words = [x for x in name.replace("-", " ").split() if x[:1].isalnum()]
    initials = "".join(x[0] for x in words[:2]).upper() or "?"
    f = QFont()
    f.setPixelSize(int(h * 0.42))
    f.setWeight(QFont.Black)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255, 235))
    p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, initials)
    p.end()
    return pm


def rounded(pm: QPixmap, w: int, h: int, radius: float, top_only: bool = False) -> QPixmap:
    scaled = pm.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = (scaled.width() - w) // 2
    y = (scaled.height() - h) // 2
    scaled = scaled.copy(x, y, w, h)
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h + (radius if top_only else 0)), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, scaled)
    p.end()
    return out


def avatar_from_rgba(w: int, h: int, data: bytes, size: int = 36) -> QPixmap:
    img = QImage(data, w, h, w * 4, QImage.Format_RGBA8888).copy()
    return circle(QPixmap.fromImage(img), size)


def circle(pm: QPixmap, size: int) -> QPixmap:
    pm = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def letter_avatar(name: str, size: int = 36) -> QPixmap:
    return circle(placeholder_cover(name or "?", 120, 120), size)
