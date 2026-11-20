"""Library, setup and settings pages + the right-hand session panel."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import config as cfgmod
from ..steam_lib import Steam, libsteam_api_score
from . import style as S
from .widgets import Dot, FlowLayout, FriendRow, GameCard, divider, label
from ..i18n import N_, _


def launcher_path() -> str:
    p = os.environ.get("RPTA_LAUNCHER")
    if p and os.path.exists(p):
        return p
    w = shutil.which("rpt-anywhere")
    if w:
        return w
    return str(Path(__file__).resolve().parents[2] / "bin" / "rpt-anywhere")


def donor_launch_options() -> str:
    return f'"{launcher_path()}" --donor # %command%'


def card(obj="Card") -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName(obj)
    v = QVBoxLayout(f)
    v.setContentsMargins(18, 16, 18, 16)
    v.setSpacing(10)
    return f, v


def scroll(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(widget)
    sa.setFrameShape(QFrame.NoFrame)
    return sa


# =========================================================================== #
class LibraryPage(QWidget):
    play = Signal(str)
    menu = Signal(str, QPoint)
    add = Signal()
    import_shortcuts = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards: dict[str, GameCard] = {}
        self.meta: dict[str, dict] = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(34, 28, 34, 0)
        v.setSpacing(16)

        top = QHBoxLayout()
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        tcol.addWidget(label(_('Bibliothek'), "H1"))
        self.count = label(_('Lade Spiele …'), "Muted")
        tcol.addWidget(self.count)
        top.addLayout(tcol, 1)
        imp = QPushButton(_('Steam-Verknüpfungen importieren'))
        imp.setToolTip(_('Übernimmt deine in Steam hinzugefügten Nicht-Steam-Spiele'))
        imp.clicked.connect(self.import_shortcuts)
        addb = QPushButton(_('＋  Spiel hinzufügen'))
        addb.setObjectName("Primary")
        addb.clicked.connect(self.add)
        top.addWidget(imp)
        top.addWidget(addb)
        v.addLayout(top)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText(_('Suchen …'))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        mag = QLabel("⌕", self.search)
        mag.setStyleSheet(f"color:{S.MUTED}; font-size:18px; background:transparent;")
        mag.move(12, 6)
        bar.addWidget(self.search, 1)
        self.group = QButtonGroup(self)
        for i, (key, text) in enumerate((("all", N_("Alle")), ("fav", N_("★ Favoriten")),
                                         ("own", N_("Eigene")), ("steam", N_("Steam")))):
            b = QPushButton(_(text))
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setProperty("key", key)
            b.setCursor(Qt.PointingHandCursor)
            if i == 0:
                b.setChecked(True)
            self.group.addButton(b)
            bar.addWidget(b)
        self.group.buttonClicked.connect(lambda _btn: self.apply_filter())
        v.addLayout(bar)

        self.grid_host = QWidget()
        self.flow = FlowLayout(self.grid_host, spacing=18, item_w=240)
        self.flow.setContentsMargins(0, 4, 4, 30)
        self.empty = label("", "Muted", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.hide()
        v.addWidget(self.empty)
        v.addWidget(scroll(self.grid_host), 1)

    # -------------------------------------------------------------- content
    def set_games(self, entries: list[dict]):
        """entries: dicts with id, name, runner, subtitle, rpt, favorite, source, last_played."""
        for c in self.cards.values():
            self.flow.removeWidget(c)
            c.deleteLater()
        self.cards.clear()
        self.meta = {e["id"]: e for e in entries}
        entries = sorted(entries, key=lambda e: (not e["favorite"], -e["last_played"],
                                                 e["name"].lower()))
        for e in entries:
            c = GameCard(e["id"], e["name"], e["runner"], e.get("subtitle", ""),
                         e.get("rpt"), e["favorite"])
            c.play.connect(self.play)
            c.menu.connect(self.menu)
            self.flow.addWidget(c)
            self.cards[e["id"]] = c
        n_steam = sum(1 for e in entries if e["source"] == "steam")
        self.count.setText(_('{v0} Spiele · {v1} eigene · {n_steam} aus Steam').format(v0=len(entries), v1=len(entries) - n_steam, n_steam=n_steam))
        self.apply_filter()

    def apply_filter(self):
        q = self.search.text().strip().lower()
        b = self.group.checkedButton()
        key = b.property("key") if b else "all"
        shown = 0
        for gid, c in self.cards.items():
            m = self.meta[gid]
            ok = (not q or q in m["name"].lower()) and (
                key == "all" or (key == "fav" and m["favorite"]) or
                (key == "own" and m["source"] == "own") or
                (key == "steam" and m["source"] == "steam"))
            c.setVisible(ok)
            shown += ok
        self.empty.setVisible(shown == 0)
        if not self.cards:
            self.empty.setText(_('Noch keine Spiele. Füge eins mit »＋ Spiel hinzufügen« hinzu oder zieh eine Datei ins Fenster.'))
        else:
            self.empty.setText(_('Nichts gefunden.'))
        self.flow.invalidate()
        self.grid_host.updateGeometry()

    def set_cover(self, gid, pm):
        c = self.cards.get(gid)
        if c:
            c.set_cover(pm)

    def set_running(self, gid: str | None):
        for k, c in self.cards.items():
            c.set_running(k == gid)


# =========================================================================== #
class SessionPanel(QWidget):
    start_donor = Signal()
    stop_game = Signal()
    invite = Signal(object)
    guest_link = Signal()
    open_overlay = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(340)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#Sidebar {{ border-right: none; border-left: 1px solid {S.BORDER}; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 24, 18, 18)
        v.setSpacing(14)

        # ---- host status
        self.status_card, sv = card()
        row = QHBoxLayout()
        self.dot = Dot(S.MUTED, 10)
        row.addWidget(self.dot)
        self.status_title = label(_('Nicht über Steam gestartet'), "H2")
        self.status_title.setStyleSheet("font-size:15px; font-weight:800;")
        row.addWidget(self.status_title, 1)
        sv.addLayout(row)
        self.status_text = label("", "Small", wrap=True)
        sv.addWidget(self.status_text)
        self.donor_btn = QPushButton(_('Über Steam starten'))
        self.donor_btn.setObjectName("Primary")
        self.donor_btn.clicked.connect(self.start_donor)
        sv.addWidget(self.donor_btn)
        v.addWidget(self.status_card)

        # ---- now playing
        self.now_card, nv = card()
        nv.addWidget(label(_('JETZT AKTIV'), "SectionTitle"))
        self.now_name = label("—", "H2")
        self.now_name.setWordWrap(True)
        nv.addWidget(self.now_name)
        self.now_info = label("", "Small", wrap=True)
        nv.addWidget(self.now_info)
        nb = QHBoxLayout()
        self.overlay_btn = QPushButton(_('Steam-Overlay'))
        self.overlay_btn.setToolTip(_('Öffnet das Remote-Play-Together-Fenster im Overlay'))
        self.overlay_btn.clicked.connect(self.open_overlay)
        self.stop_btn = QPushButton(_('Beenden'))
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.clicked.connect(self.stop_game)
        nb.addWidget(self.overlay_btn)
        nb.addWidget(self.stop_btn)
        nv.addLayout(nb)
        self.now_card.hide()
        v.addWidget(self.now_card)

        # ---- connected players
        v.addWidget(label(_('MITSPIELER'), "SectionTitle"))
        self.players = QVBoxLayout()
        self.players.setSpacing(6)
        self.no_players = label(_('Noch niemand verbunden.'), "Small")
        self.players.addWidget(self.no_players)
        v.addLayout(self.players)

        # ---- invite
        v.addWidget(divider())
        v.addWidget(label(_('EINLADEN'), "SectionTitle"))
        self.guest_btn = QPushButton(_('🔗  Gast-Link erstellen'))
        self.guest_btn.setObjectName("Primary")
        self.guest_btn.setToolTip(_('Link für Leute ohne Steam-Account (Steam Link App / Browser)'))
        self.guest_btn.clicked.connect(self.guest_link)
        v.addWidget(self.guest_btn)
        self.link_row = QWidget()
        lr = QHBoxLayout(self.link_row)
        lr.setContentsMargins(0, 0, 0, 0)
        self.link_edit = QLineEdit()
        self.link_edit.setReadOnly(True)
        copy = QPushButton(_('Kopieren'))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.link_edit.text()))
        lr.addWidget(self.link_edit, 1)
        lr.addWidget(copy)
        self.link_row.hide()
        v.addWidget(self.link_row)

        self.friend_search = QLineEdit()
        self.friend_search.setPlaceholderText(_('Freunde filtern …'))
        self.friend_search.setClearButtonEnabled(True)
        self.friend_search.textChanged.connect(self._filter_friends)
        v.addWidget(self.friend_search)
        self.friend_list = QListWidget()
        self.friend_list.setSelectionMode(QListWidget.NoSelection)
        self.friend_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        v.addWidget(self.friend_list, 1)
        self.friend_hint = label("", "Small", wrap=True)
        v.addWidget(self.friend_hint)
        self.filler = QWidget()
        v.addWidget(self.filler, 1)
        self.rows: dict[int, FriendRow] = {}
        self.set_invites_enabled(False, _('Einladen geht, sobald die App über dein Donor-Spiel in Steam gestartet wurde. Alternativ: Shift+Tab im Spiel → Freund rechtsklicken → Remote Play Together.'))

    # -------------------------------------------------------------- state
    def set_status(self, title: str, text: str, color: str, show_btn: bool, btn_text: str = ""):
        self.status_title.setText(title)
        self.status_text.setText(text)
        self.dot.set_color(color)
        self.donor_btn.setVisible(show_btn)
        if btn_text:
            self.donor_btn.setText(btn_text)

    def set_now(self, name: str | None, info: str = ""):
        self.now_card.setVisible(bool(name))
        if name:
            self.now_name.setText(name)
            self.now_info.setText(info)

    def set_invites_enabled(self, on: bool, hint: str = ""):
        self.guest_btn.setEnabled(on)
        self.overlay_btn.setEnabled(on)
        self.friend_search.setVisible(on)
        self.friend_list.setVisible(on)
        self.friend_hint.setText(hint)
        self.friend_hint.setVisible(bool(hint))
        self.filler.setVisible(not on)

    def set_friends(self, friends):
        sig = [(f.steamid, f.name, f.state_text) for f in friends]
        if sig == getattr(self, "_sig", None):
            return False
        self._sig = sig
        scroll_pos = self.friend_list.verticalScrollBar().value()
        self.friend_list.clear()
        self.rows.clear()
        for f in friends:
            it = QListWidgetItem()
            row = FriendRow(f.steamid, f.name, f.state_text, f.online, bool(f.playing_appid))
            row.invite.connect(self.invite)
            it.setSizeHint(row.sizeHint())
            it.setData(Qt.UserRole, f.name.lower())
            self.friend_list.addItem(it)
            self.friend_list.setItemWidget(it, row)
            self.rows[f.steamid] = row
        self._filter_friends()
        self.friend_list.verticalScrollBar().setValue(scroll_pos)
        return True

    def _filter_friends(self):
        q = self.friend_search.text().strip().lower()
        for i in range(self.friend_list.count()):
            it = self.friend_list.item(i)
            it.setHidden(bool(q) and q not in it.data(Qt.UserRole))

    def set_players(self, sessions):
        while self.players.count():
            w = self.players.takeAt(0).widget()
            if w and w is not self.no_players:
                w.deleteLater()
        if not sessions:
            self.players.addWidget(self.no_players)
            self.no_players.show()
            return
        self.no_players.hide()
        for s in sessions:
            f, fv = card("CardFlat")
            fv.setContentsMargins(12, 8, 12, 8)
            r = QHBoxLayout()
            r.addWidget(Dot(S.OK, 8))
            r.addWidget(label(s["name"]), 1)
            r.addWidget(label(s.get("device", ""), "Small"))
            fv.addLayout(r)
            self.players.addWidget(f)

    def show_link(self, url: str):
        self.link_edit.setText(url)
        self.link_edit.setCursorPosition(0)
        self.link_row.show()


# =========================================================================== #
class SetupPage(QWidget):
    changed = Signal()
    toast = Signal(str, str)
    test_donor = Signal()

    def __init__(self, cfg: cfgmod.Config, steam: Steam, parent=None):
        super().__init__(parent)
        self.cfg, self.steam = cfg, steam
        self.apps = []
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(34, 28, 34, 34)
        v.setSpacing(16)
        v.addWidget(label(_('Einrichtung'), "H1"))
        v.addWidget(label(
            _('Steam erlaubt Remote Play Together nur für Spiele, die es selbst gestartet hat. Der Trick: Du leihst dir ein installiertes Steam-Spiel mit Remote-Play-Together-Unterstützung als <b>Donor</b>. Steam startet statt des Spiels diese App – und alles, was du von hier startest, streamt Steam als dieses Spiel. Am Donor-Spiel selbst wird nichts verändert, nur seine Startoptionen.'), "Muted", wrap=True))

        # ---- step 1
        c1, v1 = card()
        v1.addWidget(label(_('① Donor-Spiel wählen'), "H2"))
        v1.addWidget(label(_('Spiele mit dem Abzeichen »RPT ✓« unterstützen Remote Play Together laut Steam-Store. Am besten ein kleines Spiel, das du sonst nicht spielst – solange es als Donor eingerichtet ist, startet es nicht mehr selbst.'), "Small", wrap=True))
        self.donor_search = QLineEdit()
        self.donor_search.setPlaceholderText(_('Installierte Spiele durchsuchen …'))
        self.donor_search.textChanged.connect(self._fill_donors)
        v1.addWidget(self.donor_search)
        self.only_rpt = QCheckBox(_('Nur Spiele mit Remote Play Together zeigen'))
        self.only_rpt.setChecked(True)
        self.only_rpt.toggled.connect(self._fill_donors)
        v1.addWidget(self.only_rpt)
        self.donor_list = QListWidget()
        self.donor_list.setFixedHeight(220)
        self.donor_list.itemClicked.connect(self._pick_donor)
        v1.addWidget(self.donor_list)
        self.donor_sel = label("", "Small")
        v1.addWidget(self.donor_sel)
        v.addWidget(c1)

        # ---- step 2
        c2, v2 = card()
        v2.addWidget(label(_('② Startoptionen des Donors setzen'), "H2"))
        v2.addWidget(label(_('In Steam: Rechtsklick aufs Donor-Spiel → Eigenschaften → Startoptionen, und diese Zeile einfügen:'), "Small", wrap=True))
        self.opts = QLineEdit()
        self.opts.setObjectName("Mono")
        self.opts.setReadOnly(True)
        v2.addWidget(self.opts)
        r2 = QHBoxLayout()
        cp = QPushButton(_('Kopieren'))
        cp.clicked.connect(self._copy)
        self.auto_btn = QPushButton(_('Automatisch eintragen'))
        self.auto_btn.setToolTip(_('Schreibt die Startoption in Steams localconfig.vdf (Steam muss dafür beendet sein; es wird ein Backup angelegt)'))
        self.auto_btn.clicked.connect(self._auto)
        self.close_steam = QPushButton(_('Steam beenden'))
        self.close_steam.clicked.connect(self._shutdown_steam)
        r2.addWidget(cp)
        r2.addWidget(self.auto_btn)
        r2.addWidget(self.close_steam)
        r2.addStretch(1)
        v2.addLayout(r2)
        self.opts_state = label("", "Small", wrap=True)
        v2.addWidget(self.opts_state)
        v.addWidget(c2)

        # ---- step 3
        c3, v3 = card()
        v3.addWidget(label(_('③ Einladen aus der App (optional)'), "H2"))
        v3.addWidget(label(_('Für Freundesliste und Gast-Links braucht die App eine libsteam_api.so (64-bit). Viele installierte Spiele bringen sie mit – sie wird automatisch gesucht. Ohne sie lädst du wie gewohnt über das Steam-Overlay (Shift+Tab) ein.'), "Small", wrap=True))
        self.lib_state = label("", "Small", wrap=True)
        v3.addWidget(self.lib_state)
        r3 = QHBoxLayout()
        find = QPushButton(_('Automatisch suchen'))
        find.clicked.connect(self._find_lib)
        pick = QPushButton(_('Datei wählen …'))
        pick.clicked.connect(self._pick_lib)
        r3.addWidget(find)
        r3.addWidget(pick)
        r3.addStretch(1)
        v3.addLayout(r3)
        v.addWidget(c3)

        # ---- step 4
        c4, v4 = card()
        v4.addWidget(label(_('④ Loslegen'), "H2"))
        v4.addWidget(label(_('Starte ein Spiel aus der Bibliothek – die App lässt Steam das Donor-Spiel starten, verbindet sich damit und startet dein Spiel. Hast du die Startoption gerade erst gesetzt, starte Steam einmal neu. Klappt etwas nicht, sieh im Reiter »Logs« unter »Donor-Platzhalter« nach.'), "Small", wrap=True))
        t = QPushButton(_('Donor jetzt über Steam starten'))
        t.setObjectName("Primary")
        t.clicked.connect(self.test_donor)
        r4 = QHBoxLayout()
        r4.addWidget(t)
        r4.addStretch(1)
        v4.addLayout(r4)
        v.addWidget(c4)
        v.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll(inner))
        self.opts.setText(donor_launch_options())
        self._timer = QTimer(self, interval=2500, timeout=self.refresh_state)

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh_state()
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    # -------------------------------------------------------------- data
    def set_apps(self, apps):
        self.apps = apps
        if not any(a.rpt for a in apps):
            self.only_rpt.setChecked(False)
        self._fill_donors()
        self.refresh_state()

    def _fill_donors(self):
        q = self.donor_search.text().strip().lower()
        self.donor_list.clear()
        apps = sorted(self.apps, key=lambda a: (not a.rpt, a.rpt is None, a.name.lower()))
        for a in apps:
            if self.only_rpt.isChecked() and not a.rpt:
                continue
            if q and q not in a.name.lower():
                continue
            mark = _('   RPT ✓') if a.rpt else ("" if a.rpt is None else _('   (kein RPT)'))
            it = QListWidgetItem(f"{a.name}{mark}")
            it.setData(Qt.UserRole, (a.appid, a.name))
            if a.appid == self.cfg.donor_appid:
                it.setText("● " + it.text())
            self.donor_list.addItem(it)
        if not self.donor_list.count():
            it = QListWidgetItem(_('Keine passenden Spiele gefunden.') if self.steam.ok
                                 else _('Keine Steam-Installation gefunden.'))
            it.setFlags(Qt.NoItemFlags)
            self.donor_list.addItem(it)

    def _pick_donor(self, it: QListWidgetItem):
        d = it.data(Qt.UserRole)
        if not d:
            return
        self.cfg.donor_appid, self.cfg.donor_name = d
        self.cfg.save()
        self._fill_donors()
        self.refresh_state()
        self.changed.emit()

    def refresh_state(self):
        cfg = self.cfg
        self.opts.setText(donor_launch_options())
        if cfg.donor_appid:
            self.donor_sel.setText(_('Gewählt: <b>{donor_name}</b> (AppID {donor_appid})').format(donor_name=cfg.donor_name, donor_appid=cfg.donor_appid))
        else:
            self.donor_sel.setText(_('Noch kein Donor gewählt.'))
        running = self.steam.is_running() if self.steam.ok else False
        self.auto_btn.setEnabled(bool(cfg.donor_appid) and not running and self.steam.ok)
        self.close_steam.setVisible(running)
        cur = self.steam.get_launch_options(cfg.donor_appid) if cfg.donor_appid else None
        if not cfg.donor_appid:
            st = ""
        elif cur and launcher_path() in cur:
            st = _("<span style='color:{OK}'>✓ Startoption ist eingetragen.</span>").format(OK=S.OK)
        elif cur:
            st = (_("<span style='color:{WARN}'>Aktuelle Startoption ist eine andere:</span> <code>{cur}</code>").format(WARN=S.WARN, cur=cur))
        else:
            st = _('Noch nicht eingetragen.')
        if running and cfg.donor_appid and not (cur and launcher_path() in cur):
            st += _('<br>Zum automatischen Eintragen muss Steam beendet sein – oder trag es manuell ein (Steam muss dafür laufen).')
        self.opts_state.setText(st)
        lib = cfg.libsteam_api
        if lib and Path(lib).exists():
            self.lib_state.setText(_("<span style='color:{OK}'>✓</span> {lib}").format(OK=S.OK, lib=lib))
        else:
            self.lib_state.setText(_('Noch keine libsteam_api.so gewählt.'))

    # -------------------------------------------------------------- actions
    def _copy(self):
        QGuiApplication.clipboard().setText(self.opts.text())
        self.toast.emit("Startoption kopiert.", "ok")

    def _auto(self):
        try:
            backup = self.steam.set_launch_options(self.cfg.donor_appid, self.opts.text())
        except Exception as e:
            self.toast.emit(_("Konnte nicht eintragen: {e}").format(e=e), "err")
            return
        self.toast.emit(_("Eingetragen ✓ (Backup: {name}). Du kannst Steam jetzt starten.").format(name=backup.name),
                        "ok")
        self.refresh_state()

    def _shutdown_steam(self):
        try:
            subprocess.Popen(["steam", "-shutdown"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.toast.emit("Steam wird beendet …", "info")
        except OSError:
            self.toast.emit(_("Konnte Steam nicht beenden – bitte manuell schließen."), "err")

    def _find_lib(self):
        libs = self.steam.find_libsteam_api() if self.steam.ok else []
        if libs:
            self.cfg.libsteam_api = str(libs[0])
            self.cfg.save()
            self.toast.emit(_("Gefunden: {path}").format(path=f"{libs[0].parent.name}/{libs[0].name}"), "ok")
        else:
            self.toast.emit("Keine passende libsteam_api.so in deinen Spielen gefunden.", "warn")
        self.refresh_state()

    def _pick_lib(self):
        p, _unused = QFileDialog.getOpenFileName(self, _('libsteam_api.so wählen'), str(Path.home()),
                                           _('libsteam_api.so (libsteam_api.so)'))
        if not p:
            return
        if not libsteam_api_score(Path(p)):
            self.toast.emit(_("Diese Datei ist zu alt oder nicht 64-bit (Remote-Play-API fehlt)."),
                            "err")
            return
        self.cfg.libsteam_api = p
        self.cfg.save()
        self.refresh_state()


# =========================================================================== #
class SettingsPage(QWidget):
    changed = Signal()
    reset = Signal()
    restart = Signal()

    def __init__(self, cfg: cfgmod.Config, steam: Steam, parent=None):
        super().__init__(parent)
        self.cfg, self.steam = cfg, steam
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(34, 28, 34, 34)
        v.setSpacing(16)
        v.addWidget(label(_('Einstellungen'), "H1"))

        c, cv = card()
        cv.addWidget(label("Language / Sprache", "H2"))
        from ..i18n import LANGUAGES
        self.lang = QComboBox()
        for code, native in LANGUAGES.items():
            self.lang.addItem(native, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(cfg.language or "en")))
        self.lang.currentIndexChanged.connect(self._lang_changed)
        cv.addWidget(self.lang)
        v.addWidget(c)

        c, cv = card()
        cv.addWidget(label(_('Bibliothek'), "H2"))
        self.show_steam = QCheckBox(_('Installierte Steam-Spiele in der Bibliothek anzeigen'))
        self.show_steam.setChecked(cfg.show_steam_games)
        self.show_steam.toggled.connect(self._save)
        cv.addWidget(self.show_steam)
        self.auto_overlay = QCheckBox(_('Nach Spielstart automatisch das Remote-Play-Together-Fenster im Overlay öffnen'))
        self.auto_overlay.setChecked(cfg.auto_open_rpt_overlay)
        self.auto_overlay.toggled.connect(self._save)
        cv.addWidget(self.auto_overlay)
        v.addWidget(c)

        c, cv = card()
        cv.addWidget(label(_('Proton'), "H2"))
        cv.addWidget(label(_('Standard für Windows-Spiele, die nicht aus Steam stammen.'), "Small"))
        self.proton = QComboBox()
        self.proton.addItem(_('Neueste installierte Version'), "")
        for t in steam.proton_tools() if steam.ok else []:
            self.proton.addItem(t.name, t.internal)
        self.proton.setCurrentIndex(max(0, self.proton.findData(cfg.default_proton)))
        self.proton.currentIndexChanged.connect(self._save)
        cv.addWidget(self.proton)
        v.addWidget(c)

        c, cv = card()
        cv.addWidget(label(_('Steam'), "H2"))
        cv.addWidget(label(_('Steam-Ordner (leer = automatisch)'), "Small"))
        self.root = QLineEdit(cfg.steam_root)
        self.root.setPlaceholderText(str(steam.root) if steam.root else _('nicht gefunden'))
        self.root.editingFinished.connect(self._save)
        cv.addWidget(self.root)
        info = _("Erkannt: {root}").format(root=steam.root) if steam.ok else _('Keine Steam-Installation gefunden.')
        if steam.is_flatpak:
            info += (_('\n⚠ Flatpak-Steam: Die App läuft dann in Steams Sandbox. Am zuverlässigsten ist das Steam-Paket deiner Distribution.'))
        cv.addWidget(label(info, "Small", wrap=True))
        v.addWidget(c)

        c, cv = card()
        cv.addWidget(label(_('Dateien'), "H2"))
        r = QHBoxLayout()
        for text, path in ((N_("Konfiguration"), cfgmod.CONFIG_DIR), (N_("Prefixe"), cfgmod.PREFIX_DIR),
                           (N_("Logs"), cfgmod.CACHE_DIR / "logs")):
            b = QPushButton(_('{text} öffnen').format(text=_(text)))
            b.clicked.connect(lambda _checked=False, p=path: _open_dir(p))
            r.addWidget(b)
        r.addStretch(1)
        cv.addLayout(r)
        v.addWidget(c)

        c, cv = card()
        cv.addWidget(label(_('Fehlerbehebung'), "H2"))
        cv.addWidget(label(_('Startet Steam dein Donor-Spiel von selbst oder hängt eine Session? Der Notaus beendet die Remote-Play-Session, den Agent im Steam-Prozess und das zuletzt gestartete Spiel. Im Terminal: rpt-anywhere --reset'),
                           "Small", wrap=True))
        rb = QHBoxLayout()
        nb = QPushButton(_('Notaus: Session und Hintergrundprozesse beenden'))
        nb.setObjectName("Danger")
        nb.clicked.connect(self.reset)
        rb.addWidget(nb)
        rb.addStretch(1)
        cv.addLayout(rb)
        v.addWidget(c)

        c, cv = card()
        cv.addWidget(label(f"{cfgmod.APP_NAME}", "H2"))
        cv.addWidget(label(
            _('Inspiriert von RemotePlayDetached, cooplauncher und RemotePlayWhatever. Nutzt ausschließlich offizielle Steamworks-Schnittstellen und Steams Startoptionen – keine Eingriffe in den Steam-Client.'), "Small", wrap=True))
        v.addWidget(c)
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll(inner))

    def _lang_changed(self, *_args):
        code = self.lang.currentData()
        if code == self.cfg.language:
            return
        self.cfg.language = code
        self.cfg.save()
        from PySide6.QtWidgets import QMessageBox
        from ..i18n import set_language
        set_language(code)   # so the question below already appears in the new language
        if QMessageBox.question(self, "Language / Sprache",
                                _("Die Sprache wird nach einem Neustart der App übernommen. "
                                  "Jetzt neu starten?")) == QMessageBox.Yes:
            self.restart.emit()

    def _save(self, *_args):
        old_root, old_steam = self.cfg.steam_root, self.cfg.show_steam_games
        self.cfg.show_steam_games = self.show_steam.isChecked()
        self.cfg.auto_open_rpt_overlay = self.auto_overlay.isChecked()
        self.cfg.default_proton = self.proton.currentData() or ""
        self.cfg.steam_root = self.root.text().strip()
        self.cfg.save()
        if (old_root, old_steam) != (self.cfg.steam_root, self.cfg.show_steam_games):
            self.changed.emit()


def _open_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)
    from ..steam_lib import clean_env
    subprocess.Popen(["xdg-open", str(p)], env=clean_env(), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# =========================================================================== #
def _short(path) -> str:
    home = os.path.expanduser("~")
    p = str(path)
    return "~" + p[len(home):] if home and p.startswith(home + "/") else p


class LogsPage(QWidget):
    """Live view of all log files (tail -f style)."""
    reset = Signal()

    MAX_BYTES = 400_000

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QFont, QTextCursor  # noqa: F401
        from PySide6.QtWidgets import QPlainTextEdit
        v = QVBoxLayout(self)
        v.setContentsMargins(34, 28, 34, 24)
        v.setSpacing(12)
        top = QHBoxLayout()
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        tcol.addWidget(label(_('Logs'), "H1"))
        tcol.addWidget(label(_('Wird live aktualisiert. Hilfreich, wenn etwas nicht startet.'),
                             "Muted", wrap=True))
        top.addLayout(tcol, 1)
        cp = QPushButton(_('Kopieren'))
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.view.toPlainText()))
        clr = QPushButton(_('Leeren'))
        clr.setToolTip(_('Leert die Logdatei'))
        clr.clicked.connect(self._clear)
        op = QPushButton(_('Ordner öffnen'))
        op.clicked.connect(lambda: _open_dir(cfgmod.CACHE_DIR))
        nb = QPushButton(_('Notaus'))
        nb.setObjectName("Danger")
        nb.setToolTip(_('Session, Agent im Steam-Prozess und letztes Spiel beenden'))
        nb.clicked.connect(self.reset)
        for b in (cp, clr, op, nb):
            top.addWidget(b, 0, Qt.AlignBottom)
        v.addLayout(top)

        self.source = QComboBox()
        self.source.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.source.setMinimumContentsLength(18)
        self.source.currentIndexChanged.connect(self._switch)
        v.addWidget(self.source)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setObjectName("Mono")
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setMaximumBlockCount(5000)
        v.addWidget(self.view, 1)
        foot = QHBoxLayout()
        self.info = label("", "Small")
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        foot.addWidget(self.info, 1)
        self.follow = QCheckBox(_('Automatisch mitscrollen'))
        self.follow.setChecked(True)
        foot.addWidget(self.follow)
        v.addLayout(foot)

        self.path: Path | None = None
        self.pos = 0
        self._timer = QTimer(self, interval=1000, timeout=self._poll)

    # -------------------------------------------------------------- sources
    def sources(self) -> list[tuple[str, Path]]:
        c = cfgmod.CACHE_DIR
        out = [(_('App (Ereignisse)'), c / "app.log"),
               (_('App (Ausgabe / Abstürze)'), c / "rpt-anywhere.log"),
               (_('Donor-Platzhalter (von Steam gestartet)'), c / "donor" / "stub.log")]
        logs = c / "logs"
        if logs.is_dir():
            for p in sorted(logs.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
                out.append((_("Spiel: {name}").format(name=p.stem), p))
        return out

    def _fill(self):
        cur = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        for name, p in self.sources():
            self.source.addItem(name, str(p))
        idx = self.source.findData(cur) if cur else 0
        self.source.setCurrentIndex(max(0, idx))
        self.source.blockSignals(False)
        if self.source.currentData() != (str(self.path) if self.path else None):
            self._switch()

    def show_file(self, path: Path):
        self._fill()
        i = self.source.findData(str(path))
        if i >= 0:
            self.source.setCurrentIndex(i)

    def _switch(self, *_args):
        d = self.source.currentData()
        self.path = Path(d) if d else None
        self.pos = 0
        self.view.clear()
        self._poll()

    def _poll(self):
        if not self.path:
            return
        try:
            size = self.path.stat().st_size
        except OSError:
            self.info.setText(_('{path}  –  (noch) nicht vorhanden').format(path=_short(self.path)))
            return
        if size < self.pos:  # truncated / rewritten
            self.pos = 0
            self.view.clear()
        if size > self.pos:
            start = max(self.pos, size - self.MAX_BYTES) if self.pos == 0 else self.pos
            with open(self.path, "rb") as fh:
                fh.seek(start)
                data = fh.read(size - start).decode("utf-8", "replace")
            self.pos = size
            sb = self.view.verticalScrollBar()
            at_end = sb.value() >= sb.maximum() - 4
            from PySide6.QtGui import QTextCursor
            cur = self.view.textCursor()
            cur.movePosition(QTextCursor.End)
            cur.insertText(data)
            if self.follow.isChecked() or at_end:
                sb.setValue(sb.maximum())
        import time as _t
        self.info.setText(_('{path}  ·  {v1:.1f} KB  ·  aktualisiert {v2}').format(path=_short(self.path), v1=size / 1024, v2=_t.strftime('%H:%M:%S')))

    def _clear(self):
        if self.path and self.path.exists():
            self.path.write_text("")
            self._switch()

    def showEvent(self, e):
        super().showEvent(e)
        self._fill()
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()
