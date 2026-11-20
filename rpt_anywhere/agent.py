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


def group_members(pgid: int) -> list[tuple[int, str]]:
    out = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for d in entries:
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as fh:
                raw = fh.read()
            comm = raw[raw.index("(") + 1:raw.rindex(")")]
            parts = raw.rsplit(")", 1)[1].split()
            if int(parts[2]) == pgid and parts[0] != "Z":
                out.append((int(d), comm))
        except (OSError, IndexError, ValueError):
            continue
    return out


def real_game_alive(pgid: int) -> bool:
    """True if anything other than Wine/Proton/runtime leftovers is still running."""
    return any(c.lower() not in INFRA for _k, c in group_members(pgid))


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # killpg(0) also succeeds for zombies only; check /proc for a live member
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as fh:
                    parts = fh.read().rsplit(")", 1)[1].split()
                if int(parts[2]) == pgid and parts[0] != "Z":
                    return True
            except (OSError, IndexError, ValueError):
                continue
    return False


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
        REQ_DIR.mkdir(parents=True, exist_ok=True)
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ steam
    def init_steam(self) -> None:
        from .steamworks import Steamworks, SteamworksError
        cfg = Config.load()
        lib = cfg.libsteam_api
        if not lib or not Path(lib).exists():
            try:
                from .steam_lib import Steam
                libs = Steam(cfg.steam_root or None).find_libsteam_api()
                lib = str(libs[0]) if libs else ""
            except Exception as e:  # pragma: no cover
                log(_('Suche nach libsteam_api.so fehlgeschlagen: {e}').format(e=e))
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
                self.refresh_sessions()
                self.event("joined", _('Ein Mitspieler ist beigetreten 🎮'))
            elif kind == "session_disconnected":
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

    def game_running(self) -> bool:
        """True while the game itself runs. Lingering Wine/Proton helpers do not count
        (for more than 15 s) – they are cleaned up instead of blocking the session."""
        if not self.proc:
            return False
        pg = self.proc.pid
        if not group_alive(pg):
            return False
        if real_game_alive(pg):
            self.idle_since = 0.0
            return True
        if not self.idle_since:
            self.idle_since = time.time()
            return True
        if time.time() - self.idle_since < 15:
            return True
        log(_('Nur noch Hintergrundprozesse übrig ({v0}) – räume auf').format(v0=', '.join(c for _k, c in group_members(pg))))
        self.kill_group(pg)
        return False

    def kill_group(self, pg: int) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pg, sig)
            except (ProcessLookupError, PermissionError):
                return
            for _i in range(20):
                if not group_alive(pg):
                    return
                time.sleep(0.1)

    def stop_game(self) -> None:
        if not self.proc:
            return
        log(_('Beende Spiel pid={pid}').format(pid=self.proc.pid))
        self.kill_group(self.proc.pid)

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
            ok = self.sw.invite(0)
            log(_('Gast-Link angefordert: {v0}').format(v0='ok' if ok else 'abgelehnt'))
            if not ok:
                self.event("error", _('Steam hat den Gast-Link abgelehnt.'))
        elif cmd == "overlay" and self.sw:
            self.sw.start_rpt_ui()
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
        self.init_steam()
        self.refresh_friends()
        tick = 0
        while not self.stop:
            self.pump()
            if tick % 5 == 0:
                self.process_requests()
            if tick % 10 == 0:
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
                elif self.host_seen and not self.game_running():
                    log(_('App beendet – Session endet'))
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
            if tick % 150 == 0 and tick:
                self.refresh_friends()
            tick += 1
            time.sleep(0.1)
        if self.proc and group_alive(self.proc.pid) and not real_game_alive(self.proc.pid):
            self.kill_group(self.proc.pid)
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
