# Changelog

## [0.4.0] – 2026-11-20

First public release.

### Highlights
- Steam **Remote Play Together for any game** on Linux via a donor game: Steam library titles, Windows games via Proton/GE-Proton or Wine, native Linux programs, emulators and arbitrary commands.
- **Donor agent** inside Steam's process (Python standard library only, also runs inside Steam's runtime/Proton container): games start as real children of Steam with Steam's overlay, AppID and Steam Input environment.
- **Invites from the app** through the official Steamworks API: friends list with avatars and status, guest links (copied to the clipboard), list of connected players.
- **Switch games without ending the session** – friends stay connected.
- **Guided setup** that shows which installed games support Remote Play Together and sets the donor's launch option by changing exactly one line of `localconfig.vdf` (verified before saving, with backup).
- Reads Steam's app cache in all current formats, including the **split `appcache/appinfo/appinfo_XX.vdf` (v42)** introduced by newer Steam clients; case-insensitive executable resolution and a fallback search of the game folder.
- **English and German interface** (English default, switchable in the settings; command line follows the same setting).
- **Live logs** tab, **emergency stop** (`--reset`), **diagnostics** (`--diagnose`) and `--repair-steam-config` for configurations touched by earlier test builds.

### Notes
- Earlier versions (0.1–0.3.5) were internal test builds. If you used one of them and clicked *Set automatically*, run `rpt-anywhere --diagnose`; if it reports `damaged=True`, quit Steam and run `rpt-anywhere --repair-steam-config`.

### Install
Download `rpt-anywhere-0.4.0.tar.gz`, unpack it and run `./install.sh`. See the [README](https://github.com/Schnubbirunkel/rpt-anywhere#readme) for setup.
