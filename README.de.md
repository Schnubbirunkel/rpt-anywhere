<div align="center">

<img src="rpt_anywhere/assets/icon.svg" width="96" alt="RPT-Anywhere-Symbol">

# RPT Anywhere

**Steam Remote Play Together für _jedes_ Spiel unter Linux**

Steam-Spiele ohne Remote-Play-Together-Unterstützung, Windows-Spiele von GOG/Epic/itch.io über Proton, native Linux-Spiele, Emulatoren oder beliebige Befehle – streame sie mit Steams Remote Play Together an deine Freunde, lade sie mit einem Klick ein oder schick einen Gast-Link.

[![Release](https://img.shields.io/github/v/release/Schnubbirunkel/rpt-anywhere)](https://github.com/Schnubbirunkel/rpt-anywhere/releases/latest)
[![Lizenz: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
![Plattform: Linux](https://img.shields.io/badge/platform-Linux-orange)

[English](README.md) · **Deutsch**

![Bibliothek mit laufender Session, verbundenem Mitspieler und Gast-Link](docs/screenshots/01-library-de.png)

</div>

## Funktionen

- **Jedes Spiel** – Steam-Bibliothek, Windows-Spiele über Proton (auch GE-Proton), Wine, native Linux-Programme, AppImages, Emulatoren oder beliebige Shell-Befehle
- **Einladen mit einem Klick** – Freundesliste mit Avataren und Online-Status, Gast-Links für Leute ohne Steam-Account (landen automatisch in der Zwischenablage), Liste der verbundenen Mitspieler
- **Spiele wechseln, ohne dass jemand rausfliegt** – die Remote-Play-Session bleibt offen, wenn ein Spiel endet; einfach das nächste starten
- **Schöne Bibliothek** – Cover, Favoriten, Suche und Filter, Spiele per Drag & Drop hinzufügen, Import deiner Nicht-Steam-Verknüpfungen aus Steam
- **Geführte Einrichtung** – zeigt, welche installierten Spiele Remote Play Together unterstützen und sich als Donor eignen, und trägt die Startoption auf Wunsch selbst ein (nur diese eine Zeile, mit Backup)
- **Live-Logs** in der App, ein **Notaus** und ein **Diagnose**-Befehl für die Fehlersuche
- Oberfläche auf **Englisch und Deutsch** (in den Einstellungen umschaltbar, weitere Sprachen willkommen)
- Nutzt nur **offizielle Schnittstellen**: Steams Startoptionen und die öffentliche Steamworks-API – keine Eingriffe in den Steam-Client

## So funktioniert's

Steam erlaubt Remote Play Together nur für Spiele, die es selbst gestartet hat und die es unterstützen. RPT Anywhere nutzt das **Donor-Prinzip** (wie [RemotePlayDetached](https://github.com/smaTc/RemotePlayDetached) und [cooplauncher](https://github.com/MBaliver/cooplauncher)):

1. Du wählst ein installiertes Steam-Spiel **mit** Remote-Play-Together-Unterstützung als *Donor* und setzt dessen Startoption auf RPT Anywhere.
2. Startet Steam den Donor, startet es stattdessen den **Agent** von RPT Anywhere. Der braucht nur die Python-Standardbibliothek und läuft deshalb auch in Steams Runtime-Container (bei Proton-Spielen immer der Fall).
3. Die App sagt dem Agent, was er starten soll. Der Agent startet dein Spiel **als echten Unterprozess von Steam** mit Steams eigener Umgebung (Overlay, AppID, Steam Input) – Steam streamt es also als Donor-Spiel. Einladungen, Gast-Links und die Freundesliste laufen ebenfalls über den Agent, mit der offiziellen `ISteamRemotePlay`-API.

```
 RPT-Anywhere-App (GUI) ──Aufträge──▶ Agent (im Donor-Prozess von Steam) ──startet──▶ dein Spiel
          ▲                                   │                                      │
          └────── Status, Freunde, Gäste ◀────┘          Steam-Overlay + Stream ◀────┘
```

## Installation

**Voraussetzungen:** Linux, der Steam-Client (am besten das Paket deiner Distribution), Python 3.9+. PySide6 wird automatisch installiert, falls dein System es nicht mitbringt.

1. `rpt-anywhere-<version>.tar.gz` vom [neuesten Release](https://github.com/Schnubbirunkel/rpt-anywhere/releases/latest) herunterladen.
2. Entpacken und den Installer starten:
   ```sh
   tar xzf rpt-anywhere-*.tar.gz
   cd rpt-anywhere-*/
   ./install.sh
   ```

Installiert wird nach `~/.local/share/rpt-anywhere/app`, mit dem Befehl `rpt-anywhere` in `~/.local/bin` und einem Eintrag im Anwendungsmenü. Root-Rechte sind nicht nötig. Zum Aktualisieren einfach `install.sh` der neuen Version ausführen; `uninstall.sh` entfernt alles wieder.

Aus dem Quellcode: `git clone https://github.com/Schnubbirunkel/rpt-anywhere && cd rpt-anywhere && ./install.sh` (oder direkt `./bin/rpt-anywhere` starten).

## Einrichtung (einmalig, ca. 2 Minuten)

Die Sprache stellst du unter Settings → *Language / Sprache* auf Deutsch. Dann RPT Anywhere → **Einrichtung**:

1. **Donor wählen.** Spiele mit **RPT ✓** unterstützen laut Steam-Store Remote Play Together. Am besten ein kleines Spiel, das du sonst nicht spielst – solange es Donor ist, startet es nicht mehr selbst.
2. **Startoption setzen.** Entweder **Automatisch eintragen** (Steam muss beendet sein; es wird ein Backup von `localconfig.vdf` angelegt und nur diese eine Zeile geändert) oder in Steam → Rechtsklick auf den Donor → *Eigenschaften → Startoptionen* einfügen:
   ```
   "/home/DU/.local/bin/rpt-anywhere" --donor # %command%
   ```
   Danach Steam einmal neu starten, damit es die Option sicher übernimmt.
3. **libsteam_api.so** (für Einladungen aus der App) wird automatisch in deinen installierten Spielen gesucht. Ohne sie lädst du wie gewohnt über das Steam-Overlay (Shift+Tab) ein.

## Benutzung

- **Spiele hinzufügen:** **＋ Spiel hinzufügen**, Datei ins Fenster ziehen oder **Steam-Verknüpfungen importieren**. Startarten: *Linux nativ*, *Proton* (eigener Prefix pro Spiel, Steam Linux Runtime wie bei Steam selbst), *Wine*, *Befehl / Skript*. Installierte Steam-Spiele erscheinen automatisch.
- **Klick auf ein Spiel:** Ist die App noch nicht mit Steam verbunden, lässt sie Steam den Donor starten, verbindet sich mit dem Agent und startet dann dein Spiel.
- **Einladen** im rechten Bereich: Freunde direkt einladen oder einen **Gast-Link erstellen**. Verbundene Mitspieler stehen unter *Mitspieler*.
- **Rechtsklick auf ein Spiel:** bearbeiten, Favorit, Ordner öffnen, Log anzeigen, ausblenden.

### Kommandozeile

```
rpt-anywhere --run "Name oder ID"    # Spiel starten (über Steam/Donor, falls nötig)
rpt-anywhere --run 413150            # Steam-Spiel per AppID
rpt-anywhere --list                  # eigene Spiele auflisten
rpt-anywhere --diagnose              # Steam-Erkennung prüfen: Bibliotheken, Startdateien, Proton, Donor
rpt-anywhere --reset                 # Notaus: Session, Agent und letztes Spiel beenden
rpt-anywhere --repair-steam-config   # localconfig.vdf aus der ersten Sicherung von RPT Anywhere wiederherstellen
rpt-anywhere --print-launch-option   # Startoption für den Donor ausgeben
```

Die Ausgabe der Kommandozeile folgt der in der App eingestellten Sprache; mit `RPTA_LANG=de` lässt sie sich auch direkt wählen.

## Fehlersuche

- **Beim Start des Donors passiert nichts / „Donor nicht verbunden“:** Nach dem Setzen der Startoption Steam einmal neu starten. Im Reiter *Logs* → *Donor-Platzhalter* steht, was Steam tatsächlich gestartet hat (Version, Container ja/nein).
- **Steam zeigt den Donor weiter als laufend an:** Ein Prozess der letzten Session lebt noch. Notaus (Einstellungen oder Reiter Logs) oder `rpt-anywhere --reset`.
- **Startdatei nicht gefunden:** `rpt-anywhere --diagnose` zeigt, welche Datei für jedes Spiel erkannt wurde. Die Startkonfiguration kommt aus Steams App-Cache (`appinfo.vdf` und das neuere aufgeteilte Format `appcache/appinfo/appinfo_XX.vdf`), sonst durchsucht die App den Spielordner. Im Bearbeiten-Dialog kannst du die Datei immer selbst angeben.
- **Overlay fehlt im Spiel:** Programme, die im Steam-Container nicht sichtbar sind (z. B. `/usr/bin/…`, Wine), startet die App direkt auf dem System – dort kann das Overlay fehlen; die App weist dann darauf hin. Flatpak-/Snap-Apps laufen in einer eigenen Sandbox.
- **Die Maus des Gasts reagiert träge:** Eingaben überträgt Steam selbst, nicht RPT Anywhere; die Stream-Statistik zeigt meist die Netzwerk-Latenz als Ursache. Am meisten helfen eine Kabelverbindung oder ein Gamepad für den Gast.

## Grenzen

- Das Spiel braucht **lokalen Mehrspielermodus** – Gäste kommen als (virtuelle) Controller bzw. Maus/Tastatur an, genau wie bei normalem Remote Play Together.
- **Anti-Cheat und DRM-Launcher** (EA App, Ubisoft Connect, manche Steam-Spiele) können den Start auf diesem Weg verweigern. Bei Steam-Spielen hilft manchmal *Mit eigener Steam-AppID starten* im Bearbeiten-Dialog.
- **Flatpak-Steam** führt alles in seiner Sandbox aus; am zuverlässigsten ist das native Steam-Paket deiner Distribution.

## Weitere Sprache hinzufügen

1. `rpt_anywhere/locale/en.json` z. B. nach `fr.json` kopieren und die Werte übersetzen (die Schlüssel sind die deutschen Ausgangstexte; `{Platzhalter}` und `%s` unverändert lassen).
2. Die Sprache in `LANGUAGES` in `rpt_anywhere/i18n.py` eintragen.
3. `python3 tools/i18n_check.py` ausführen – meldet fehlende Texte und kaputte Platzhalter.

## Danke

Inspiriert von [RemotePlayDetached](https://github.com/smaTc/RemotePlayDetached), [cooplauncher](https://github.com/MBaliver/cooplauncher) und [RemotePlayWhatever](https://github.com/m4dEngi/RemotePlayWhatever). Die Formatbeschreibung von [SteamAppInfo](https://github.com/ValveResourceFormat/SteamAppInfo) war sehr hilfreich.

## Lizenz

[GPL-3.0](LICENSE). RPT Anywhere steht in keiner Verbindung zu Valve. Steam und Remote Play Together sind Marken der Valve Corporation.
