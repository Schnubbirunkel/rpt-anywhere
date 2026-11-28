"""Remote Play Whatever (RPW) as the Remote Play engine for Steam games.

RPW (https://github.com/Casual-Gamer8001/RemotePlayWhatever, MIT) asks the running
Steam client – through Steam's own internal functions – to start a Remote Play
Together session for *whatever game Steam is running*, under that game's own AppID.
So Steam games are started normally by Steam (with their DRM, launch options and
mods) and no donor is needed. RPT Anywhere drives RPW's window-less ``--serve`` mode
(docs/serve-protocol.md in the RPW repo): plain-word commands on stdin, one JSON
object per line on stdout.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, QTimer, Signal

from . import config as cfgmod
from .i18n import _

log = logging.getLogger("rpt")

REPO = "Casual-Gamer8001/RemotePlayWhatever"
ASSET = "RemotePlayWhatever-linux-x86_64.AppImage"
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest/download/{ASSET}"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
HOMEPAGE = f"https://github.com/{REPO}"


def managed_path() -> Path:
    """Where RPT Anywhere keeps its own copy of the AppImage."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "rpt-anywhere" / ASSET


def find_appimage(configured: str = "") -> Path | None:
    """The RPW AppImage: configured path, our managed copy, or a copy the user downloaded."""
    cands: list[Path] = []
    if configured:
        cands.append(Path(configured).expanduser())
    cands.append(managed_path())
    home = Path.home()
    for d in (home / "Applications", home / "Downloads", home / ".local" / "bin", home / "bin", home):
        try:
            cands += sorted(d.glob("RemotePlayWhatever*.AppImage"), key=lambda p: -p.stat().st_mtime)
        except OSError:
            pass
    exe = shutil.which("RemotePlayWhatever")
    if exe:
        cands.append(Path(exe))
    for c in cands:
        try:
            if c.is_file() and c.stat().st_size > 100_000:
                return c
        except OSError:
            continue
    return None


def latest_release() -> dict:
    """{"tag": "...", "published": "..."} of RPW's newest release (raises on network errors)."""
    req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json",
                                                        "User-Agent": "rpt-anywhere"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    return {"tag": d.get("tag_name", ""), "published": d.get("published_at", "")}


def download(progress=None) -> Path:
    """Download the newest RPW AppImage into managed_path() (atomic, made executable)."""
    dest = managed_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "rpt-anywhere"})
    with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if tmp.stat().st_size < 100_000 or tmp.read_bytes()[1:4] != b"ELF":
        tmp.unlink(missing_ok=True)
        raise OSError(_('Die heruntergeladene Datei ist kein AppImage.'))
    tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    tmp.replace(dest)
    try:
        info = latest_release()
        (dest.parent / "rpw-version.json").write_text(json.dumps(info))
    except Exception:
        pass
    return dest


def installed_release() -> dict:
    try:
        return json.loads((managed_path().parent / "rpw-version.json").read_text())
    except Exception:
        return {}


