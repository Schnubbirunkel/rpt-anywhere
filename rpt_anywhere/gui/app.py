"""Main window: glues library, launcher, Steam bridge and pages together."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QMainWindow,
                               QMenu, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..i18n import N_, _
from .. import config as cfgmod
from ..log import log
from ..config import Config, Game
from ..donor_link import DonorLink
from ..launcher import LaunchError, RunningGame, plan
from ..steam_lib import Steam
from . import style as S
from .dialogs import GameDialog, guess_runner
from .images import CoverLoader, avatar_from_rgba
from .pages import LibraryPage, LogsPage, SessionPanel, SettingsPage, SetupPage
from .widgets import Dot, Toast, label

ASSETS = Path(__file__).resolve().parents[1] / "assets"


# --------------------------------------------------------------------------- #
class LibraryScan(QObject):
    done = Signal(list)

    def __init__(self, steam: Steam):
        super().__init__()
        self.steam = steam

    def run(self):
        try:
            apps = self.steam.installed_apps() if self.steam.ok else []
        except Exception:
            apps = []
        self.done.emit(apps)


# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        self.steam = Steam(self.cfg.steam_root or None)
        self.link = DonorLink()
        self.donor_mode: str | None = None   # None | "agent" | "stub"
        self.donor_id = 0
        self.apps = None
        self.host_game: RunningGame | None = None
        self._agent_state: dict | None = None
        self._seen_events: set = set()
        self._guest_ts = None
        self._guest_requested = 0.0
        self._avatar_tick = 0
        self._was_running = False
        self._launched_at = 0.0
        self._last_name = ""
        self.covers = CoverLoader(self)
        self.covers.loaded.connect(self._cover_loaded)
        self.cover_keys: dict[str, list[str]] = {}
        self._waiting_since = 0.0

        self.setWindowTitle(f"{cfgmod.APP_NAME} {__version__}")
        icon = ASSETS / "icon.svg"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1440, 880)
        self.setMinimumSize(1080, 640)
        self.setAcceptDrops(True)

        root = QWidget()
        root.setObjectName("Root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.library = LibraryPage()
        self.library.play.connect(self.launch_id)
        self.library.menu.connect(self._game_menu)
        self.library.add.connect(lambda: self.add_game())
        self.library.import_shortcuts.connect(self.import_shortcuts)
        self.setup = SetupPage(self.cfg, self.steam)
        self.setup.changed.connect(self._refresh_status)
        self.setup.toast.connect(self.toast)
        self.setup.test_donor.connect(self.start_donor)
        self.settings = SettingsPage(self.cfg, self.steam)
        self.settings.changed.connect(self._settings_changed)
        self.settings.reset.connect(self.emergency_reset)
        self.settings.restart.connect(self.restart_app)
        self.logs = LogsPage()
        self.logs.reset.connect(self.emergency_reset)
        for w in (self.library, self.setup, self.settings, self.logs):
            self.stack.addWidget(w)
        h.addWidget(self.stack, 1)

        self.panel = SessionPanel()
        self.panel.start_donor.connect(self.start_donor)
        self.panel.stop_game.connect(self.stop_game)
        self.panel.invite.connect(self.invite_friend)
        self.panel.guest_link.connect(self.guest_link)
        self.panel.open_overlay.connect(self.open_overlay)
        h.addWidget(self.panel)
        self.setCentralWidget(root)
        self._toast = Toast(root)

        self._monitor = QTimer(self, interval=1000, timeout=self._tick)
        self._monitor.start()

        self.scan_library()
        self._refresh_status()
        self._poll_donor()
        if not self.cfg.donor_appid:
            QTimer.singleShot(300, lambda: self.go(1))

    # ================================================================ layout
    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setAttribute(Qt.WA_StyledBackground, True)
        side.setFixedWidth(232)
        v = QVBoxLayout(side)
        v.setContentsMargins(16, 22, 16, 18)
        v.setSpacing(4)
        brand = QHBoxLayout()
        logo = label("◖◗")
        logo.setStyleSheet(f"color:{S.ACCENT}; font-size:22px; font-weight:900;")
        brand.addWidget(logo)
        bcol = QVBoxLayout()
        bcol.setSpacing(0)
        bcol.addWidget(label(cfgmod.APP_NAME, "Brand"))
        brow = QHBoxLayout()
        brow.setSpacing(6)
        vb = label(f"v{__version__}")
        vb.setToolTip(_('Installierte Version'))
        vb.setStyleSheet(f"color:{S.ACCENT}; background:{S.SURFACE_3}; border-radius:6px; "
                         "padding:1px 6px; font-size:10px; font-weight:800;")
        brow.addWidget(vb)
        brow.addWidget(label(_('Remote Play Together'), "BrandSub"))
        brow.addStretch(1)
        bcol.addLayout(brow)
        brand.addLayout(bcol, 1)
        v.addLayout(brand)
        v.addSpacing(22)
        self.nav = QButtonGroup(self)
        for i, text in enumerate((N_("▦   Bibliothek"), N_("✦   Einrichtung"), N_("⚙   Einstellungen"),
                                  N_("☰   Logs"))):
            b = QPushButton(_(text))
            b.setObjectName("Nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _checked=False, i=i: self.go(i))
            self.nav.addButton(b, i)
            v.addWidget(b)
        self.nav.button(0).setChecked(True)
        v.addStretch(1)

        self.donor_box = QFrame()
        self.donor_box.setObjectName("CardFlat")
        dv = QVBoxLayout(self.donor_box)
        dv.setContentsMargins(14, 12, 14, 12)
        dv.setSpacing(4)
        dv.addWidget(label(_('DONOR-SPIEL'), "SectionTitle"))
        row = QHBoxLayout()
        self.donor_dot = Dot(S.MUTED, 8)
        row.addWidget(self.donor_dot)
        self.donor_label = label(_('nicht gewählt'))
        self.donor_label.setStyleSheet("font-weight:700;")
        self.donor_label.setWordWrap(True)
        row.addWidget(self.donor_label, 1)
        dv.addLayout(row)
        self.user_label = label("", "Small")
        dv.addWidget(self.user_label)
        v.addWidget(self.donor_box)
        ver = label(_('Version {version__}').format(version__=__version__), "Small")
        ver.setToolTip(f"{cfgmod.APP_NAME} {__version__}\n{Path(__file__).resolve().parents[2]}")
        ver.setStyleSheet(f"color:{S.MUTED}; font-size:11px; padding:8px 4px 0 4px;")
        v.addWidget(ver)
        return side

    def go(self, i: int):
        self.stack.setCurrentIndex(i)
        self.nav.button(i).setChecked(True)

    def toast(self, text: str, kind: str = "info"):
        self._toast.show_msg(text, kind)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._toast.isVisible():
            p = self._toast.parentWidget()
            self._toast.move((p.width() - self._toast.width()) // 2,
                             p.height() - self._toast.height() - 28)

    # ================================================================ library
    def scan_library(self):
        self._thread = QThread(self)
        self._scan = LibraryScan(self.steam)
        self._scan.moveToThread(self._thread)
        self._thread.started.connect(self._scan.run)
        self._scan.done.connect(self._library_ready)
        self._scan.done.connect(self._thread.quit)
        self._thread.start()

    def _library_ready(self, apps):
        self.apps = apps
        self.setup.set_apps(apps)
        self.rebuild_library()
        self._launch_pending()

    def _launch_pending(self):
        if self.donor_mode and self.apps is not None:
            pending = cfgmod.take_pending()
            if pending:
                QTimer.singleShot(400, lambda: self.launch_id(pending))

    def all_games(self) -> list[Game]:
        games = [g for g in self.cfg.games if not g.hidden]
        if self.cfg.show_steam_games:
            for a in self.apps or []:
                if a.appid in (self.cfg.donor_appid, self.donor_id):
                    continue
                g = self.cfg.steam_game(a.appid, a.name)
                if not g.hidden:
                    games.append(g)
        return games

    def find_game(self, gid: str) -> Game | None:
        if gid.startswith("steam:"):
            appid = int(gid.split(":", 1)[1])
            a = next((x for x in self.apps or [] if x.appid == appid), None)
            return self.cfg.steam_game(appid, a.name if a else f"App {appid}")
        return self.cfg.game(gid)

    def rebuild_library(self):
        rpt = {a.appid: a.rpt for a in self.apps or []}
        entries = []
        self.cover_keys.clear()
        for g in self.all_games():
            steam = g.runner == "steam"
            sub = ""
            if steam:
                sub = _("Steam-Bibliothek")
            elif g.runner == "command":
                sub = g.exe
            elif g.exe:
                sub = Path(g.exe).name
            entries.append(dict(id=g.id, name=g.name, runner=g.runner, subtitle=sub,
                                rpt=rpt.get(g.steam_appid) if steam else None,
                                favorite=g.favorite, last_played=g.last_played,
                                source="steam" if steam else "own"))
        self.library.set_games(entries)
        for g in self.all_games():
            key, path, url = self._cover_source(g)
            self.cover_keys.setdefault(key, []).append(g.id)
            pm = self.covers.request(key, path, url)
            if pm:
                self.library.set_cover(g.id, pm)
        if self.game_is_running():
            r = (self.agent or {}).get("running") or {}
            gid = r.get("game_id") or (self.host_game.game.id if self.host_game else None)
            self.library.set_running(gid)

    def _cover_source(self, g: Game):
        if g.image:
            return f"file:{g.image}", g.image, None
        appid = g.steam_appid
        if appid:
            p = self.steam.header_image(appid) if self.steam.ok else None
            return f"app:{appid}", str(p) if p else None, \
                (Steam.header_url(appid) if appid < 0x80000000 and g.runner == "steam" else None)
        return f"none:{g.id}", None, None

    def _cover_loaded(self, key, pm):
        for gid in self.cover_keys.get(key, []):
            self.library.set_cover(gid, pm)

    def _settings_changed(self):
        self.steam = Steam(self.cfg.steam_root or None)
        self.setup.steam = self.steam
        self.scan_library()

    # ================================================================ add/edit
    def add_game(self, path: str = ""):
        g = Game(name="", exe=path, runner=guess_runner(path) if path else "native")
        dlg = GameDialog(g, self.steam, self, is_new=True)
        if dlg.exec():
            self.cfg.save_game(g)
            self.rebuild_library()
            self.toast(_('»{name}« hinzugefügt.').format(name=g.name), "ok")

    def edit_game(self, gid: str):
        g = self.find_game(gid)
        if not g:
            return
        if GameDialog(g, self.steam, self).exec():
            self.cfg.save_game(g)
            self.rebuild_library()

    def import_shortcuts(self):
        if not self.steam.ok:
            self.toast(_('Keine Steam-Installation gefunden.'), "err")
            return
        have = {(g.exe, g.args) for g in self.cfg.games}
        added = 0
        for s in self.steam.shortcuts():
            if not s.exe or (s.exe, s.launch_options) in have:
                continue
            if Path(s.exe).name == "rpt-anywhere":
                continue
            runner = guess_runner(s.exe)
            g = Game(name=s.name or Path(s.exe).stem, runner=runner, exe=s.exe,
                     args=s.launch_options, workdir=s.start_dir, steam_appid=s.appid,
                     image=s.icon if s.icon and Path(s.icon).suffix.lower()
                     in (".png", ".jpg", ".jpeg") else "")
            grid = self.steam.userdata() / "config" / "grid" if self.steam.userdata() else None
            if grid and not g.image:
                for ext in ("png", "jpg"):
                    p = grid / f"{s.appid}.{ext}"
                    if p.exists():
                        g.image = str(p)
                        break
            self.cfg.games.append(g)
            added += 1
        self.cfg.save()
        self.rebuild_library()
        self.toast(_('{added} Verknüpfung(en) importiert.').format(added=added) if added
                   else _('Keine neuen Nicht-Steam-Verknüpfungen gefunden.'), "ok" if added else "info")

    def _game_menu(self, gid: str, pos):
        g = self.find_game(gid)
        if not g:
            return
        m = QMenu(self)
        m.addAction(_('▶  Spielen'), lambda: self.launch_id(gid))
        m.addAction(_('✎  Bearbeiten …'), lambda: self.edit_game(gid))
        m.addAction(_('★  Favorit entfernen') if g.favorite else _('☆  Als Favorit'),
                    lambda: self._toggle(gid, "favorite"))
        m.addSeparator()
        if g.exe or g.runner == "steam":
            m.addAction(_('Ordner öffnen'), lambda: self._open_folder(g))
        logfile = self._log_path(g)
        if logfile.exists():
            m.addAction(_('Log anzeigen'), lambda: self.show_log(logfile))
        m.addSeparator()
        m.addAction(_('Ausblenden'), lambda: self._toggle(gid, "hidden"))
        if not gid.startswith("steam:"):
            m.addAction(_('Entfernen'), lambda: self._remove(gid))
        m.exec(pos)

    def show_log(self, path):
        self.go(3)
        self.logs.show_file(path)

    def _toggle(self, gid, attr):
        g = self.find_game(gid)
        setattr(g, attr, not getattr(g, attr))
        self.cfg.save_game(g)
        self.rebuild_library()

    def _remove(self, gid):
        g = self.cfg.game(gid)
        if g and QMessageBox.question(self, _('Entfernen'), _('»{name}« aus der Liste entfernen?\n(Es werden keine Dateien gelöscht.)').format(name=g.name)) == QMessageBox.Yes:
            self.cfg.remove_game(gid)
            self.rebuild_library()

    def _open_folder(self, g: Game):
        if g.runner == "steam":
            a = next((x for x in self.apps or [] if x.appid == g.steam_appid), None)
            if a:
                _xdg_open(a.path)
        elif g.exe and os.path.exists(os.path.expanduser(g.exe)):
            _xdg_open(Path(os.path.expanduser(g.exe)).parent)

    # drag & drop -----------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.add_game(u.toLocalFile())
                break

    # ================================================================ launching
    def _log_path(self, g: Game) -> Path:
        slug = re.sub(r"[^a-z0-9]+", "-", g.name.lower()).strip("-") or "game"
        return cfgmod.CACHE_DIR / "logs" / f"{slug}.log"

    def launch_id(self, gid: str):
        g = self.find_game(gid)
        if not g:
            self.toast(_('Spiel nicht gefunden.'), "err")
            return
        self.launch(g)

    # ================================================================ launching
    @property
    def agent(self) -> dict | None:
        return self._agent_state if self.donor_mode == "agent" else None

    def launch(self, g: Game):
        if not self.donor_mode:
            if not self.cfg.donor_appid:
                self.go(1)
                self.toast(_('Wähle zuerst ein Donor-Spiel.'), "warn")
                return
            cfgmod.write_pending(g.id)
            self.start_donor(handover_msg=_('»{name}« wird über {donor_name} gestartet …').format(name=g.name, donor_name=self.cfg.donor_name))
            return

        if self.game_is_running():
            r = QMessageBox.question(self, _('Spiel läuft'),
                                     _('»{v0}« läuft noch.\nBeenden und »{name}« starten?\n\nDeine Mitspieler bleiben verbunden.').format(v0=self.running_name(), name=g.name))
            if r != QMessageBox.Yes:
                return
            self.stop_game()
            QTimer.singleShot(1500, lambda: self._do_launch(g))
            return
        self._do_launch(g)

    def _do_launch(self, g: Game):
        st = self.agent
        try:
            p = plan(g, self.steam, self.cfg.default_proton,
                     donor=self.link.game_env(self.steam.root),
                     container=bool(st and st.get("container")))
        except LaunchError as e:
            log.error(_('Start von %s fehlgeschlagen: %s'), g.name, e)
            QMessageBox.warning(self, _('Start fehlgeschlagen'), str(e))
            return
        logfile = self._log_path(g)
        logfile.parent.mkdir(parents=True, exist_ok=True)
        with open(logfile, "w") as fh:
            fh.write(f"# {time.ctime()}\n# {' '.join(p.argv)}\n# cwd={p.cwd}\n"
                     "# " + _("gestartet von: {who}").format(who=_("Steam-Donor-Prozess") if st and p.where == "agent" else _("App (Host)")) + "\n\n")
        if st and p.where == "agent":
            self.link.send("launch", argv=p.argv, env=p.extra, cwd=p.cwd, log=str(logfile),
                           game_id=g.id, name=g.name, summary=p.summary)
            self.host_game = None
            log.info(_('Spiel an Donor-Agent übergeben: %s | %s | cwd=%s'), g.name,
                     " ".join(p.argv), p.cwd)
        else:
            try:
                self.host_game = RunningGame(g, p, logfile)
            except LaunchError as e:
                QMessageBox.warning(self, _('Start fehlgeschlagen'), str(e))
                return
            log.info(_('Spiel auf dem Host gestartet: %s | %s | Overlay=%s'), g.name,
                     " ".join(p.argv), "yes" if "gameoverlayrenderer" in p.env.get("LD_PRELOAD", "")
                     else "NO")
            if p.note:
                log.warning("%s: %s", g.name, p.note)
        self.cfg.touch(g)
        self.library.set_running(g.id)
        self.panel.set_now(g.name, p.summary + (f"\n⚠ {p.note}" if p.note else ""))
        self._launched_at = time.time()
        self._now_summary = p.summary + (f"\n⚠ {p.note}" if p.note else "")
        self.toast(_('»{name}« gestartet – lade jetzt Freunde ein.').format(name=g.name) if not p.note
                   else _('»{name}« gestartet. Hinweis: {note}').format(name=g.name, note=p.note), "ok" if not p.note else "warn")
        if self.cfg.auto_open_rpt_overlay and st:
            QTimer.singleShot(8000, self.open_overlay)

    def game_is_running(self) -> bool:
        if self.host_game and self.host_game.running:
            return True
        st = self.agent
        return bool(st and st.get("running"))

    def running_name(self) -> str:
        if self.host_game and self.host_game.running:
            return self.host_game.game.name
        st = self.agent
        return (st.get("running") or {}).get("name", "") if st else ""

    def stop_game(self):
        if self.host_game and self.host_game.running:
            self.host_game.stop()
        if self.agent:
            self.link.send("stop_game")

    def start_donor(self, handover_msg: str = ""):
        if not self.cfg.donor_appid:
            self.go(1)
            self.toast(_('Wähle zuerst ein Donor-Spiel.'), "warn")
            return
        if self.donor_mode:
            self.toast(_('Läuft bereits über Steam.'), "info")
            return
        log.info(_('Starte Donor %s (%s) über Steam'), self.cfg.donor_name, self.cfg.donor_appid)
        try:
            self.steam.run_app(self.cfg.donor_appid)
        except Exception as e:
            self.toast(str(e), "err")
            return
        self.toast(handover_msg or _('Steam startet {donor_name} …').format(donor_name=self.cfg.donor_name), "info")
        self._waiting_since = time.time()
        self.panel.set_status(_('Warte auf Steam …'), _('Steam startet »{donor_name}«. Bei Proton-Spielen kann das beim ersten Mal etwas dauern.').format(donor_name=self.cfg.donor_name),
                              S.INFO, False)

    def _wait_timeout(self):
        log.warning(_('Donor hat sich nicht innerhalb von 90 s verbunden'))
        self._waiting_since = 0.0
        self._refresh_status()
        stub = self.link.stub_log().strip()
        if not stub:
            msg = (_('Steam hat RPT Anywhere nicht gestartet – die Startoption beim Donor fehlt oder Steam hat sie noch nicht übernommen (Steam einmal neu starten).'))
        else:
            msg = (_('Der Donor-Platzhalter lief, hat sich aber wieder beendet:\n\n{v0}\n\nDetails im Reiter »Logs«.').format(v0=stub.splitlines()[-1]))
        QMessageBox.warning(self, _('Donor nicht verbunden'), msg)

    def restart_app(self):
        """Restart (e.g. after a language change). The donor session keeps running."""
        import shlex
        import subprocess
        from .pages import launcher_path
        subprocess.Popen(["/bin/sh", "-c", f"sleep 1; exec {shlex.quote(launcher_path())}"],
                         start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        self._restarting = True
        self.close()

    def emergency_reset(self):
        if QMessageBox.question(self, _('Notaus'), _('Remote-Play-Session, Agent und das zuletzt gestartete Spiel jetzt hart beenden?')) != QMessageBox.Yes:
            return
        from ..donor_link import reset
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            killed = reset(include_gui=False)
        finally:
            QApplication.restoreOverrideCursor()
        if self.host_game and self.host_game.running:
            self.host_game.stop()
        log.warning(_('Notaus ausgeführt: %s'), "; ".join(killed) or _('nichts lief'))
        self._waiting_since = 0.0
        self.toast(_('Notaus: {v0} Prozess(e) beendet.').format(v0=len(killed)) if killed
                   else _('Notaus: Es lief nichts mehr.'), "ok")

    def stop_session(self):
        if self.agent:
            self.link.send("end_session")
        self.link.stop_session()
        self.toast(_('Remote-Play-Session wird beendet …'), "info")

    # ================================================================ donor link
    def _poll_donor(self):
        try:
            self.link.heartbeat()
        except OSError:
            pass
        st = self.link.agent_state()
        mode = "agent" if st else ("stub" if self.link.refresh() else None)
        self._steam_check = getattr(self, "_steam_check", 0) + 1
        if mode and self._steam_check % 5 == 0 and self.steam.ok and not self.steam.is_running():
            self._steam_dead = getattr(self, "_steam_dead", 0) + 1
            if self._steam_dead >= 2:
                log.warning(_('Steam läuft nicht mehr, Donor-Prozess aber schon – räume auf'))
                from ..donor_link import reset
                reset(include_gui=False, wait=2.0)
                self._steam_dead = 0
                st, mode = None, None
        elif self._steam_check % 5 == 0:
            self._steam_dead = 0
        self._agent_state = st
        if mode and mode != self.donor_mode:
            first = not self.donor_mode
            self.donor_mode = mode
            self.donor_id = (st or {}).get("appid") or self.link.appid
            self._waiting_since = 0.0
            if mode == "agent":
                log.info(_('Donor-Agent %s verbunden: AppID %s, Container=%s, Steam-API=%s %s'),
                         st.get("version"), self.donor_id, st.get("container"),
                         "ok" if st.get("steam_ok") else "NEIN", st.get("steam_error", ""))
                log.info(_('Steam-Variablen im Donor-Prozess: %s'), ", ".join(st.get("env_keys", [])))
                self._seen_events = {e["id"] for e in st.get("events", [])}
                if st.get("version") != __version__:
                    self.toast(_('Achtung: Donor-Agent hat Version {v0}, App {version__}. Steam/Donor neu starten.').format(v0=st.get('version'), version__=__version__), "warn")
            else:
                log.warning(_('Donor ohne Agent verbunden (Notbetrieb, kein python3 im Steam-Prozess?) – Spiele starten auf dem Host'))
            if st and st.get("steam_ok"):
                self.panel.set_invites_enabled(True)
            elif st:
                self.panel.set_invites_enabled(False, _('Steam-API im Donor nicht verfügbar: {v0}\nEinladen geht über Shift+Tab im Spiel.').format(v0=st.get('steam_error')))
            else:
                self.panel.set_invites_enabled(False, _('Notbetrieb ohne Agent – lade über Shift+Tab im Spiel ein. Details: Logs.'))
            self._refresh_status()
            if self.apps is not None:
                self.rebuild_library()
            if first:
                self.toast(_('Mit Steam verbunden – Remote Play Together ist bereit ✓'), "ok")
                self._launch_pending()
        elif not mode and self.donor_mode:
            log.info(_('Donor-Session beendet'))
            self.donor_mode = None
            self.donor_id = 0
            self.panel.set_players([])
            self.panel.set_invites_enabled(False, _('Session beendet. Klick auf ein Spiel, um eine neue zu starten.'))
            self._refresh_status()
        elif self._waiting_since and time.time() - self._waiting_since > 90:
            self._wait_timeout()
        if st:
            self._apply_agent_state(st)

    def _apply_agent_state(self, st: dict):
        from types import SimpleNamespace
        friends = [SimpleNamespace(steamid=int(f["steamid"]), name=f["name"],
                                   state_text=f["state_text"], online=f["online"],
                                   playing_appid=f["playing"]) for f in st.get("friends", [])]
        if self.panel.set_friends(friends) or self._avatar_tick % 15 == 0:
            for sid, row in self.panel.rows.items():
                a = self.link.avatar(sid)
                if a:
                    row.set_avatar(avatar_from_rgba(*a, size=36))
        self._avatar_tick += 1
        self.panel.set_players([{"name": s["name"], "device": _("Gast") if s.get("guest") else ""}
                                for s in st.get("sessions", [])])
        if st.get("me"):
            self.user_label.setText(_('Angemeldet als {v0}').format(v0=st['me']))
        if st.get("guest_link") and st.get("guest_link_ts") != self._guest_ts:
            self._guest_ts = st.get("guest_link_ts")
            if self._guest_requested:
                self.panel.show_link(st["guest_link"])
                QApplication.clipboard().setText(st["guest_link"])
                self.toast(_('Gast-Link erstellt und kopiert ✓'), "ok")
                self._guest_requested = 0.0
        for e in st.get("events", []):
            if e["id"] in self._seen_events:
                continue
            self._seen_events.add(e["id"])
            k = e["kind"]
            if k == "joined":
                self.toast(e["text"], "ok")
            elif k == "invite_ok":
                row = self.panel.rows.get(int(e["text"]))
                self.toast(_('Einladung an {v0} gesendet ✓').format(v0=row.name if row else _('Freund')), "ok")
            elif k == "invite_fail":
                self.toast(_('Steam hat die Einladung abgelehnt.'), "err")
            elif k == "error":
                self.toast(e["text"], "err")
        if self._guest_requested and time.time() - self._guest_requested > 15:
            self._guest_requested = 0.0
            log.warning(_('Kein Gast-Link nach 15 s erhalten'))
            self.link.send("overlay")
            self.toast(_("Steam hat keinen Gast-Link geliefert – Steams Remote-Play-Together-Fenster "
                         "wurde geöffnet, dort kannst du den Link erstellen (sonst: Shift+Tab im Spiel)."),
                       "warn")

    def _tick(self):
        self._poll_donor()
        running = self.game_is_running()
        if self._was_running and not running and time.time() - self._launched_at > 3:
            name = self._last_name
            self.library.set_running(None)
            self.panel.set_now(None)
            self.host_game = None
            self.toast(_('»{name}« beendet. Mitspieler bleiben verbunden – starte einfach das nächste Spiel.').format(name=name), "info")
        if running:
            self._last_name = self.running_name()
            r = (self.agent or {}).get("running") or {}
            started = r.get("started") or (self.host_game.started if self.host_game else 0)
            if started:
                dur = int(time.time() - started)
                cur = getattr(self, "_now_summary", "")
                self.panel.now_info.setText(_('{cur}\nläuft seit {v1}:{v2:02d}').format(cur=cur, v1=dur // 60, v2=dur % 60))
        if running or time.time() - self._launched_at > 3:
            self._was_running = running

    # ================================================================ invites
    def invite_friend(self, steamid: int):
        if not self.agent:
            return
        if not self.game_is_running():
            self.toast(_('Tipp: Starte zuerst ein Spiel – sonst sehen Freunde nur einen leeren Stream.'), "warn")
        self.link.send("invite", steamid=str(steamid))
        row = self.panel.rows.get(steamid)
        if row:
            row.mark_invited()
        log.info(_('Einladung an %s angefordert'), steamid)

    def guest_link(self):
        if not self.agent:
            return
        self._guest_requested = time.time()
        self.link.send("guest")
        log.info(_('Gast-Link angefordert'))
        self.toast(_('Gast-Link wird erstellt …'), "info")

    def open_overlay(self):
        if self.agent:
            self.link.send("overlay")

    # ================================================================ status
    def _refresh_status(self):
        cfg = self.cfg
        if cfg.donor_appid:
            self.donor_label.setText(cfg.donor_name)
            self.donor_dot.set_color(S.OK if self.donor_mode else S.WARN)
        else:
            self.donor_label.setText(_('nicht gewählt'))
            self.donor_dot.set_color(S.ERR)
        u = self.steam.current_user() if self.steam.ok else None
        if u and not self.agent:
            self.user_label.setText(_('Steam-Konto: {v0}').format(v0=u['persona']) if u["persona"] else "")
        if self._waiting_since and not self.donor_mode:
            return
        if self.donor_mode:
            name = cfg.donor_name if self.donor_id == cfg.donor_appid else f"AppID {self.donor_id}"
            if self.donor_mode == "agent":
                self.panel.set_status(_('Host aktiv'), _('Steam streamt alles, was du hier startest, als »{name}«. Spiele kannst du wechseln, ohne dass Mitspieler rausfliegen.').format(name=name), S.OK, True, _('Session beenden'))
            else:
                self.panel.set_status(_('Host aktiv (Notbetrieb)'), _('Verbunden mit »{name}«, aber ohne Agent im Steam-Prozess – Overlay/Einladen können fehlen. Siehe Logs → Donor-Platzhalter.').format(name=name), S.WARN, True,
                                      _('Session beenden'))
            try:
                self.panel.donor_btn.clicked.disconnect()
            except RuntimeError:
                pass
            self.panel.donor_btn.clicked.connect(self.stop_session)
        elif not cfg.donor_appid:
            self.panel.set_status(_('Nicht eingerichtet'), _('Wähle unter »Einrichtung« ein Donor-Spiel, um loszulegen.'), S.ERR, True, _('Zur Einrichtung'))
            try:
                self.panel.donor_btn.clicked.disconnect()
            except RuntimeError:
                pass
            self.panel.donor_btn.clicked.connect(lambda: self.go(1))
        else:
            self.panel.set_status(_('Bereit'), _('Klick auf ein Spiel – Steam startet dann »{donor_name}«, und die App verbindet sich automatisch.').format(donor_name=cfg.donor_name),
                                  S.WARN, True, _('Über Steam starten'))
            try:
                self.panel.donor_btn.clicked.disconnect()
            except RuntimeError:
                pass
            self.panel.donor_btn.clicked.connect(self.panel.start_donor)

    def closeEvent(self, e):
        if getattr(self, "_restarting", False):
            super().closeEvent(e)      # restart: keep the donor session and the game alive
            return
        if self.game_is_running():
            r = QMessageBox.question(self, _('Beenden?'), _('»{v0}« läuft noch.\nSpiel und Remote-Play-Session ebenfalls beenden?\n\n(Nein = Spiel läuft weiter, Session endet mit dem Spiel)').format(v0=self.running_name()),
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                e.ignore()
                return
            if r == QMessageBox.Yes:
                self.stop_game()
                self.stop_session()
        elif self.donor_mode:
            self.stop_session()
        super().closeEvent(e)


def _xdg_open(p):
    import subprocess
    from ..steam_lib import clean_env
    subprocess.Popen(["xdg-open", str(p)], env=clean_env(), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
