"""Remote Play Together for a game Steam itself has started ("attach" mode).

Some games register with Steam under their own, hard-coded AppID (e.g. Unity games
using Facepunch.Steamworks). Started through a donor, Steam then files the game under
its own app and streams nothing to the donor's session. For those, start the game
normally from Steam and create the invite for *its* AppID – the approach of
RemotePlayWhatever. The Steam API can be initialised only once per process, so this
runs in its own short-lived process (``rpt-anywhere --guest-link APPID``).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .i18n import _


def _find_lib() -> str:
    from .config import Config
    from .steam_lib import Steam, libsteam_api_score
    cfg = Config.load()
    lib = cfg.libsteam_api if cfg.libsteam_api and Path(cfg.libsteam_api).exists() else ""
    try:
        libs = Steam(cfg.steam_root or None).find_libsteam_api()
    except Exception:
        libs = []
    if libs and (not lib or (libsteam_api_score(Path(lib)) or ()) < (libsteam_api_score(libs[0]) or ())):
        lib = str(libs[0])
    return lib


def attach(appid: int, steamid: int = 0, timeout: float = 15.0, out=print) -> int:
    """Create a guest link (steamid=0) or invite a friend for the running game *appid*.
    Returns a process exit code."""
    from .steamworks import Steamworks, SteamworksError
    lib = _find_lib()
    if not lib:
        out(_('Keine libsteam_api.so gefunden (Einrichtung B3).'))
        return 2
    os.environ["SteamAppId"] = os.environ["SteamGameId"] = str(appid)
    os.environ.pop("SteamOverlayGameId", None)
    sw = Steamworks(lib)
    try:
        sw.init()
    except SteamworksError as e:
        out(_('Steam-API: {e}').format(e=e))
        return 2
    try:
        out(_('Angemeldet als {me} für AppID {appid} ({lib})').format(me=sw.my_name(), appid=appid, lib=lib))
        if not sw.invite(steamid):
            out(_('Steam hat die Anfrage abgelehnt – läuft das Spiel mit AppID {appid}?').format(appid=appid))
            return 1
        if steamid:
            out(_('Einladung an {sid} gesendet.').format(sid=steamid))
            return 0
        end = time.time() + timeout
        while time.time() < end:
            for kind, payload in sw.poll_events():
                if kind == "guest_link":
                    out(_('Gast-Link: {link}').format(link=payload))
                    return 0
            time.sleep(0.1)
        out(_('Kein Gast-Link von Steam nach {s} s – öffne Steams Remote-Play-Together-Fenster.').format(s=int(timeout)))
        sw.start_rpt_ui()
        return 1
    finally:
        try:
            sw.shutdown()
        except Exception:
            pass
