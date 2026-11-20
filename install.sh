#!/usr/bin/env bash
# Installs RPT Anywhere for the current user (no root needed).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/rpt-anywhere/app"
BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

say() { printf '\033[1;38;5;209m▸\033[0m %s\n' "$*"; }

command -v python3 >/dev/null || { echo "python3 is missing – please install it first."; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo "Python 3.9 or newer is required."; exit 1; }

say "Copying to $DEST"
mkdir -p "$DEST"
rm -rf "$DEST/rpt_anywhere" "$DEST/bin"
cp -r "$SRC/rpt_anywhere" "$SRC/bin" "$DEST/"
find "$DEST" -name '__pycache__' -prune -exec rm -rf {} +

if python3 -c 'import PySide6.QtWidgets, PySide6.QtNetwork' 2>/dev/null; then
    say "Using the system PySide6."
    rm -rf "$DEST/.venv"
else
    say "Setting up a Python environment with PySide6 (one-time, ~100 MB) …"
    python3 -m venv "$DEST/.venv" || {
        echo "python3-venv is missing. Debian/Ubuntu: sudo apt install python3-venv"; exit 1; }
    "$DEST/.venv/bin/pip" install --quiet --upgrade pip
    "$DEST/.venv/bin/pip" install --quiet "PySide6-Essentials>=6.5"
fi

mkdir -p "$BIN" "$APPS" "$ICONS"
ln -sf "$DEST/bin/rpt-anywhere" "$BIN/rpt-anywhere"
cp "$DEST/rpt_anywhere/assets/icon.svg" "$ICONS/rpt-anywhere.svg"
cat > "$APPS/rpt-anywhere.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=RPT Anywhere
GenericName=Remote Play Together Launcher
Comment=Steam Remote Play Together for any game
Comment[de]=Steam Remote Play Together für jedes Spiel
Exec=$BIN/rpt-anywhere %F
Icon=rpt-anywhere
Terminal=false
Categories=Game;Network;
StartupWMClass=rpt-anywhere
Keywords=steam;remote;play;together;coop;proton;
DESK
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true

say "Done!"
echo
echo "  Start:  rpt-anywhere   (or from your application menu)"
echo "  Launch option for your donor game in Steam:"
echo
echo "      \"$BIN/rpt-anywhere\" --donor # %command%"
echo
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  Note: $BIN is not in your PATH.";; esac
