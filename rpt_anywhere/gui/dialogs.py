"""Add / edit game dialog."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from ..config import RUNNERS, Game
from ..steam_lib import Steam
from .widgets import label
from ..i18n import _


def _browse_row(edit: QLineEdit, mode: str, parent: QWidget, filt: str = "") -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    b = QPushButton(_('Durchsuchen…'))

    def pick():
        start = os.path.dirname(os.path.expanduser(edit.text())) or str(Path.home())
        if mode == "dir":
            p = QFileDialog.getExistingDirectory(parent, _('Ordner wählen'), start)
        else:
            p, _unused = QFileDialog.getOpenFileName(parent, _('Datei wählen'), start, filt)
        if p:
            edit.setText(p)
    b.clicked.connect(pick)
    h.addWidget(b)
    return w


def guess_runner(path: str) -> str:
    low = path.lower()
    if low.endswith((".exe", ".bat", ".msi", ".lnk")):
        return "proton"
    if os.path.isfile(path) and (os.access(path, os.X_OK) or low.endswith((".sh", ".x86_64",
                                                                          ".appimage"))):
        return "native"
    return "command" if " " in path or not os.path.exists(path) else "native"


class GameDialog(QDialog):
    def __init__(self, game: Game, steam: Steam, parent=None, is_new=False):
        super().__init__(parent)
        self.game = game
        self.steam = steam
        self.setWindowTitle(_('Spiel hinzufügen') if is_new else _("{name} bearbeiten").format(name=game.name))
        self.setMinimumWidth(620)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(14)
        v.addWidget(label(_('Spiel hinzufügen') if is_new else _('Spiel bearbeiten'), "H2"))
        is_steam = game.runner == "steam"

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.name = QLineEdit(game.name)
        form.addRow(_("Name"), self.name)

        self.runner = QComboBox()
        for k, t in RUNNERS.items():
            if k == "steam" and not is_steam:
                continue
            self.runner.addItem(_(t), k)
        self.runner.setCurrentIndex(max(0, self.runner.findData(game.runner)))
        self.runner.setEnabled(not is_steam)
        form.addRow(_('Starten mit'), self.runner)

        self.exe = QLineEdit(game.exe)
        self.exe.setPlaceholderText(_('leer = automatisch aus Steam') if is_steam
                                    else _("/pfad/zum/spiel  oder  /pfad/zum/Spiel.exe"))
        self.exe_row = _browse_row(self.exe, "file", self)
        self.exe_label = QLabel(_('Programm'))
        form.addRow(self.exe_label, self.exe_row)

        self.args = QLineEdit(game.args)
        self.args.setPlaceholderText(_('z. B. -windowed -nosplash'))
        form.addRow(_("Argumente"), self.args)

        self.workdir = QLineEdit(game.workdir)
        self.workdir.setPlaceholderText(_('leer = Ordner der Datei'))
        form.addRow(_("Arbeitsordner"), _browse_row(self.workdir, "dir", self))

        self.proton = QComboBox()
        self.proton.addItem(_('Standard'), "")
        for t in steam.proton_tools() if steam.ok else []:
            self.proton.addItem(t.name, t.internal)
        self.proton.setCurrentIndex(max(0, self.proton.findData(game.proton)))
        self.proton_label = QLabel(_('Proton-Version'))
        form.addRow(self.proton_label, self.proton)

        self.prefix = QLineEdit(game.prefix)
        self.prefix.setPlaceholderText(_('leer = automatisch'))
        self.prefix_row = _browse_row(self.prefix, "dir", self)
        self.prefix_label = QLabel(_('Prefix'))
        form.addRow(self.prefix_label, self.prefix_row)

        self.image = QLineEdit(game.image)
        self.image.setPlaceholderText(_('optional: eigenes Coverbild (460×215)'))
        form.addRow(_("Cover"), _browse_row(self.image, "file", self,
                                         _('Bilder (*.png *.jpg *.jpeg *.webp)')))

        self.env = QPlainTextEdit("\n".join(f"{k}={v}" for k, v in game.env.items()))
        self.env.setPlaceholderText(_('Umgebungsvariablen, eine pro Zeile\nz. B. DXVK_HUD=fps'))
        self.env.setFixedHeight(76)
        form.addRow(_("Umgebung"), self.env)

        self.own_appid = QCheckBox(_('Mit eigener Steam-AppID starten (nur falls das Spiel sonst nicht startet)'))
        self.own_appid.setChecked(game.own_appid)
        self.own_appid.setVisible(is_steam)
        form.addRow("", self.own_appid)
        v.addLayout(form)

        self.hint = label("", "Small", wrap=True)
        v.addWidget(self.hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(_('Abbrechen'))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(_('Speichern'))
        ok.setObjectName("Primary")
        ok.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        v.addLayout(btns)

        self.runner.currentIndexChanged.connect(self._sync)
        self.exe.textChanged.connect(self._auto_name)
        self._sync()

    def _auto_name(self, t: str):
        if not self.name.text().strip() and t and self.runner.currentData() != "command":
            self.name.setText(Path(t).stem.replace("_", " ").replace("-", " ").title())
        if self.runner.isEnabled() and t and self.runner.currentData() in ("native", "proton"):
            g = guess_runner(t)
            if g != self.runner.currentData() and g in ("native", "proton"):
                self.runner.setCurrentIndex(self.runner.findData(g))

    def _sync(self):
        r = self.runner.currentData()
        is_win = r in ("proton", "wine")
        for w in (self.proton, self.proton_label):
            w.setVisible(r == "proton" or (r == "steam"))
        for w in (self.prefix_row, self.prefix_label):
            w.setVisible(is_win or r == "steam")
        self.exe_label.setText(_("Befehl") if r == "command" else _("Programm"))
        hints = {
            "native": _('Linux-Programm, Skript oder AppImage. Wird direkt als Teil des Donor-Spiels gestartet – das Steam-Overlay wird automatisch eingebunden.'),
            "proton": _('Windows-Spiel über Steams Proton. Jedes Spiel bekommt automatisch einen eigenen Prefix unter ~/.local/share/rpt-anywhere/prefixes.'),
            "wine": _('Windows-Spiel über das System-Wine.'),
            "command": _('Beliebiger Shell-Befehl, z. B. »retroarch -L core.so rom.sfc« oder »flatpak run org.ppsspp.PPSSPP«. Hinweis: Flatpak-/Snap-Apps laufen in einer Sandbox, dort greift das Steam-Overlay oft nicht.'),
            "steam": _('Steam-Spiel aus deiner Bibliothek. Es wird direkt gestartet (ohne Steam), damit Steam es als Donor-Spiel streamt. Felder leer lassen = automatisch.'),
        }
        self.hint.setText(hints.get(r, ""))

    def _accept(self):
        g = self.game
        g.name = self.name.text().strip() or _("Unbenannt")
        g.runner = self.runner.currentData()
        g.exe = self.exe.text().strip()
        g.args = self.args.text().strip()
        g.workdir = self.workdir.text().strip()
        g.proton = self.proton.currentData() or ""
        g.prefix = self.prefix.text().strip()
        g.image = self.image.text().strip()
        env = {}
        for line in self.env.toPlainText().splitlines():
            if "=" in line:
                k, _unused, val = line.partition("=")
                if k.strip():
                    env[k.strip()] = val.strip()
        g.env = env
        g.own_appid = self.own_appid.isChecked()
        if g.runner != "steam" and not g.exe:
            self.hint.setText(_('Bitte ein Programm bzw. einen Befehl angeben.'))
            self.hint.setStyleSheet("color:#f87171;")
            return
        self.accept()
