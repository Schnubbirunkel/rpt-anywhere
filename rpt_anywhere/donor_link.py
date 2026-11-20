"""File-based link between the GUI (on the host) and the donor stub.

Steam may run the donor's launch options inside its runtime container (always
for Proton games). The stub in bin/rpt-anywhere therefore only does shell work:
it dumps Steam's environment to ``donor/env`` and keeps the Steam session alive
(heartbeat ``donor/stub.alive``) while the GUI writes ``donor/host.alive``.
"""
from __future__ import annotations

import json
import os
import struct
import time
import uuid
from pathlib import Path

from .config import CACHE_DIR
from .i18n import _

DONOR_DIR = CACHE_DIR / "donor"

# Variables from Steam's launch environment that games need on the host.
_PASS_PREFIXES = ("Steam", "ENABLE_VK_LAYER_VALVE_steam", "SDL_GAMECONTROLLER",
                  "SDL_JOYSTICK", "STEAM_INPUT", "SteamEnv")
_PASS_EXACT = {"SteamAppId", "SteamGameId", "SteamOverlayGameId", "SteamClientLaunch",
               "STEAM_GAME_LAUNCH_SHELL", "SDL_VIDEO_X11_DGAMOUSE", "EnableConfiguratorSupport"}
_BLOCK_PREFIXES = ("STEAM_COMPAT_", "STEAM_RUNTIME", "PRESSURE_VESSEL", "STEAM_ZENITY")


def _now() -> int:
    return int(time.time())


def _read_int(p: Path) -> int:
    try:
        return int(p.read_text().strip())
    except Exception:
        return 0


class DonorLink:
    def __init__(self):
        DONOR_DIR.mkdir(parents=True, exist_ok=True)
        self.env: dict[str, str] = {}

    # GUI side -------------------------------------------------------------
    def heartbeat(self) -> None:
        tmp = DONOR_DIR / "host.alive.tmp"
        tmp.write_text(str(_now()))
        os.replace(tmp, DONOR_DIR / "host.alive")

    def stop_session(self) -> None:
        (DONOR_DIR / "stop").write_text("1")

    # agent (preferred) ------------------------------------------------------
    def agent_state(self) -> dict | None:
        try:
            st = json.loads((DONOR_DIR / "state.json").read_text())
        except Exception:
            return None
        return st if time.time() - float(st.get("ts", 0)) < 5 else None

    def send(self, cmd: str, **kw) -> None:
        d = DONOR_DIR / "req"
        d.mkdir(parents=True, exist_ok=True)
        name = f"{time.time():.6f}-{uuid.uuid4().hex[:6]}"
        tmp = d / f"{name}.tmp"
        tmp.write_text(json.dumps({"cmd": cmd, **kw}, ensure_ascii=False))
        os.replace(tmp, d / f"{name}.json")

    def avatar(self, steamid) -> tuple[int, int, bytes] | None:
        try:
            raw = (DONOR_DIR / "avatars" / f"{steamid}.rgba").read_bytes()
            w, h = struct.unpack_from("<II", raw)
            data = raw[8:]
            return (w, h, data) if len(data) == w * h * 4 else None
        except Exception:
            return None

    # bash-only fallback -----------------------------------------------------
    def alive(self) -> bool:
        t = _read_int(DONOR_DIR / "stub.alive")
        return bool(t) and _now() - t < 6 and (DONOR_DIR / "env").exists()

    def refresh(self) -> bool:
        """Re-read the donor environment. Returns True while a donor session is live."""
        if not self.alive():
            self.env = {}
            return False
        if not self.env:
            try:
                raw = (DONOR_DIR / "env").read_bytes()
            except OSError:
                return False
            env = {}
            for item in raw.split(b"\0"):
                k, sep, v = item.partition(b"=")
                if sep:
                    env[k.decode(errors="replace")] = v.decode(errors="replace")
            self.env = env
        return True

    @property
    def appid(self) -> int:
        for k in ("SteamAppId", "SteamGameId"):
            v = self.env.get(k, "")
            if v.isdigit() and int(v):
                return int(v) & 0xFFFFFF
        return 0

    def game_env(self, steam_root: Path | None = None) -> dict[str, str]:
        """Steam variables to inject into games launched from the host."""
        out = {}
        for k, v in self.env.items():
            if k.startswith(_BLOCK_PREFIXES):
                continue
            if k in _PASS_EXACT or k.startswith(_PASS_PREFIXES):
                out[k] = v
        preload = []
        if steam_root:
            preload = [str(steam_root / d / "gameoverlayrenderer.so")
                       for d in ("ubuntu12_32", "ubuntu12_64")
                       if (steam_root / d / "gameoverlayrenderer.so").exists()]
        if not preload:
            for p in self.env.get("LD_PRELOAD", "").replace(" ", ":").split(":"):
                if "gameoverlayrenderer" in p:
                    p = p[len("/run/host"):] if p.startswith("/run/host/") else p
                    if os.path.exists(p):
                        preload.append(p)
        if preload:
            out["LD_PRELOAD"] = ":".join(preload)
        return out

    def stub_log(self) -> str:
        try:
            return (DONOR_DIR / "stub.log").read_text()
        except OSError:
            return ""


# --------------------------------------------------------------------------- #
#  emergency stop
# --------------------------------------------------------------------------- #
def _rpt_processes(include_gui: bool) -> list[tuple[int, str]]:
    me = os.getpid()
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == me:
            continue
        try:
            raw = Path(f"/proc/{d}/cmdline").read_bytes()
        except OSError:
            continue
        cmd = raw.replace(b"\0", b" ").decode(errors="replace")
        if "rpt_anywhere.agent" in cmd or ("rpt-anywhere" in cmd and "--donor" in cmd):
            out.append((int(d), cmd.strip()))
        elif include_gui and "-m rpt_anywhere" in cmd and "--reset" not in cmd:
            out.append((int(d), cmd.strip()))
    return out


def reset(include_gui: bool = False, wait: float = 3.0) -> list[str]:
    """Stop the donor session: ask nicely, then kill agent/stub (and optionally other GUIs).
    Returns a description of what was killed."""
    import signal
    link = DonorLink()
    game_pid = 0
    try:
        st = json.loads((DONOR_DIR / "state.json").read_text())
        game_pid = int((st.get("running") or {}).get("pid") or 0)
    except Exception:
        pass
    try:
        link.send("end_session")
        link.stop_session()
    except OSError:
        pass
    end = time.time() + wait
    while time.time() < end and _rpt_processes(False):
        time.sleep(0.2)
    killed = []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        procs = _rpt_processes(include_gui)
        for pid, cmd in procs:
            try:
                os.kill(pid, sig)
                if sig == signal.SIGTERM:
                    killed.append(f"{pid}: {cmd[:90]}")
            except (ProcessLookupError, PermissionError):
                pass
        if procs:
            time.sleep(1.0)
    if game_pid:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(game_pid, sig)
                if sig == signal.SIGTERM:
                    killed.append(_('Spiel-Prozessgruppe {game_pid}').format(game_pid=game_pid))
                time.sleep(1.0)
            except (ProcessLookupError, PermissionError):
                break
    for name in ("state.json", "env", "pid", "stub.alive", "stop", "host.alive"):
        try:
            (DONOR_DIR / name).unlink()
        except OSError:
            pass
    for f in (DONOR_DIR / "req").glob("*") if (DONOR_DIR / "req").is_dir() else []:
        try:
            f.unlink()
        except OSError:
            pass
    return killed
