"""Everything we can learn about the local Steam installation from disk."""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import vdf
from .vdf import iget, ipath
from .i18n import _

log = logging.getLogger("rpt")
STEAMID64_BASE = 76561197960265728
RPT_CATEGORY = "category_44"  # "Remote Play Together" store category

# App ids that are tools/runtimes and never interesting as games
TOOL_APPIDS = {
    228980,   # Steamworks Common Redistributables
    1070560,  # Steam Linux Runtime 1.0 (scout)
    1391110,  # Steam Linux Runtime 2.0 (soldier)
    1628350,  # Steam Linux Runtime 3.0 (sniper)
    4183110,  # Steam Linux Runtime 4.0
    1493710,  # Proton Experimental
    2180100,  # Proton Hotfix
    1826330,  # Proton EasyAntiCheat Runtime
    1161040,  # Proton BattlEye Runtime
    250820,   # SteamVR
}
_TOOL_NAME = re.compile(r"^(proton|steam linux runtime|steamworks common)", re.I)


# --------------------------------------------------------------------------- #
@dataclass
class SteamApp:
    appid: int
    name: str
    installdir: str
    library: Path            # .../steamapps
    rpt: bool | None = None  # supports Remote Play Together (None = unknown)
    app_type: str = ""
    launch: list[dict] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.library / "common" / self.installdir

    @property
    def compatdata(self) -> Path:
        return self.library / "compatdata" / str(self.appid)


@dataclass
class ProtonTool:
    name: str
    path: Path
    internal: str = ""        # name Steam uses in CompatToolMapping
    runtime_appid: int | None = None

    @property
    def script(self) -> Path:
        return self.path / "proton"


@dataclass
class Shortcut:
    appid: int
    name: str
    exe: str
    start_dir: str
    launch_options: str
    icon: str


