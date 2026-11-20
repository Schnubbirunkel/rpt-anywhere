#!/usr/bin/env bash
# Removes RPT Anywhere (your settings and prefixes are kept).
set -eu
D="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -rf "$D/rpt-anywhere/app"
rm -f "$HOME/.local/bin/rpt-anywhere" "$D/applications/rpt-anywhere.desktop" \
      "$D/icons/hicolor/scalable/apps/rpt-anywhere.svg"
echo "Removed. Your settings are still in ~/.config/rpt-anywhere and the Proton"
echo "prefixes in $D/rpt-anywhere/prefixes – delete them manually if you like."
echo "Don't forget to clear the launch option of your donor game in Steam."
