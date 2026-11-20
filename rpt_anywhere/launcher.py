"""Turns a Game into a command line + environment and runs it as our child.

While the donor stub keeps the donor game "running" in Steam, games get Steam's
overlay (LD_PRELOAD) and the donor's SteamAppId injected, so Steam treats the
launched game as the donor – that is what makes Remote Play Together work.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import PREFIX_DIR, Game
from .steam_lib import Steam, find_game_executable, has_steam_drm, resolve_ci
from .i18n import _



class LaunchError(RuntimeError):
    pass


def game_env(extra: dict | None = None, donor: dict | None = None) -> dict:
    """Host environment + Steam's donor variables (overlay, AppID, Steam Input)."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("RPTA_") or k in ("PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE",
                                          "VIRTUAL_ENV", "LD_PRELOAD"):
            env.pop(k, None)
    if donor:
        env.update(donor)
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "game"


@dataclass
class Plan:
    argv: list[str]
    env: dict                 # full environment for a host launch
    cwd: str | None
    summary: str
    extra: dict = field(default_factory=dict)   # game-specific variables only
    where: str = "agent"      # "agent" = inside Steam's donor process, "host" = by the GUI
    note: str = ""


def _visible_in_container(path: str, steam: Steam) -> bool:
    """pressure-vessel shares $HOME and the Steam library folders with the container."""
    try:
        rp = os.path.realpath(path)
    except OSError:
        return False
    roots = [os.path.realpath(os.path.expanduser("~"))]
    roots += [os.path.realpath(str(l.parent)) for l in steam.libraries()]
    return any(rp == r or rp.startswith(r.rstrip("/") + "/") for r in roots)


