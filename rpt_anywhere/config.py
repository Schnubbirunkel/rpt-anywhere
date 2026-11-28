"""Persistent settings and the user's own game list (JSON in XDG dirs)."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from .i18n import N_

APP_ID = "rpt-anywhere"
APP_NAME = "RPT Anywhere"


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path(default).expanduser()) / APP_ID


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", "~/.config")
DATA_DIR = _xdg("XDG_DATA_HOME", "~/.local/share")
CACHE_DIR = _xdg("XDG_CACHE_HOME", "~/.cache")
CONFIG_FILE = CONFIG_DIR / "config.json"
PENDING_FILE = CACHE_DIR / "pending-launch.json"
PREFIX_DIR = DATA_DIR / "prefixes"
COVER_DIR = CACHE_DIR / "covers"

RUNNERS = {
    "native": N_("Linux nativ"),
    "proton": N_("Proton"),
    "wine": N_("Wine"),
    "command": N_("Befehl / Skript"),
    "steam": N_("Steam-Spiel"),
}


@dataclass
class Game:
    name: str
    runner: str = "native"            # see RUNNERS
    exe: str = ""                     # executable / .exe / shell command
    args: str = ""
    workdir: str = ""
    env: dict = field(default_factory=dict)
    proton: str = ""                  # Proton tool name ("" = default)
    prefix: str = ""                  # Wine/Proton prefix ("" = automatic)
    steam_appid: int = 0              # for runner == steam / imported shortcuts
    own_appid: bool = False           # run with the game's own SteamAppId instead of the donor's
    steam_options: bool = True        # apply the game's own Steam launch options (env, wrappers, args)
    image: str = ""
    favorite: bool = False
    hidden: bool = False
    last_played: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @classmethod
    def from_dict(cls, d: dict) -> "Game":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Config:
    donor_appid: int = 0
    donor_name: str = ""
    libsteam_api: str = ""
    steam_root: str = ""
    default_proton: str = ""
    show_steam_games: bool = True
    only_rpt_hint_dismissed: bool = False
    auto_open_rpt_overlay: bool = False
    language: str = "en"
    use_rpw: bool = True              # Steam games: start via Steam + Remote Play Whatever (no donor)
    rpw_path: str = ""                # RemotePlayWhatever AppImage (empty: auto-detect)
    games: list[Game] = field(default_factory=list)
    steam_overrides: dict = field(default_factory=dict)   # appid(str) -> Game dict overrides

    # ----------------------------------------------------------------- io
    @classmethod
    def load(cls) -> "Config":
        try:
            d = json.loads(CONFIG_FILE.read_text())
        except Exception:
            return cls()
        games = [Game.from_dict(g) for g in d.pop("games", [])]
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        cfg = cls(**known)
        cfg.games = games
        return cfg

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        d = asdict(self)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        os.replace(tmp, CONFIG_FILE)

    # ----------------------------------------------------------------- helpers
    def game(self, gid: str) -> Game | None:
        return next((g for g in self.games if g.id == gid), None)

    def steam_game(self, appid: int, name: str) -> Game:
        """A Game object for a Steam library title, with user overrides applied."""
        base = Game(name=name, runner="steam", steam_appid=appid, id=f"steam:{appid}")
        ov = self.steam_overrides.get(str(appid))
        if ov:
            for k, v in ov.items():
                if k in Game.__dataclass_fields__ and k != "id":
                    setattr(base, k, v)
        return base

    def save_game(self, g: Game) -> None:
        if g.id.startswith("steam:"):
            default = Game(name=g.name, runner="steam", steam_appid=g.steam_appid, id=g.id)
            diff = {k: v for k, v in asdict(g).items()
                    if k != "id" and getattr(default, k) != v}
            self.steam_overrides[str(g.steam_appid)] = diff
        else:
            for i, x in enumerate(self.games):
                if x.id == g.id:
                    self.games[i] = g
                    break
            else:
                self.games.append(g)
        self.save()

    def remove_game(self, gid: str) -> None:
        self.games = [g for g in self.games if g.id != gid]
        self.save()

    def touch(self, g: Game) -> None:
        g.last_played = time.time()
        self.save_game(g)


def write_pending(game_id: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps({"game": game_id, "ts": time.time()}))


def take_pending(max_age: float = 180) -> str | None:
    try:
        d = json.loads(PENDING_FILE.read_text())
        PENDING_FILE.unlink()
    except Exception:
        return None
    if time.time() - float(d.get("ts", 0)) > max_age:
        return None
    return d.get("game")