# --------------------------------------------------------------------------- #
class Steam:
    """Facade over one Steam installation."""

    CANDIDATE_ROOTS = [
        "~/.steam/steam",
        "~/.steam/root",
        "~/.local/share/Steam",
        "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
        "~/snap/steam/common/.local/share/Steam",
    ]

    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root).expanduser() if root else self.find_root()
        self._appinfo_cache: dict[int, dict] | None = None

    # ---------------------------------------------------------------- basics
    @classmethod
    def find_root(cls) -> Path | None:
        env = os.environ.get("RPTA_STEAM_ROOT")
        if env and Path(env).expanduser().is_dir():
            return Path(env).expanduser()
        seen = set()
        for c in cls.CANDIDATE_ROOTS:
            p = Path(c).expanduser()
            try:
                rp = p.resolve()
            except OSError:
                continue
            if rp in seen:
                continue
            seen.add(rp)
            if (p / "steamapps").is_dir():
                return rp
        return None

    @property
    def ok(self) -> bool:
        return self.root is not None and (self.root / "steamapps").is_dir()

    @property
    def is_flatpak(self) -> bool:
        return self.root is not None and ".var/app/com.valvesoftware.Steam" in str(self.root)

    def libraries(self) -> list[Path]:
        if not self.ok:
            return []
        libs = [self.root / "steamapps"]
        lf = self.root / "steamapps" / "libraryfolders.vdf"
        if lf.exists():
            data = iget(vdf.load(lf), "libraryfolders", {})
            for _k, v in data.items():
                p = iget(v, "path") if isinstance(v, dict) else v
                if isinstance(p, str):
                    sp = Path(p) / "steamapps"
                    if sp.is_dir() and sp.resolve() not in [l.resolve() for l in libs]:
                        libs.append(sp)
        return libs

    # ---------------------------------------------------------------- apps
    def installed_apps(self, with_details: bool = True) -> list[SteamApp]:
        apps: dict[int, SteamApp] = {}
        for lib in self.libraries():
            for acf in lib.glob("appmanifest_*.acf"):
                try:
                    st = iget(vdf.load(acf), "AppState", {})
                    appid = int(iget(st, "appid", 0))
                except Exception:
                    continue
                name = iget(st, "name", f"App {appid}")
                if not appid or appid in TOOL_APPIDS or _TOOL_NAME.match(name):
                    continue
                apps[appid] = SteamApp(appid, name, iget(st, "installdir", ""), lib)
        if with_details and apps:
            info = self.appinfo(set(apps))
            for appid, app in list(apps.items()):
                kv = iget(info.get(appid, {}), "appinfo", {})
                common = iget(kv, "common", {})
                if common:
                    app.app_type = str(iget(common, "type", "")).lower()
                    cats = iget(common, "category", {}) or {}
                    app.rpt = str(iget(cats, RPT_CATEGORY, "0")) == "1"
                    if app.app_type in ("tool", "config", "music", "video", "dlc"):
                        del apps[appid]
                        continue
                launch = ipath(kv, "config", "launch", default={}) or {}
                app.launch = [v for _k, v in sorted(launch.items(), key=lambda kv_: _intkey(kv_[0]))
                              if isinstance(v, dict)]
        return sorted(apps.values(), key=lambda a: a.name.lower())

    def appinfo_files(self) -> list[Path]:
        """Steam's app info cache: one appinfo.vdf, or (newer clients) a folder of shards
        appcache/appinfo/appinfo_00.vdf … appinfo_ff.vdf."""
        if not self.root:
            return []
        cache = self.root / "appcache"
        files = []
        if (cache / "appinfo.vdf").is_file():
            files.append(cache / "appinfo.vdf")
        if (cache / "appinfo").is_dir():
            files += sorted((cache / "appinfo").glob("appinfo*.vdf"))
        return files

    def appinfo(self, wanted: set[int]) -> dict[int, dict]:
        files = self.appinfo_files()
        if not files:
            log.warning(_('Steams App-Cache (appinfo) nicht gefunden'))
            return {}
        info: dict[int, dict] = {}
        errors = []
        for f in files:
            rest = set(wanted) - set(info)
            if not rest:
                break
            try:
                info.update(vdf.read_appinfo(f, rest))
            except Exception as e:
                errors.append(f"{f.name}: {e}")
        if errors:
            log.error(_('appinfo: %d Datei(en) nicht lesbar, z. B. %s'), len(errors), errors[0])
        log.info(_('appinfo (%d Datei(en)): %d von %d Spielen gelesen'), len(files), len(info),
                 len(wanted))
        return info

    # ---------------------------------------------------------------- proton
    def proton_tools(self) -> list[ProtonTool]:
        tools: list[ProtonTool] = []
        if not self.ok:
            return tools
        dirs = []
        for lib in self.libraries():
            dirs += [Path(p) for p in glob.glob(str(lib / "common" / "Proton*"))]
        for base in (self.root / "compatibilitytools.d",
                     Path("~/.steam/root/compatibilitytools.d").expanduser(),
                     Path("/usr/share/steam/compatibilitytools.d")):
            if base.is_dir():
                dirs += [p for p in base.iterdir() if p.is_dir()]
        seen = set()
        for d in dirs:
            if not (d / "proton").exists():
                continue
            rd = d.resolve()
            if rd in seen:
                continue
            seen.add(rd)
            internal = d.name
            cvdf = d / "compatibilitytool.vdf"
            if cvdf.exists():
                try:
                    ct = ipath(vdf.load(cvdf), "compatibilitytools", "compat_tools", default={})
                    if ct:
                        internal = next(iter(ct))
                except Exception:
                    pass
            else:  # official Proton: "Proton 9.0" -> proton_9, Experimental -> proton_experimental
                m = re.match(r"Proton (\d+)", d.name)
                internal = f"proton_{m.group(1)}" if m else d.name.lower().replace(" - ", "_").replace(" ", "_")
            rt = None
            tm = d / "toolmanifest.vdf"
            if tm.exists():
                try:
                    rt = int(ipath(vdf.load(tm), "manifest", "require_tool_appid", default=0)) or None
                except Exception:
                    pass
            tools.append(ProtonTool(d.name, d, internal, rt))
        tools.sort(key=_proton_sort_key, reverse=True)
        return tools

    def runtime_entry_point(self, runtime_appid: int | None) -> Path | None:
        """Entry point of the Steam Linux Runtime a compatibility tool requires.
        Looked up via the runtime's appmanifest, so new runtimes (e.g. 4183110,
        used by Proton-CachyOS "slr" builds) are found as well."""
        if not runtime_appid:
            return None
        dirs: list[str] = []
        for lib in self.libraries():
            acf = lib / f"appmanifest_{runtime_appid}.acf"
            if acf.exists():
                try:
                    d = iget(iget(vdf.load(acf), "AppState", {}), "installdir", "")
                    if d:
                        dirs.append(str(lib / "common" / d))
                except Exception:
                    pass
        names = {1628350: "SteamLinuxRuntime_sniper", 1391110: "SteamLinuxRuntime_soldier",
                 4183110: "SteamLinuxRuntime_4"}
        if runtime_appid in names:
            dirs += [str(lib / "common" / names[runtime_appid]) for lib in self.libraries()]
        for d in dirs:
            ep = resolve_ci(d, "_v2-entry-point") if Path(d).is_dir() else None
            if ep and Path(ep).exists():
                return Path(ep)
        return None

    def compat_tool_for(self, appid: int) -> str | None:
        cfg = self.root / "config" / "config.vdf" if self.root else None
        if not cfg or not cfg.exists():
            return None
        try:
            m = ipath(vdf.load(cfg), "InstallConfigStore", "Software", "Valve", "Steam",
                      "CompatToolMapping", default={})
        except Exception:
            return None
        for key in (str(appid), "0"):
            name = iget(iget(m, key, {}), "name")
            if name:
                return name
        return None

    def proton_by_name(self, name: str | None) -> ProtonTool | None:
        tools = self.proton_tools()
        if not tools:
            return None
        if name:
            for t in tools:
                if name in (t.internal, t.name, str(t.path)):
                    return t
        return tools[0]

    # ---------------------------------------------------------------- user
    def users(self) -> list[dict]:
        out = []
        lu = self.root / "config" / "loginusers.vdf" if self.root else None
        if lu and lu.exists():
            for sid, info in iget(vdf.load(lu), "users", {}).items():
                try:
                    sid64 = int(sid)
                except ValueError:
                    continue
                out.append({
                    "steamid": sid64,
                    "accountid": sid64 - STEAMID64_BASE,
                    "persona": iget(info, "PersonaName", ""),
                    "recent": str(iget(info, "MostRecent", "0")) == "1",
                    "timestamp": int(iget(info, "Timestamp", 0) or 0),
                })
        out.sort(key=lambda u: (u["recent"], u["timestamp"]), reverse=True)
        if not out and self.root and (self.root / "userdata").is_dir():
            for d in (self.root / "userdata").iterdir():
                if d.name.isdigit() and d.name != "0":
                    out.append({"steamid": int(d.name) + STEAMID64_BASE, "accountid": int(d.name),
                                "persona": "", "recent": False, "timestamp": 0})
        return out

    def current_user(self) -> dict | None:
        u = self.users()
        return u[0] if u else None

    def userdata(self, accountid: int | None = None) -> Path | None:
        if accountid is None:
            u = self.current_user()
            if not u:
                return None
            accountid = u["accountid"]
        p = self.root / "userdata" / str(accountid)
        return p if p.is_dir() else None

    # ---------------------------------------------------------------- shortcuts
    def shortcuts(self) -> list[Shortcut]:
        ud = self.userdata()
        f = ud / "config" / "shortcuts.vdf" if ud else None
        if not f or not f.exists():
            return []
        try:
            data, _unused = vdf.parse_binary(f.read_bytes())
        except Exception:
            return []
        out = []
        for _k, s in iget(data, "shortcuts", {}).items():
            if not isinstance(s, dict):
                continue
            appid = int(iget(s, "appid", 0)) & 0xFFFFFFFF
            out.append(Shortcut(appid, iget(s, "AppName", "") or iget(s, "appname", ""),
                                str(iget(s, "Exe", "")).strip('"'),
                                str(iget(s, "StartDir", "")).strip('"'),
                                iget(s, "LaunchOptions", ""), iget(s, "icon", "")))
        return out

    # ---------------------------------------------------------------- launch options
    def _localconfig(self) -> Path | None:
        ud = self.userdata()
        p = ud / "config" / "localconfig.vdf" if ud else None
        return p if p and p.exists() else None

    def get_launch_options(self, appid: int) -> str | None:
        lc = self._localconfig()
        if not lc:
            return None
        apps = ipath(vdf.load(lc), "UserLocalConfigStore", "Software", "Valve", "Steam", "apps")
        return iget(iget(apps, str(appid), {}), "LaunchOptions")

    def set_launch_options(self, appid: int, value: str) -> Path:
        """Write launch options for *appid*. Steam must be closed. Returns backup path."""
        if self.is_running():
            raise RuntimeError(_('Steam läuft noch – bitte Steam komplett beenden.'))
        lc = self._localconfig()
        if not lc:
            raise FileNotFoundError(_('localconfig.vdf nicht gefunden'))
        backup = lc.with_name(f"localconfig.vdf.rpta-backup-{int(time.time())}")
        shutil.copy2(lc, backup)
        raw = lc.read_bytes()
        text = raw.decode("utf-8", "surrogateescape")
        new = vdf.set_text_value(text, ["UserLocalConfigStore", "Software", "Valve", "Steam",
                                        "apps", str(appid)], "LaunchOptions", value)
        # safety: the result must parse and contain our value
        check = vdf.ipath(vdf.loads(new), "UserLocalConfigStore", "Software", "Valve", "Steam",
                          "apps", str(appid), "LaunchOptions")
        if check != value:
            raise RuntimeError(_('Prüfung fehlgeschlagen – localconfig.vdf wurde nicht verändert.'))
        tmp = lc.with_suffix(".rpta-tmp")
        tmp.write_bytes(new.encode("utf-8", "surrogateescape"))
        os.replace(tmp, lc)
        log.info(_('Startoption für %s gesetzt (nur diese Zeile geändert), Backup: %s'), appid, backup)
        return backup

    # ---------------------------------------------------------------- images
    def header_image(self, appid: int) -> Path | None:
        if not self.root:
            return None
        lc = self.root / "appcache" / "librarycache"
        for p in (lc / f"{appid}_header.jpg", lc / str(appid) / "header.jpg"):
            if p.exists():
                return p
        d = lc / str(appid)
        if d.is_dir():  # new layout: librarycache/<appid>/<hash>/header.jpg
            hits = list(d.glob("*/header.jpg")) or list(d.glob("**/library_header*.jpg"))
            if hits:
                return hits[0]
        ud = self.userdata()
        if ud:
            for ext in ("png", "jpg"):
                g = ud / "config" / "grid" / f"{appid}.{ext}"
                if g.exists():
                    return g
        return None

    @staticmethod
    def header_url(appid: int) -> str:
        return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"

    # ---------------------------------------------------------------- process
    def is_running(self) -> bool:
        pidfile = Path("~/.steam/steam.pid").expanduser()
        try:
            pid = int(pidfile.read_text().strip())
            if Path(f"/proc/{pid}").exists():
                comm = Path(f"/proc/{pid}/comm").read_text().strip()
                if "steam" in comm.lower():
                    return True
        except Exception:
            pass
        try:
            r = subprocess.run(["pgrep", "-x", "steam|steamwebhelper"], capture_output=True)
            return r.returncode == 0
        except FileNotFoundError:
            return False

    def open_url(self, url: str) -> None:
        """Hand a steam:// URL to the running client."""
        candidates = []
        if shutil.which("steam"):
            candidates.append(["steam", url])
        if self.is_flatpak and shutil.which("flatpak"):
            candidates.append(["flatpak", "run", "com.valvesoftware.Steam", url])
        if shutil.which("xdg-open"):
            candidates.append(["xdg-open", url])
        for c in candidates:
            try:
                subprocess.Popen(c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, env=clean_env())
                return
            except OSError:
                continue
        raise RuntimeError(_("Konnte Steam nicht aufrufen (weder 'steam' noch 'xdg-open' gefunden)"))

    def run_app(self, appid: int) -> None:
        self.open_url(f"steam://rungameid/{appid}")

    # ---------------------------------------------------------------- libsteam_api
    def find_libsteam_api(self) -> list[Path]:
        """64-bit libsteam_api.so builds shipped with installed games, best first."""
        hits: list[tuple[tuple, Path]] = []
        for lib in self.libraries():
            for p in glob.glob(str(lib / "common" / "*" / "**" / "libsteam_api.so"), recursive=True):
                path = Path(p)
                score = libsteam_api_score(path)
                if score:
                    hits.append((score, path))
        hits.sort(key=lambda t: t[0], reverse=True)
        return [p for _k, p in hits]