class Downloader(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(str)
    failed = Signal(str)

    def run(self):
        try:
            p = download(lambda d, t: self.progress.emit(d, t))
        except Exception as e:
            self.failed.emit(str(e))
        else:
            self.finished_ok.emit(str(p))


class RPW(QObject):
    """Runs ``RemotePlayWhatever --serve`` and keeps the latest state.

    Signals carry already parsed data; ``status`` is RPW's status object
    (game, session, link, guests, waiting)."""
    ready = Signal(dict)
    status = Signal(dict)
    friends = Signal(list)
    invite = Signal(dict)
    joined = Signal(dict)
    left = Signal(dict)
    notice = Signal(str)
    error = Signal(dict)
    stopped = Signal(str)

    RETRY_S = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path: Path | None = None
        self.proc: QProcess | None = None
        self.is_ready = False
        self.me = ""
        self.state: dict = {}
        self._buf = b""
        self._err = b""
        self._extract = False          # APPIMAGE_EXTRACT_AND_RUN (no FUSE)
        self._want = False
        self._started = 0.0
        self._last_error = ""
        self._poll = QTimer(self, interval=2000, timeout=lambda: self.send("status"))
        self._retry = QTimer(self, singleShot=True, timeout=self._start)

    # ------------------------------------------------------------ lifecycle
    def start(self, path: Path):
        self.path = Path(path)
        self._want = True
        if not self.running:
            self._start()

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.state() != QProcess.NotRunning)

    @property
    def last_error(self) -> str:
        return self._last_error

    def _start(self):
        if not self._want or not self.path or self.running:
            return
        self.is_ready = False
        self._buf = self._err = b""
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        # RPT Anywhere's own Qt/Python environment must not leak into the AppImage
        for k in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                  "LD_PRELOAD", "LD_LIBRARY_PATH"):
            env.remove(k)
        if self._extract:
            env.insert("APPIMAGE_EXTRACT_AND_RUN", "1")
        p.setProcessEnvironment(env)
        p.setProgram(str(self.path))
        p.setArguments(["--serve"])
        p.readyReadStandardOutput.connect(self._read)
        p.readyReadStandardError.connect(self._read_err)
        p.finished.connect(self._finished)
        p.errorOccurred.connect(self._proc_error)
        self.proc = p
        self._started = time.time()
        home = os.path.expanduser("~")
        shown = "~" + str(self.path)[len(home):] if str(self.path).startswith(home + "/") else self.path
        log.info(_('Starte Remote Play Whatever: %s --serve'), shown)
        p.start()

    def stop(self, end_session: bool = False):
        """Stop RPW. end_session=False keeps a running Remote Play session alive in Steam."""
        self._want = False
        self._retry.stop()
        self._poll.stop()
        if not self.running:
            return
        if end_session:
            self.send("end")
        self.send("quit")
        if not self.proc.waitForFinished(3000):
            self.proc.kill()
            self.proc.waitForFinished(1000)

    def send(self, cmd: str):
        if self.running and self.is_ready or cmd in ("quit",) and self.running:
            self.proc.write((cmd + "\n").encode())

    # ------------------------------------------------------------ commands
    def request_link(self):
        self.send("link")

    def invite_friend(self, steamid: int):
        self.send(f"invite {int(steamid)}")

    def end_session(self):
        self.send("end")

    def remove_guest(self, key: str):
        self.send(f"remove {key}")

    def set_input(self, key: str, keyboard: bool, mouse: bool, controller: bool):
        self.send(f"input {key} {int(keyboard)} {int(mouse)} {int(controller)}")

    def request_friends(self):
        self.send("friends")

    # ------------------------------------------------------------ state helpers
    @property
    def game(self) -> dict | None:
        return self.state.get("game") or None

    @property
    def game_appid(self) -> int:
        g = self.game
        try:
            return int(g["id"]) & 0xFFFFFF if g and g.get("steam") else 0
        except (KeyError, ValueError):
            return 0

    # ------------------------------------------------------------ io
    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                log.debug("RPW: %s", line[:300])
                continue
            self._handle(msg)

    def _read_err(self):
        data = bytes(self.proc.readAllStandardError())
        self._err = (self._err + data)[-8000:]
        for line in data.decode(errors="replace").splitlines():
            if line.strip():
                log.debug("RPW stderr: %s", line)

    def _handle(self, m: dict):
        ev = m.get("event")
        if ev == "ready":
            self.is_ready = True
            self.me = m.get("name", "")
            self._last_error = ""
            log.info(_('Remote Play Whatever bereit (Build %s, Protokoll %s), angemeldet als %s'),
                     m.get("build"), m.get("protocol"), self.me)
            self.ready.emit(m)
            self.send("status")
            self.send("friends")
            self._poll.start()
        elif ev == "status":
            self.state = m
            self.status.emit(m)
        elif ev == "friends":
            self.friends.emit(m.get("friends", []))
        elif ev == "invite":
            if m.get("result") == "ok":
                log.info(_('RPW: Einladung ok (%s) %s'), m.get("steamid"), m.get("url", ""))
            else:
                log.warning(_('RPW: Einladung fehlgeschlagen (%s): %s'), m.get("code"), m.get("message"))
            self.invite.emit(m)
            self.send("status")
        elif ev == "joined":
            log.info(_('RPW: %s ist beigetreten'), m.get("name") or m.get("steamid"))
            self.joined.emit(m)
            self.send("status")
        elif ev == "left":
            log.info(_('RPW: %s hat die Session verlassen'), m.get("name") or m.get("steamid"))
            self.left.emit(m)
            self.send("status")
        elif ev in ("notice", "group-failed"):
            log.info("RPW: %s", m.get("message"))
            self.notice.emit(m.get("message", ""))
        elif ev == "ended":
            self.send("status")
        elif ev == "error":
            self._last_error = m.get("message") or m.get("error", "")
            log.warning(_('RPW-Fehler: %s'), self._last_error)
            self.error.emit(m)

    def _proc_error(self, err):
        if err == QProcess.FailedToStart:
            self._last_error = _('Remote Play Whatever ließ sich nicht starten ({path}).').format(path=self.path)
            log.error("%s", self._last_error)

    def _finished(self, code, _status):
        self._poll.stop()
        was_ready = self.is_ready
        self.is_ready = False
        err = self._err.decode(errors="replace")
        if not self._extract and "fuse" in err.lower() and not was_ready:
            log.warning(_('AppImage braucht FUSE – starte RPW im Entpack-Modus'))
            self._extract = True
            QTimer.singleShot(200, self._start)
            return
        if err.strip() and not was_ready and not self._last_error:
            self._last_error = err.strip().splitlines()[-1][:300]
        log.info(_('Remote Play Whatever beendet (Code %s)'), code)
        self.state = {}
        self.stopped.emit(self._last_error)
        if self._want:
            self._retry.start(self.RETRY_S * 1000)
