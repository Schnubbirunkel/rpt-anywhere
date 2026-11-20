# Changelog

## [0.4.1] – 2026-11-20

### Fixed
- **Guest links from the app now arrive.** Steam callbacks are received through the classic `SteamAPI_RegisterCallback` mechanism, which works with every Steamworks SDK version. Before, the agent relied on "manual dispatch", which silently failed with some `libsteam_api.so` builds, so Steam created the link but the app never saw it.
- **Inviting a friend from the app works.** 64-bit Steam IDs overflowed a 32-bit Qt signal when the invite button was clicked.
- **Proton games are no longer cut off from Steam.** The leftover cleanup could terminate Proton's `steam.exe` bridge ~15 s after the start because the actual game runs in its own process group. Games are now tracked through all descendants of the agent (which acts as a child subreaper like Steam's own `reaper`); the launched process is never killed automatically while it is alive. Guests that were stuck at "Please wait" were affected by this.
- *Stop game* now also ends game processes that run in their own process group or session.

### Improved
- The agent picks the best available `libsteam_api.so` (newest Remote Play interface, libraries shipped with games preferred over `node_modules` copies) instead of keeping a previously saved one.
- The *Steam overlay* button opens Steam's own Remote Play Together window (`ShowRemotePlayTogetherUI`). If no guest link arrives within 15 s, that window opens automatically so the link can be created there.
- More diagnostics in the donor placeholder log: Remote Play interface version, callback mode, received callbacks, appinfo header values of unreadable files.

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