def libsteam_api_score(path: Path) -> tuple | None:
    """Rank a libsteam_api.so: 64-bit x86_64 and has the Remote Play invite API."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(20)
            if head[:4] != b"\x7fELF" or head[4] != 2 or int.from_bytes(head[18:20], "little") != 62:
                return None
            fh.seek(0)
            data = fh.read()
    except OSError:
        return None
    if b"SteamAPI_ISteamRemotePlay_BSendRemotePlayTogetherInvite" not in data:
        return None
    import re as _re
    versions = [int(v) for v in _re.findall(rb"SteamAPI_SteamRemotePlay_v(\d{3})", data)]
    rp_version = max(versions) if versions else 0
    has_ui = b"SteamAPI_ISteamRemotePlay_ShowRemotePlayTogetherUI" in data
    has_start = b"SteamAPI_ISteamRemotePlay_BStartRemotePlayTogether" in data
    has_register = b"SteamAPI_RegisterCallback" in data
    bundled = b"node_modules" not in str(path).encode()   # prefer libs shipped by games
    return (rp_version, has_ui or has_start, has_register, bundled, path.stat().st_mtime)


def clean_env() -> dict:
    """Environment for spawning host tools (no Steam runtime / overlay leftovers)."""
    env = dict(os.environ)
    for k in ("LD_PRELOAD", "PYTHONHOME", "PYTHONPATH"):
        env.pop(k, None)
    if "SYSTEM_LD_LIBRARY_PATH" in env:
        env["LD_LIBRARY_PATH"] = env["SYSTEM_LD_LIBRARY_PATH"]
    return env


def _intkey(k: str) -> int:
    try:
        return int(k)
    except ValueError:
        return 1 << 30


def _proton_sort_key(t: ProtonTool):
    n = t.name.lower()
    if "experimental" in n:
        return (2, 0.0)
    if "hotfix" in n:
        return (-1, 0.0)
    nums = re.findall(r"\d+(?:\.\d+)?", n)
    v = float(nums[0]) if nums else 0.0
    return (1 if n.startswith("proton") else 0, v)


def pick_launch(app: SteamApp) -> dict | None:
    """Choose the default launch entry: prefer Linux, fall back to Windows."""
    def os_of(e):
        return str(ipath(e, "config", "oslist", default="") or "").lower()

    def ok(e):
        if not iget(e, "executable"):
            return False
        if ipath(e, "config", "betakey"):
            return False
        t = str(iget(e, "type", "default")).lower()
        return t in ("default", "none", "")

    entries = [e for e in app.launch if ok(e)] or [e for e in app.launch if iget(e, "executable")]
    for want in ("linux", "", "windows"):
        for e in entries:
            o = os_of(e)
            if (want == "" and o == "") or (want and want in o):
                arch = str(ipath(e, "config", "osarch", default="") or "")
                if arch == "32" and any(str(ipath(x, "config", "osarch", default="")) == "64"
                                        and want in os_of(x) for x in entries):
                    continue
                return {**e, "_os": "windows" if want == "windows" else "linux" if want else "any"}
    return None


# --------------------------------------------------------------------------- #
#  executable discovery
# --------------------------------------------------------------------------- #
def resolve_ci(base, rel: str) -> str | None:
    """Resolve *rel* below *base* ignoring case (Steam's Windows paths often differ in case)."""
    rel = str(rel).replace("\\", "/").strip()
    if rel.startswith("./"):
        rel = rel[2:]
    cur = Path(base)
    if os.path.isabs(rel):
        cur, rel = Path("/"), rel.lstrip("/")
    for part in [p for p in rel.split("/") if p and p != "."]:
        nxt = cur / part
        if not nxt.exists():
            try:
                hit = next((c for c in cur.iterdir() if c.name.lower() == part.lower()), None)
            except OSError:
                hit = None
            if hit is None:
                return None
            nxt = hit
        cur = nxt
    return str(cur)


_SKIP_EXE = re.compile(
    r"(unins|uninst|setup|install|redist|vc_?redist|vcredist|dxsetup|directx|dotnet|ue4prereq|"
    r"prereq|crash|report|easyanticheat|eac_|battleye|_be\.exe$|helper|updater|touchup|"
    r"cefprocess|subprocess|unitycrash|7z|python|java|notification|overlay|ffmpeg|ffprobe|"
    r"server|dedicated|autorun|benchmark|config|settings|editor|sdk|tool)", re.I)


def _is_elf_exec(p: Path) -> bool:
    try:
        if not os.access(p, os.X_OK) or not p.is_file():
            return False
        with open(p, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def scan_executables(folder: Path, name: str = "") -> list[Path]:
    """Guess the game's executable(s) in *folder*, best first."""
    if not folder.is_dir():
        return []
    want = re.sub(r"[^a-z0-9]", "", name.lower())
    cands: list[tuple[float, Path]] = []
    base_depth = len(folder.parts)
    elf_checks = 0
    seen_files = 0
    for root, dirs, files in os.walk(folder):
        depth = len(Path(root).parts) - base_depth
        seen_files += len(files)
        if seen_files > 20000:      # huge folders: stop, use what we have
            break
        if depth > 4:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in
                   ("_commonredist", "redist", "directx", "support", "__installer", "dotnet",
                    "vcredist", "easyanticheat", "battleye", "logs", "crashreporter",
                    "streamingassets", "node_modules", "plugins", "mods", "saves", "save",
                    "data", "content", "assets", "resources", "locale", "localization",
                    "shaders", "music", "sound", "sounds", "audio", "movies", "video", "docs",
                    "engine", "third_party", "thirdparty", "redistributables")
                   and not d.lower().endswith(("_data", ".app"))]
        for f in files:
            p = Path(root) / f
            low = f.lower()
            is_win = low.endswith(".exe")
            is_lin = low.endswith((".sh", ".x86_64", ".x86", ".appimage")) and os.access(p, os.X_OK)
            if not is_lin and not is_win and "." not in f and depth <= 2 and elf_checks < 60:
                elf_checks += 1     # opening files is slow on big/NTFS drives – keep it bounded
                is_lin = _is_elf_exec(p)
            if not (is_win or is_lin) or _SKIP_EXE.search(low):
                continue
            stem = re.sub(r"[^a-z0-9]", "", Path(low).stem.split(".")[0])
            score = 0.0
            if want and stem and (stem in want or want in stem):
                score += 50
            if "shipping" in low or low.endswith(".x86_64"):
                score += 15
            if "launcher" in low:
                score -= 10
            if is_lin:
                score += 8
            try:
                score += min(p.stat().st_size / 5_000_000, 20)
            except OSError:
                pass
            score -= depth * 4
            cands.append((score, p))
    cands.sort(key=lambda t: t[0], reverse=True)
    return [p for _k, p in cands]


def find_game_executable(app: "SteamApp") -> tuple[str, list[str], str | None] | None:
    """(exe, args, workdir) for a Steam game – from Steam's launch config, else by scanning."""
    import shlex
    entry = pick_launch(app)
    if entry:
        exe = resolve_ci(app.path, str(iget(entry, "executable")))
        if exe and os.path.isfile(exe):
            args = shlex.split(str(iget(entry, "arguments") or "")) if iget(entry, "arguments") else []
            wd = iget(entry, "workingdir")
            wd = resolve_ci(app.path, str(wd)) if wd else None
            log.info(_('Startdatei für %s aus Steam-Daten: %s'), app.name, exe)
            return exe, args, wd
        log.warning(_('Startdatei aus Steam-Daten für %s existiert nicht: %s'), app.name,
                    iget(entry, "executable"))
    else:
        log.warning(_('Keine Startkonfiguration für %s (%s) in appinfo.vdf'), app.name, app.appid)
    hits = scan_executables(app.path, app.name)
    if hits:
        log.info(_('Startdatei für %s per Suche geraten: %s'), app.name, hits[0])
        return str(hits[0]), [], None
    return None


# --------------------------------------------------------------------------- #
#  repair for localconfig.vdf damaged by RPT Anywhere <= 0.3.1
# --------------------------------------------------------------------------- #
def localconfig_damaged(path: Path) -> bool:
    """Old versions rewrote the file and turned \\n / \\t escapes into raw control chars."""
    try:
        text = path.read_text("utf-8", "surrogateescape")
    except OSError:
        return False
    return any(k == "s" and ("\n" in v or "\t" in v) for k, v, _a, _b in vdf._raw_tokens(text))


def repair_localconfigs(root: Path) -> list[tuple[bool, str]]:
    """Restore the oldest RPT backup (= state before RPT Anywhere first touched the file)."""
    msgs = []
    for lc in sorted(Path(root).glob("userdata/*/config/localconfig.vdf")):
        backups = sorted(lc.parent.glob("localconfig.vdf.rpta-backup-*"),
                         key=lambda p: int(p.name.rsplit("-", 1)[1]) if p.name.rsplit("-", 1)[1].isdigit() else 0)
        damaged = localconfig_damaged(lc)
        if not backups:
            msgs.append((False, _('{lc}: keine RPT-Sicherung vorhanden').format(lc=lc)
                        + (_(' – Datei sieht BESCHÄDIGT aus!') if damaged else _(' – nie verändert, ok.'))))
            continue
        orig = backups[0]
        broken = lc.with_name(f"localconfig.vdf.rpta-defekt-{int(time.time())}")
        shutil.copy2(lc, broken)
        shutil.copy2(orig, lc)
        msgs.append((True, _('{lc}: Original von {v1} wiederhergestellt (vorher {v2}; aktuelle Fassung gesichert als {name})').format(lc=lc, v1=time.ctime(orig.stat().st_mtime), v2=_('beschädigt') if damaged else _('unauffällig'), name=broken.name)))
    return msgs
