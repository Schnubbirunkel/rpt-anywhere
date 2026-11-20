"""Entry point: single-instance handling + CLI."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .i18n import _
from . import config as cfgmod


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="rpt-anywhere", description=_('{APP_NAME} – Steam Remote Play Together für beliebige Spiele unter Linux').format(APP_NAME=cfgmod.APP_NAME))
    ap.add_argument("--donor", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--run", metavar="NAME_OR_ID",
                    help=_('Spiel aus der Liste starten (über Steam/Donor, falls nötig)'))
    ap.add_argument("--list", action="store_true", help=_('eigene Spiele auflisten'))
    ap.add_argument("--reset", action="store_true",
                    help=_('Notaus: Remote-Play-Session, Agent und alle RPT-Prozesse beenden'))
    ap.add_argument("--repair-steam-config", action="store_true",
                    help=_('Steams localconfig.vdf auf den Stand vor RPT Anywhere zurücksetzen'))
    ap.add_argument("--diagnose", action="store_true",
                    help=_('Steam-Erkennung prüfen (Bibliotheken, Startdateien, Proton, Donor)'))
    ap.add_argument("--print-launch-option", action="store_true",
                    help=_('Startoption für das Donor-Spiel ausgeben'))
    ap.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def diagnose(cfg) -> int:
    import logging
    from .steam_lib import Steam, find_game_executable
    logging.basicConfig(level=logging.WARNING, format="  ! %(message)s")
    logging.getLogger("rpt").setLevel(logging.INFO if os.environ.get("RPTA_DEBUG") else logging.WARNING)
    from .steam_lib import log as _l
    _l.addFilter(lambda r: r.msg != _("Keine Startkonfiguration für %s (%s) in appinfo.vdf"))
    print(_('RPT Anywhere {version__}').format(version__=__version__))
    s = Steam(cfg.steam_root or None)
    print(_('Steam-Ordner: {root}  (ok={ok}, flatpak={is_flatpak}, läuft={v3})').format(root=s.root, ok=s.ok, is_flatpak=s.is_flatpak, v3=s.is_running()))
    for lib in s.libraries():
        print(_('  Bibliothek: {lib}').format(lib=lib))
    files = s.appinfo_files()
    if files:
        with open(files[0], "rb") as fh:
            head = fh.read(16)
        print(_('appinfo: {v0} Datei(en), z. B. {name} (Version {v2}, Kopf {v3})').format(v0=len(files), name=files[0].name, v2=head[0], v3=head.hex(' ')))
    else:
        cache = s.root / "appcache" if s.root else None
        print(_('appinfo: FEHLT – Steams App-Cache ist leer oder liegt woanders.'))
        if cache and cache.is_dir():
            print(_('  Inhalt von appcache/:'), ", ".join(sorted(p.name for p in cache.iterdir())[:20]))
    sys.stdout.flush()
    from .steam_lib import localconfig_damaged
    for lc in sorted(s.root.glob("userdata/*/config/localconfig.vdf")) if s.root else []:
        bk = list(lc.parent.glob("localconfig.vdf.rpta-backup-*"))
        print(_('localconfig: {name}  beschädigt={v1}  RPT-Sicherungen={v2}').format(name=lc.parent.parent.name, v1=localconfig_damaged(lc), v2=len(bk)))
    print(_('Proton:'), ", ".join(t.name for t in s.proton_tools()) or "keins")
    print(_('Donor: {donor_name} ({donor_appid}) – Startoption: {v2}').format(donor_name=cfg.donor_name, donor_appid=cfg.donor_appid, v2=s.get_launch_options(cfg.donor_appid) if cfg.donor_appid else '-'))
    print(_('libsteam_api: {v0}  | gefunden: {v1}').format(v0=cfg.libsteam_api or '-', v1=[str(p) for p in s.find_libsteam_api()[:2]]))
    print(_('\nSpiele:'), flush=True)
    for a in s.installed_apps():
        try:
            f = find_game_executable(a)
        except Exception as e:
            f = None
            print(_('  ! Fehler: {e}').format(e=e))
        rpt = {True: "RPT", False: "   ", None: " ? "}[a.rpt]
        print(_('  {rpt} {appid:>8}  {v2:34}  {v3} Startkonfig.  ->  {v4}').format(rpt=rpt, appid=a.appid, v2=a.name[:34], v3=len(a.launch), v4=f[0] if f else _('NICHT GEFUNDEN')), flush=True)
    return 0


def main(argv=None) -> int:
    from .i18n import init_from_config
    init_from_config()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    cfg = cfgmod.Config.load()

    if args.list:
        for g in cfg.games:
            print(f"{g.id}\t{g.runner:8}\t{g.name}")
        return 0
    if args.reset:
        from .donor_link import reset
        killed = reset(include_gui=True)
        print("Beendet:" if killed else _('Keine laufenden RPT-Anywhere-Prozesse gefunden.'))
        for k in killed:
            print("  " + k)
        print(_('Steam sollte das Donor-Spiel jetzt nicht mehr als laufend anzeigen.'))
        return 0
    if args.repair_steam_config:
        from .steam_lib import Steam, repair_localconfigs
        s = Steam(cfg.steam_root or None)
        if not s.ok:
            print(_('Keine Steam-Installation gefunden.'))
            return 1
        if s.is_running():
            print(_('Steam läuft noch. Bitte Steam komplett beenden (Steam → Beenden) und erneut ausführen.'))
            return 1
        msgs = repair_localconfigs(s.root)
        for _restored, m in msgs:
            print(m)
        if any(restored for restored, _m in msgs):
            print(_('\nFertig. Steam jetzt starten. Die Startoption beim Donor ist damit wieder auf dem alten Stand – trag sie in Steam selbst wieder ein (Eigenschaften → Startoptionen).'))
        else:
            print(_('\nNichts zu reparieren – RPT Anywhere hat Steams Einstellungen nicht verändert.'))
        return 0
    if args.diagnose:
        return diagnose(cfg)
    if args.print_launch_option:
        from .gui.pages import donor_launch_options
        print(donor_launch_options())
        return 0

    run_id = None
    if args.run:
        key = args.run.lower()
        g = next((x for x in cfg.games if x.id == args.run or x.name.lower() == key), None)
        if not g and args.run.isdigit():
            run_id = f"steam:{args.run}"
        elif g:
            run_id = g.id
        else:
            print(_('Spiel nicht gefunden: {run}').format(run=args.run), file=sys.stderr)
            return 2

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(cfgmod.APP_ID)
    app.setApplicationDisplayName(cfgmod.APP_NAME)
    app.setDesktopFileName(cfgmod.APP_ID)
    f = QFont()
    f.setPointSizeF(10)
    app.setFont(f)

    server_name = f"{cfgmod.APP_ID}-{os.getuid()}"

    # ---- talk to an already running instance
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if sock.waitForConnected(400):
        sock.write((json.dumps({"cmd": "show", "run": run_id}) + "\n").encode())
        sock.flush()
        sock.waitForBytesWritten(1000)
        sock.disconnectFromServer()
        return 0  # the running instance takes care of it

    from .log import setup as setup_log
    setup_log()
    from .gui import style
    from .gui.app import MainWindow
    app.setStyleSheet(style.QSS)
    win = MainWindow()

    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    def on_conn():
        c = server.nextPendingConnection()

        def read():
            if not c.canReadLine():
                return
            try:
                m = json.loads(bytes(c.readLine()).decode())
            except Exception:
                return
            win.showNormal()
            win.raise_()
            win.activateWindow()
            if m.get("run"):
                win.launch_id(m["run"])
        c.readyRead.connect(read)
        read()  # data may already be buffered
    server.newConnection.connect(on_conn)

    win.show()
    if run_id:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(800, lambda: win.launch_id(run_id))
    rc = app.exec()
    server.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
