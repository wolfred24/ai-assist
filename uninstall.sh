#!/usr/bin/env bash
# Desinstala el Asistente IA (no borra el código ni la configuración).
set -euo pipefail

APP_ID="ai-assist"
KEY_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/$APP_ID/"

pkill -f "[a]i_assist\.py" 2>/dev/null || true

rm -f "$HOME/.local/share/applications/$APP_ID.desktop"
rm -f "$HOME/.config/autostart/$APP_ID.desktop"
rm -f "$HOME/.local/bin/$APP_ID"

KEY_PATH="$KEY_PATH" python3 - <<'PY'
import ast
import os
import subprocess

base = "org.gnome.settings-daemon.plugins.media-keys"
key = "custom-keybindings"
path = os.environ["KEY_PATH"]

raw = subprocess.check_output(["gsettings", "get", base, key], text=True).strip()
try:
    entries = [] if raw in ("@as []", "[]") else list(ast.literal_eval(raw))
except (ValueError, SyntaxError):
    entries = []

if path in entries:
    entries.remove(path)
    value = "[" + ", ".join("'%s'" % item for item in entries) + "]"
    subprocess.check_call(["gsettings", "set", base, key, value])
PY

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

echo "Desinstalado. El código sigue en $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)."
