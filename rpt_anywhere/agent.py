"""Donor agent – runs *inside* the process Steam started for the donor game.

Started by bin/rpt-anywhere when Steam launches the donor (possibly inside Steam's
runtime container, where only the Python standard library is available – so this
module and everything it imports must not use Qt).

Being part of Steam's own process tree is what makes the overlay, Remote Play
streaming and invites work: games are started as our children, exactly like
Steam would start the donor game itself.

Protocol (all files in ~/.cache/rpt-anywhere/donor):
  state.json        written by the agent every second
  req/*.json        requests from the GUI (processed in name order, then deleted)
  host.alive        GUI heartbeat (unix time)
  avatars/<id>.rgba avatar pixels: 8-byte header (w, h as uint32 LE) + RGBA
"""
from __future__ import annotations

import json
import os
import signal
import struct
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import __version__
from .i18n import _
from .config import CACHE_DIR, Config

DONOR_DIR = CACHE_DIR / "donor"
REQ_DIR = DONOR_DIR / "req"
AVATAR_DIR = DONOR_DIR / "avatars"
LOG = DONOR_DIR / "stub.log"


def log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} [agent] {msg}\n")
    except OSError:
        pass


def write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def base_game_env() -> dict:
    """Steam's environment exactly as it was handed to the donor game."""
    env = dict(os.environ)
    for k in ("LD_PRELOAD", "LD_LIBRARY_PATH", "PATH"):
        saved = env.pop(f"RPTA_GAME_{k}", None)
        if saved is not None:
            if saved:
                env[k] = saved
            else:
                env.pop(k, None)
    for k in list(env):
        if k.startswith("RPTA_") or k in ("PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE",
                                          "PYTHONDONTWRITEBYTECODE"):
            env.pop(k, None)
    return env


# Wine/Proton/runtime helpers that may linger after the actual game has exited.
INFRA = {"wineserver", "wineserver64", "winedevice.exe", "services.exe", "plugplay.exe",
         "explorer.exe", "rpcss.exe", "svchost.exe", "conhost.exe", "tabtip.exe",
         "start.exe", "steam.exe", "winedbg", "wine", "wine64", "wine-preloader",
         "wine64-preloader", "pv-adverb", "srt-bwrap", "bwrap", "pressure-vessel",
         "reaper", "steam-runtime-l", "python3", "proton", "sh", "bash", "sleep",
         "crashpad_handle", "mscorsvw.exe", "rundll32.exe", "winemenubuilder"}