def plan(game: Game, steam: Steam, default_proton: str = "", donor: dict | None = None,
         container: bool = False) -> Plan:
    """Build the launch plan. *container*: the donor agent runs inside Steam's runtime
    container (always the case for Proton donors)."""
    runner = game.runner
    exe = os.path.expanduser(game.exe.strip())
    args = shlex.split(game.args) if game.args.strip() else []
    extra = dict(game.env)
    cwd = os.path.expanduser(game.workdir) if game.workdir else None
    prefix = os.path.expanduser(game.prefix) if game.prefix else ""
    proton_name = game.proton or default_proton

    def mk(argv, summary, where="agent", note=""):
        return Plan(argv, game_env(extra, donor), cwd, summary, dict(extra), where, note)

    # ---------------------------------------------------------- Steam library game
    if runner == "steam":
        app = next((a for a in steam.installed_apps() if a.appid == game.steam_appid), None)
        if not app:
            raise LaunchError(_('Steam-Spiel {steam_appid} ist nicht installiert.').format(steam_appid=game.steam_appid))
        if not exe:
            found = find_game_executable(app)
            if not found:
                raise LaunchError(
                    _('Keine Startdatei für »{name}« gefunden (Ordner: {path}).\nBitte im Bearbeiten-Dialog die ausführbare Datei angeben.').format(name=app.name, path=app.path))
            exe, eargs, ewd = found
            if not args:
                args = eargs
            if ewd and not cwd:
                cwd = ewd
        elif not os.path.isabs(exe):
            exe = resolve_ci(app.path, exe) or str(app.path / exe)
        cwd = cwd or str(Path(exe).parent)
        windows = exe.lower().endswith((".exe", ".bat", ".msi"))
        if game.own_appid:
            extra.update(SteamAppId=str(app.appid), SteamGameId=str(app.appid))
        if windows:
            runner = "proton"
            prefix = prefix or str(app.compatdata)
            proton_name = game.proton or steam.compat_tool_for(app.appid) or default_proton
            extra.setdefault("STEAM_COMPAT_INSTALL_PATH", str(app.path))
        else:
            runner = "native"

    # ---------------------------------------------------------- runners
    if runner == "command":
        if not exe:
            raise LaunchError(_('Kein Befehl angegeben.'))
        cmd = exe + ((" " + " ".join(shlex.quote(a) for a in args)) if args else "")
        if container:
            return mk(["/bin/sh", "-c", cmd], cmd, "host",
                      _('Befehle laufen außerhalb von Steams Container – Overlay evtl. nicht aktiv.'))
        return mk(["/bin/sh", "-c", cmd], cmd)

    if not exe:
        raise LaunchError(_('Keine ausführbare Datei angegeben.'))
    if not os.path.exists(exe):
        fixed = resolve_ci("/", exe)
        if not fixed:
            raise LaunchError(_('Datei nicht gefunden: {exe}').format(exe=exe))
        exe = fixed
    cwd = cwd or str(Path(exe).parent)
    if cwd and not os.path.isdir(cwd):
        cwd = resolve_ci("/", cwd) or str(Path(exe).parent)

    if exe.lower().endswith(".exe") and has_steam_drm(exe):
        raise LaunchError(_('»{name}« ist mit Steams Kopierschutz (SteamStub) versehen und startet nur, wenn Steam genau dieses Spiel startet – nicht über einen Donor. Steam würde mit „Application load error V:0000065432“ abbrechen.').format(name=game.name))

    if runner == "native":
        argv = [exe, *args]
        if not os.access(exe, os.X_OK):
            if exe.endswith(".sh"):
                argv = ["/bin/bash", exe, *args]
            else:
                raise LaunchError(_('Datei ist nicht ausführbar: {exe}\n(chmod +x setzen)').format(exe=exe))
        if container and not _visible_in_container(exe, steam):
            return mk(argv, f"Linux · {Path(exe).name}", "host",
                      _('Liegt außerhalb von Home/Steam-Bibliothek – startet ohne Steam-Container.'))
        return mk(argv, f"Linux · {Path(exe).name}")

    if runner == "wine":
        wine = shutil.which("wine")
        if not wine:
            raise LaunchError(_("'wine' wurde nicht gefunden."))
        if prefix:
            Path(prefix).mkdir(parents=True, exist_ok=True)
            extra["WINEPREFIX"] = prefix
        return mk([wine, exe, *args], f"Wine · {Path(exe).name}", "host" if container else "agent",
                  _('Wine gibt es im Steam-Container nicht – Proton wird empfohlen.') if container
                  else "")

    if runner == "proton":
        tool = steam.proton_by_name(proton_name)
        if not tool:
            raise LaunchError(_('Keine Proton-Version gefunden. Installiere Proton über Steam oder lege GE-Proton in compatibilitytools.d ab.'))
        prefix = prefix or str(PREFIX_DIR / _slug(game.name))
        Path(prefix).mkdir(parents=True, exist_ok=True)
        extra.update({
            "STEAM_COMPAT_DATA_PATH": prefix,
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(steam.root),
        })
        extra.setdefault("STEAM_COMPAT_INSTALL_PATH", str(Path(exe).parent))
        entry = steam.runtime_entry_point(tool.runtime_appid)
        summary = f"{tool.name} · {Path(exe).name}"
        if container:
            # already inside Steam's runtime container: call Proton directly, like Steam does
            ok = all(_visible_in_container(p, steam) for p in (exe, prefix, str(tool.path)))
            argv = [str(tool.script), "waitforexitandrun", exe, *args]
            if ok:
                return mk(argv, summary)
            if entry:
                argv = [str(entry), "--verb=waitforexitandrun", "--", *argv]
            return mk(argv, summary, "host",
                      _('Spiel liegt außerhalb von Home/Steam-Bibliothek – startet ohne Donor-Prozess.'))
        if entry:
            argv = [str(entry), "--verb=waitforexitandrun", "--",
                    str(tool.script), "waitforexitandrun", exe, *args]
            return mk(argv, summary)
        argv = [str(tool.script), "waitforexitandrun", exe, *args]
        if tool.runtime_appid:
            return mk(argv, summary, note=_('{tool} braucht die Steam Linux Runtime ({appid}), die nicht gefunden wurde – Proton startet ohne sie.').format(tool=tool.name, appid=tool.runtime_appid))
        return mk(argv, summary)

    raise LaunchError(_('Unbekannter Runner: {runner}').format(runner=runner))


class RunningGame:
    def __init__(self, game: Game, p: Plan, log_path: Path | None = None):
        self.game = game
        self.plan = p
        self.started = time.time()
        self.log_path = log_path
        out = open(log_path, "ab") if log_path else subprocess.DEVNULL
        try:
            self.proc = subprocess.Popen(p.argv, env=p.env, cwd=p.cwd, stdout=out, stderr=out,
                                         stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            raise LaunchError(_('Start fehlgeschlagen: {e}').format(e=e)) from e
        finally:
            if log_path:
                out.close()

    @property
    def running(self) -> bool:
        if self.proc.poll() is None:
            return True
        # launcher-style games fork and exit; keep tracking the process group
        return _group_alive(self.proc.pid)

    def stop(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except ProcessLookupError:
                return
            for _i in range(30):
                if not _group_alive(self.proc.pid):
                    return
                time.sleep(0.1)


def _group_alive(pgid: int) -> bool:
    try:
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/stat") as fh:
                    parts = fh.read().rsplit(")", 1)[1].split()
                if int(parts[2]) == pgid and parts[0] != "Z":
                    return True
            except (OSError, IndexError, ValueError):
                continue
    except OSError:
        pass
    return False
