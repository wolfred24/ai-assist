#!/usr/bin/env bash
# Instala el Asistente IA en el escritorio del usuario actual.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_EXEC="$DIR/ai_assist.py"
APP_ID="ai-assist"
KEY_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/$APP_ID/"
KEY_SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KEY_PATH"
SHORTCUT="<Ctrl><Alt>a"

chmod +x "$APP_EXEC"
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/bin" "$HOME/.config/autostart"

cat > "$HOME/.local/share/applications/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Asistente IA
Comment=Consulta rápida a la IA local (Ollama)
Exec="$APP_EXEC" --toggle
Icon=chat-message-new-symbolic
Terminal=false
Categories=Utility;
StartupNotify=false
EOF

cat > "$HOME/.config/autostart/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Asistente IA
Comment=Asistente IA en la bandeja del sistema
Exec="$APP_EXEC" --tray
Icon=chat-message-new-symbolic
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

ln -sf "$APP_EXEC" "$HOME/.local/bin/$APP_ID"

AI_ASSIST_EXEC="$APP_EXEC" KEY_PATH="$KEY_PATH" python3 - <<'PY'
import ast
import os
import subprocess

base = "org.gnome.settings-daemon.plugins.media-keys"
key = "custom-keybindings"
path = os.environ["KEY_PATH"]
exec_path = os.environ["AI_ASSIST_EXEC"]

raw = subprocess.check_output(["gsettings", "get", base, key], text=True).strip()
try:
    entries = [] if raw in ("@as []", "[]") else list(ast.literal_eval(raw))
except (ValueError, SyntaxError):
    entries = []

if path not in entries:
    entries.append(path)
    value = "[" + ", ".join("'%s'" % item for item in entries) + "]"
    subprocess.check_call(["gsettings", "set", base, key, value])

schema = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:" + path
subprocess.check_call(["gsettings", "set", schema, "name", "Asistente IA"])
subprocess.check_call(["gsettings", "set", schema, "command", exec_path + " --toggle"])
subprocess.check_call(["gsettings", "set", schema, "binding", "<Ctrl><Alt>a"])
PY

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

if ! pgrep -f "[a]i_assist\.py" >/dev/null 2>&1; then
    setsid nohup "$APP_EXEC" --tray >/dev/null 2>&1 < /dev/null &
fi

echo "Instalado."
echo "  Icono en la bandeja: Asistente IA"
echo "  Atajo global: Ctrl+Alt+A"
echo "  Comando: ai-assist --ask \"tu pregunta\""
echo "  Config: $HOME/.config/ai-assist/config.json"