def become_subreaper() -> bool:
    """Orphaned grandchildren (games whose launcher exits, Wine helpers) are re-parented
    to us instead of init, so we can keep track of them – like Steam's own 'reaper'."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_CHILD_SUBREAPER = 36
        return libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except Exception:
        return False


def descendants(root: int) -> list[tuple[int, str]]:
    """All live (non-zombie) descendants of *root* as (pid, comm)."""
    children: dict[int, list[int]] = {}
    comm: dict[int, str] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for d in entries:
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as fh:
                raw = fh.read()
            parts = raw.rsplit(")", 1)[1].split()
            if parts[0] == "Z":
                continue
            pid = int(d)
            comm[pid] = raw[raw.index("(") + 1:raw.rindex(")")]
            children.setdefault(int(parts[1]), []).append(pid)
        except (OSError, IndexError, ValueError):
            continue
    out, todo = [], list(children.get(root, []))
    while todo:
        pid = todo.pop()
        if pid in comm:
            out.append((pid, comm[pid]))
        todo.extend(children.get(pid, []))
    return out






class Agent:
    def __init__(self):
        self.in_container = os.environ.get("RPTA_IN_CONTAINER") == "1"
        self.appid = 0
        for k in ("SteamAppId", "SteamGameId"):
            v = os.environ.get(k, "")
            if v.isdigit() and int(v):
                self.appid = int(v) & 0xFFFFFF
                break
        self.sw = None
        self.steam_error = ""
        self.friends: list[dict] = []
        self.sessions: list[dict] = []
        self.me = ""
        self.guest_link = ""
        self.guest_link_ts = 0.0
        self.guest_requested = 0.0
        self.events: list[dict] = []
        self.event_id = 0
        self.proc: subprocess.Popen | None = None
        self.running: dict | None = None
        self.host_seen = False
        self.started = time.time()
        self.stop = False
        self.avatar_done: set[tuple[int, int]] = set()
        self.idle_since = 0.0          # only leftovers alive since …
        self.steam_gone_since = 0.0
        self.orphan_since = 0.0
        REQ_DIR.mkdir(parents=True, exist_ok=True)
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ steam
    def init_steam(self) -> None:
        from .steamworks import Steamworks, SteamworksError
        from .steam_lib import Steam, libsteam_api_score
        cfg = Config.load()
        lib = cfg.libsteam_api if cfg.libsteam_api and Path(cfg.libsteam_api).exists() else ""
        try:
            libs = Steam(cfg.steam_root or None).find_libsteam_api()
        except Exception as e:  # pragma: no cover
            libs = []
            log(_('Suche nach libsteam_api.so fehlgeschlagen: {e}').format(e=e))
        # use the best library available (newest Remote Play interface), not just the saved one
        if libs and (not lib or (libsteam_api_score(Path(lib)) or ()) < (libsteam_api_score(libs[0]) or ())):
            if lib:
                log(_("Bessere libsteam_api.so gefunden: {new} (statt {old})").format(new=libs[0], old=lib))
            lib = str(libs[0])
        if not lib:
            self.steam_error = _('Keine libsteam_api.so gefunden (Einrichtung ③).')
            log(self.steam_error)
            return
        sw = Steamworks(lib)
        try:
            sw.init()
        except SteamworksError as e:
            self.steam_error = str(e)
            log(_('Steam-API: {e}').format(e=e))
            return
        self.sw = sw
        self.me = sw.my_name()
        log(_("Remote-Play-Schnittstelle v{v}, Callbacks: {mode}").format(
            v=sw.remote_version, mode=sw.callback_mode))
        log(_('Steam-API bereit ({lib}), AppID {app_id}, angemeldet als {me}').format(lib=lib, app_id=sw.app_id, me=self.me))

    def event(self, kind: str, text: str) -> None:
        self.event_id += 1
        self.events = (self.events + [{"id": self.event_id, "kind": kind, "text": text}])[-20:]

    def refresh_friends(self) -> None:
        if not self.sw:
            return
        try:
            fl = self.sw.list_friends()
        except Exception as e:
            log(_("Freundesliste: {e}").format(e=e))
            return
        out = []
        for f in fl:
            out.append({"steamid": str(f.steamid), "name": f.name, "state": f.state,
                        "state_text": f.state_text, "online": f.online,
                        "playing": f.playing_appid})
            key = (f.steamid, f.avatar_handle)
            if f.avatar_handle and key not in self.avatar_done:
                try:
                    rgba = self.sw.avatar_rgba(f.avatar_handle)
                except Exception:
                    rgba = None
                if rgba:
                    w, h, data = rgba
                    tmp = AVATAR_DIR / f"{f.steamid}.tmp"
                    tmp.write_bytes(struct.pack("<II", w, h) + data)
                    os.replace(tmp, AVATAR_DIR / f"{f.steamid}.rgba")
                    self.avatar_done.add(key)
        self.friends = out

    def refresh_sessions(self) -> None:
        if not self.sw:
            return
        try:
            names = {f["steamid"]: f["name"] for f in self.friends}
            self.sessions = [{"id": s.session_id, "steamid": str(s.steamid),
                              "name": s.client_name or names.get(str(s.steamid), _("Gast")),
                              "guest": not s.steamid}
                             for s in self.sw.sessions()]
        except Exception as e:
            log(f"Sessions: {e}")

    def pump(self) -> None:
        if not self.sw:
            return
        try:
            events = self.sw.poll_events()
        except Exception as e:
            log(f"Callbacks: {e}")
            return
        for kind, payload in events:
            if kind == "guest_link":
                self.guest_link = str(payload)
                self.guest_link_ts = time.time()
                log(_('Gast-Link: {guest_link}').format(guest_link=self.guest_link))
                self.event("guest_link", self.guest_link)
            elif kind == "session_connected":
                log(_("Gast verbunden (Session {sid}), Spiel läuft: {running}").format(
                    sid=payload, running=_("ja") if self.game_running() else _("nein")))
                self.log_processes(_("Gast verbunden"))
                self.refresh_sessions()
                self.event("joined", _('Ein Mitspieler ist beigetreten 🎮'))
            elif kind == "session_disconnected":
                log(_("Gast getrennt (Session {sid})").format(sid=payload))
                self.refresh_sessions()
                self.event("left", _('Ein Mitspieler hat die Session verlassen'))

    # ------------------------------------------------------------------ games
    def launch(self, req: dict) -> None:
        if self.game_running():
            self.stop_game()
        env = base_game_env()
        env.update({str(k): str(v) for k, v in (req.get("env") or {}).items()})
        cwd = req.get("cwd") or None
        if cwd and not os.path.isdir(cwd):
            cwd = None
        logp = req.get("log")
        out = open(logp, "ab") if logp else subprocess.DEVNULL
        try:
            self.proc = subprocess.Popen(req["argv"], env=env, cwd=cwd, stdout=out, stderr=out,
                                         stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            log(_('Start fehlgeschlagen: {e}').format(e=e))
            self.event("error", _('Start fehlgeschlagen: {e}').format(e=e))
            self.running = None
            return
        finally:
            if logp:
                out.close()
        self.running = {"game_id": req.get("game_id"), "name": req.get("name", "?"),
                        "pid": self.proc.pid, "started": time.time(),
                        "summary": req.get("summary", "")}
        log(_('Spiel gestartet: {v0} pid={pid} argv={v2}').format(v0=req.get('name'), pid=self.proc.pid, v2=req['argv']))
        log(_("Overlay fürs Streaming: LD_PRELOAD={pre}, Vulkan-Layer={vk}, SteamGameId={gid}").format(
            pre=_("ja") if "gameoverlayrenderer" in env.get("LD_PRELOAD", "") else _("FEHLT"),
            vk=env.get("ENABLE_VK_LAYER_VALVE_steam_overlay_1", _("FEHLT")),
            gid=env.get("SteamGameId", "-")))

    def reap(self) -> None:
        """Collect exit status of re-parented children (avoid zombies)."""
        if self.proc and self.proc.returncode is None:
            self.proc.poll()
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return
            if self.proc and pid == self.proc.pid and self.proc.returncode is None:
                self.proc.returncode = os.waitstatus_to_exitcode(status) \
                    if hasattr(os, "waitstatus_to_exitcode") else status

    def log_processes(self, why: str) -> None:
        """Write the game's process tree to the log (which process has which program)."""
        rows = []
        for pid, comm in descendants(os.getpid()):
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    args = [a.decode(errors="replace") for a in fh.read().split(b"\0") if a]
            except OSError:
                args = []
            exe = next((a for a in args if a.lower().endswith((".exe", ".x86_64"))), "")
            if not exe and args:
                exe = args[0]
            exe = exe.replace("\\", "/").rsplit("/", 1)[-1]
            rows.append(f"{pid} {comm}" + (f" [{exe}]" if exe and exe != comm else ""))
        log(_("Prozesse ({why}): {procs}").format(why=why, procs=", ".join(rows) or "-"))

    def real_processes(self) -> list[tuple[int, str]]:
        """Descendants of the agent that are not Wine/Proton/runtime helpers."""
        return [(p, c) for p, c in descendants(os.getpid()) if c.lower() not in INFRA]

    def game_running(self) -> bool:
        """True while the game runs. The launched process (for Proton: the proton script,
        which waits for the whole Wine session) counts as running as long as it lives –
        it is never killed automatically. Only when it has exited and nothing but
        Wine/Proton helpers is left (for 15 s), those leftovers are cleaned up."""
        if not self.proc:
            return False
        if self.proc.poll() is None:
            self.idle_since = 0.0
            return True
        procs = descendants(os.getpid())
        if not procs:
            return False
        if any(c.lower() not in INFRA for _p, c in procs):
            self.idle_since = 0.0
            return True                      # launcher-style game: main exited, game lives on
        if not self.idle_since:
            self.idle_since = time.time()
            return True
        if time.time() - self.idle_since < 15:
            return True
        log(_('Nur noch Hintergrundprozesse übrig ({v0}) – räume auf').format(
            v0=', '.join(c for _p, c in procs)))
        self.kill_tree()
        return False

    def kill_tree(self) -> None:
        """Terminate everything this agent started (also processes in other groups)."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            procs = descendants(os.getpid())
            if self.proc:
                try:
                    os.killpg(self.proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            for pid, _c in procs:
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            for _i in range(30):
                if not descendants(os.getpid()):
                    if self.proc:
                        self.proc.poll()
                    return
                time.sleep(0.1)

    def stop_game(self) -> None:
        if not self.proc and not descendants(os.getpid()):
            return
        log(_('Beende Spiel pid={pid}').format(pid=self.proc.pid if self.proc else "-"))
        self.kill_tree()

    # ------------------------------------------------------------------ requests
    def handle(self, req: dict) -> None:
        cmd = req.get("cmd")
        if cmd == "launch":
            self.launch(req)
        elif cmd == "stop_game":
            self.stop_game()
        elif cmd == "end_session":
            log(_('Session-Ende angefordert'))
            self.stop_game()
            self.stop = True
        elif cmd == "invite" and self.sw:
            sid = int(req.get("steamid", 0))
            ok = self.sw.invite(sid)
            log(_('Einladung an {sid}: {v1}').format(sid=sid, v1='ok' if ok else 'abgelehnt'))
            self.event("invite_ok" if ok else "invite_fail", str(sid))
        elif cmd == "guest" and self.sw:
            self.guest_requested = time.time()
            ok = self.sw.invite(0)
            log(_('Gast-Link angefordert: {v0}').format(v0='ok' if ok else 'abgelehnt'))
            if not ok:
                self.event("error", _('Steam hat den Gast-Link abgelehnt.'))
        elif cmd == "overlay" and self.sw:
            log(_("Remote-Play-Together-Fenster von Steam geöffnet: {ok}").format(ok=self.sw.start_rpt_ui()))
        elif cmd == "friends":
            self.refresh_friends()
        elif cmd in ("invite", "guest", "overlay"):
            self.event("error", _('Steam-API nicht verfügbar: {steam_error}').format(steam_error=self.steam_error))

    def process_requests(self) -> None:
        try:
            files = sorted(REQ_DIR.glob("*.json"))
        except OSError:
            return
        for f in files:
            try:
                req = json.loads(f.read_text())
            except Exception:
                req = None
            try:
                f.unlink()
            except OSError:
                pass
            if req:
                try:
                    self.handle(req)
                except Exception:
                    log(_('Fehler bei Anfrage:\n') + traceback.format_exc())

    # ------------------------------------------------------------------ main loop
    def host_fresh(self, max_age: float) -> bool:
        try:
            t = int((DONOR_DIR / "host.alive").read_text().strip())
        except Exception:
            return False
        return time.time() - t < max_age

    def write_state(self) -> None:
        running = self.running if self.game_running() else None
        if self.running and not running:
            log(_('Spiel beendet: {v0}').format(v0=self.running.get('name')))
            self.event("game_ended", self.running.get("name", ""))
            self.running = None
            self.proc = None
        write_json(DONOR_DIR / "state.json", {
            "version": __version__, "ts": time.time(), "pid": os.getpid(),
            "appid": self.appid, "container": self.in_container,
            "steam_ok": self.sw is not None, "steam_error": self.steam_error,
            "can_start_rpt": bool(self.sw and self.sw.can_start_rpt),
            "me": self.me, "friends": self.friends, "sessions": self.sessions,
            "guest_link": self.guest_link, "guest_link_ts": self.guest_link_ts,
            "running": running, "events": self.events,
            "env_keys": sorted(k for k in base_game_env() if k.startswith(("Steam", "LD_PRE",
                                                                         "ENABLE_VK", "SDL_"))),
        })

    def run(self) -> int:
        log(_('Agent {version__} gestartet (python {v1}, container={in_container}, AppID {appid})').format(version__=__version__, v1=sys.version.split()[0], in_container=self.in_container, appid=self.appid))
        try:
            (DONOR_DIR / "stop").unlink()
        except OSError:
            pass
        log(_("Subreaper aktiv: {ok}").format(ok=become_subreaper()))
        self.init_steam()
        self.refresh_friends()
        tick = 0
        while not self.stop:
            self.reap()
            self.pump()
            if tick % 5 == 0:
                self.process_requests()
            if tick % 10 == 0:
                if self.sw and self.running and not self.running.get("appid_warned") \
                        and time.time() - self.running.get("started", 0) > 15:
                    other = self.sw.playing_appid()
                    if other and self.appid and other != int(self.appid):
                        self.running["appid_warned"] = True
                        msg = _("»{name}« hat sich bei Steam mit eigener AppID {other} angemeldet (statt {donor}). Steam ordnet sein Bild deshalb nicht dem Donor zu – Gäste sehen nur „Bitte warten“. Mit dem Donor-Weg lässt sich dieses Spiel nicht streamen.").format(
                            name=self.running.get("name", "?"), other=other, donor=self.appid)
                        log(msg)
                        self.event("warn", msg)
                if (self.running and not self.running.get("procs_logged")
                        and time.time() - self.running.get("started", 0) > 20):
                    self.running["procs_logged"] = True
                    self.log_processes(_("20 s nach Spielstart"))
                if (DONOR_DIR / "stop").exists():
                    self.handle({"cmd": "end_session"})
                    break
                if self.sw and not self.sw.steam_running():
                    if not self.steam_gone_since:
                        self.steam_gone_since = time.time()
                    elif time.time() - self.steam_gone_since > 5:
                        log(_('Steam wurde beendet – Session endet'))
                        self.stop_game()
                        break
                else:
                    self.steam_gone_since = 0.0
                if self.host_fresh(15):
                    self.host_seen = True
                    self.orphan_since = 0.0
                elif self.host_seen and not self.game_running():
                    log(_('App beendet – Session endet'))
                    break
                elif self.host_seen and not self.real_processes():
                    # app closed, and only Wine/Proton helpers are left (e.g. a hanging
                    # wineserver keeps the proton script alive) -> clean up after 30 s
                    if not self.orphan_since:
                        self.orphan_since = time.time()
                    elif time.time() - self.orphan_since > 30:
                        log(_('App beendet, nur noch Hintergrundprozesse – räume auf'))
                        self.stop_game()
                        break
                elif not self.host_fresh(1800) and self.host_seen:
                    log(_('App seit 30 min weg – Session endet'))
                    self.stop_game()
                    break
                elif not self.host_seen and time.time() - self.started > 600:
                    log(_('App nicht innerhalb 10 min verbunden – Session endet'))
                    break
                self.write_state()
            if tick % 20 == 0:
                self.refresh_sessions()
            if self.guest_requested and time.time() - self.guest_requested > 10:
                self.guest_requested = 0.0
                if not self.guest_link_ts or time.time() - self.guest_link_ts > 15:
                    seen = sorted(self.sw.seen_callbacks) if self.sw else []
                    log(_("Kein Gast-Link von Steam nach 10 s (empfangene Callbacks: {seen})").format(
                        seen=seen or "-"))
            if tick % 150 == 0 and tick:
                self.refresh_friends()
            tick += 1
            time.sleep(0.1)
        if descendants(os.getpid()) and not self.real_processes():
            self.kill_tree()
        for f in ("state.json", "stop"):
            try:
                (DONOR_DIR / f).unlink()
            except OSError:
                pass
        if self.sw:
            self.sw.shutdown()
        log(_('Agent beendet'))
        return 0


def main() -> int:
    DONOR_DIR.mkdir(parents=True, exist_ok=True)
    from .i18n import init_from_config
    init_from_config()
    try:
        return Agent().run()
    except Exception:
        log(_('Agent abgestürzt:\n') + traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
