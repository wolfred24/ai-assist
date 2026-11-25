#!/usr/bin/python3
"""Asistente IA local para GNOME: icono de bandeja + atajo global + voz.

Motor: Ollama (http://127.0.0.1:11434). Interfaz: GTK3 + AyatanaAppIndicator.
El dictado delega en `voiceio`, que ya está instalado y configurado en español.
"""

import html
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

APP_ID = "org.local.AiAssist"
APP_NAME = "Asistente IA"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ai-assist"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "ai-assist"
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = DATA_DIR / "history.jsonl"
LOG_PATH = DATA_DIR / "ai-assist.log"
CONTEXT_PATH = DATA_DIR / "system-context.md"

DEFAULTS = {
    "base_url": "http://127.0.0.1:11434",
    "model": "qwen3.5:9b",
    "system_prompt": (
        "Eres un asistente útil, directo y preciso. Respondes en el idioma del "
        "usuario, normalmente español. Sé breve salvo que se pida detalle. "
        "Usa Markdown para estructurar (títulos, listas, negritas, código) "
        "cuando ayude a leer."
    ),
    "temperature": 0.7,
    "timeout": 600,
    "disable_thinking": True,
    "include_system_context": True,
    "tts": {"enabled": False, "language": "es"},
}


# --------------------------------------------------------------------------- #
# Configuración e historial
# --------------------------------------------------------------------------- #

def _merge(base, extra):
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            merged = _merge(DEFAULTS, stored)
            if merged != stored:
                save_config(merged)  # añade claves nuevas sin perder las tuyas
            return merged
        except (json.JSONDecodeError, OSError) as exc:
            logging.warning("config inválida, usando valores por defecto: %s", exc)
    save_config(DEFAULTS)
    return dict(DEFAULTS)


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def save_history(model, user, assistant):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": GLib.DateTime.new_now_local().format_iso8601(),
                "model": model,
                "user": user,
                "assistant": assistant,
            }, ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.warning("no se pudo guardar el historial: %s", exc)


# --------------------------------------------------------------------------- #
# Contexto real del equipo
# --------------------------------------------------------------------------- #

def _cmd(args, timeout=2):
    """Ejecuta un comando y devuelve su primera línea útil (o cadena vacía)."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    output = (result.stdout or result.stderr or "").strip()
    return output.splitlines()[0].strip() if output else ""


def _os_pretty_name():
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def _cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "desconocido"


def _ram_gib():
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024 / 1024)
    except (OSError, ValueError):
        pass
    return 0


def _gpu_info():
    name = "desconocida"
    try:
        output = subprocess.run(["lspci"], capture_output=True, text=True,
                                timeout=2, check=False).stdout
        for line in output.splitlines():
            low = line.lower()
            if "vga compatible" in low or "3d controller" in low:
                name = line.split(":", 2)[-1].strip()
                break
    except (OSError, subprocess.SubprocessError):
        pass
    vram = ""
    try:
        for path in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
            total = int(path.read_text().strip())
            if total > 0:
                vram = f", {round(total / 1024 ** 3)} GB de VRAM"
                break
    except (OSError, ValueError):
        pass
    return name + vram


def _tool_list():
    tools = ["ollama", "docker", "python3", "git", "ffmpeg", "voiceio",
             "notify-send", "spd-say", "flatpak", "code", "opencode"]
    present = [tool for tool in tools if shutil.which(tool)]
    return ", ".join(present) if present else "ninguna relevante"


def generate_context(cfg):
    """Recolecta datos reales del equipo y los guarda como texto para el prompt."""
    now = GLib.DateTime.new_now_local()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "desconocido")
    session = os.environ.get("XDG_SESSION_TYPE", "desconocido")
    shell_version = _cmd(["gnome-shell", "--version"]).replace("GNOME Shell ", "")
    free_disk = round(shutil.disk_usage("/").free / 1024 ** 3)
    file_manager = "Nautilus (GNOME)" if shutil.which("nautilus") else "desconocido"

    lines = [
        "## Contexto del equipo (generado automáticamente, datos reales)",
        f"- Fecha y hora local: {now.format('%Y-%m-%d %H:%M %Z')}",
        f"- Sistema operativo: {_os_pretty_name()}",
        f"- Kernel: {platform.release()} ({platform.machine()})",
        f"- Escritorio: {desktop} / {session}"
        + (f", GNOME Shell {shell_version}" if shell_version else ""),
        f"- Gestor de archivos: {file_manager}",
        f"- CPU: {_cpu_model()} ({os.cpu_count()} hilos)",
        f"- RAM: {_ram_gib()} GiB",
        f"- GPU: {_gpu_info()}",
        f"- Disco libre en /: {free_disk} GB",
        f"- Usuario: {os.environ.get('USER', 'debian')}, inicio: {Path.home()}",
        f"- Shell: {os.environ.get('SHELL', '/bin/bash')}",
        f"- Ollama: {_cmd(['ollama', '--version'])} en {cfg['base_url']}, "
        f"modelo actual: {cfg['model']}",
        f"- Herramientas instaladas: {_tool_list()}",
        "",
        "### Atajos de esta máquina",
        "- Asistente IA: Ctrl+Alt+A abre/oculta; Ctrl+Alt+V dicta (voiceio).",
        "- GNOME: Super = vista general; Alt+Tab = cambiar ventana; "
        "Ctrl+Alt+T = terminal; Ctrl+Alt+Flechas = mover ventana de escritorio.",
        "- Nautilus (archivos): Ctrl+Shift+N = carpeta nueva; Ctrl+L = escribir ruta; "
        "Ctrl+H = mostrar ocultos; Ctrl+F = buscar.",
        "",
        "### Reglas",
        "- Asume SIEMPRE este entorno (Debian + GNOME + Wayland) salvo que el usuario "
        "diga explícitamente que pregunta por otro sistema o equipo.",
        "- No des instrucciones de Windows ni macOS como opción principal; si el "
        "usuario no especifica, responde para GNOME/Linux.",
        "- Los atajos que propongas deben existir en GNOME/Nautilus, no en Windows.",
    ]
    text = "\n".join(lines) + "\n"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONTEXT_PATH.write_text(text, encoding="utf-8")
    except OSError as exc:
        logging.warning("no se pudo guardar el contexto: %s", exc)
    return text


def get_context(cfg, refresh=False, max_age=3600):
    """Devuelve el contexto cacheado; lo regenera si está viejo o se pide."""
    if not refresh and CONTEXT_PATH.exists():
        try:
            if time.time() - CONTEXT_PATH.stat().st_mtime < max_age:
                return CONTEXT_PATH.read_text(encoding="utf-8")
        except OSError:
            pass
    return generate_context(cfg)


def build_messages(cfg, history, context=None):
    system = cfg["system_prompt"]
    if cfg.get("include_system_context", True):
        context = context if context is not None else get_context(cfg)
        if context:
            system += "\n\n" + context
    return [{"role": "system", "content": system}] + list(history)


# --------------------------------------------------------------------------- #
# Cliente Ollama
# --------------------------------------------------------------------------- #

def stream_chat(cfg, messages, on_chunk, model=None):
    """Envía una conversación a Ollama y entrega el texto por trozos."""
    url = cfg["base_url"].rstrip("/") + "/api/chat"
    model = model or cfg["model"]
    base = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": cfg["temperature"]},
    }
    attempts = [{**base, "think": False}, base] if cfg.get("disable_thinking", True) else [base]

    last_error = None
    for payload in attempts:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=cfg["timeout"]) as response:
                for raw in response:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        raise RuntimeError(obj["error"])
                    piece = (obj.get("message") or {}).get("content") or ""
                    if piece:
                        on_chunk(piece)
                    if obj.get("done"):
                        return
            return
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {body[:300]}")
            # Algunos modelos no aceptan el parámetro "think": reintenta sin él.
            if exc.code == 400 and "think" in body.lower():
                continue
            raise last_error
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"No puedo conectar con Ollama en {cfg['base_url']}. "
                f"Comprueba el servicio: systemctl status ollama ({exc.reason})"
            ) from exc
    raise last_error or RuntimeError("Ollama no devolvió respuesta")


def cli_ask(cfg, prompt):
    messages = build_messages(cfg, [{"role": "user", "content": prompt}])

    def emit(text):
        sys.stdout.write(text)
        sys.stdout.flush()

    try:
        stream_chat(cfg, messages, emit)
        sys.stdout.write("\n")
        return 0
    except Exception as exc:  # noqa: BLE001 - mensaje claro para el usuario
        print(f"\nError: {exc}", file=sys.stderr)
        return 1


def fetch_models(cfg):
    url = cfg["base_url"].rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
        return sorted(model["name"] for model in data.get("models", []))
    except Exception as exc:  # noqa: BLE001 - la bandeja debe seguir viva
        logging.warning("no se pudo listar modelos: %s", exc)
        return []


# --------------------------------------------------------------------------- #
# Voz
# --------------------------------------------------------------------------- #

def dictate():
    """Delega el dictado en voiceio (escribe en el campo con foco)."""
    try:
        subprocess.Popen(
            ["voiceio", "toggle"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        notify("voiceio no está instalado")


def clean_for_speech(text, limit=1200):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[`*_#>|]", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        return cut + "…"
    return text


def speak(text):
    try:
        subprocess.run(["spd-say", "-S"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["spd-say", "-l", "es", clean_for_speech(text)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        logging.warning("spd-say no está disponible")


def notify(message):
    try:
        subprocess.Popen(["notify-send", APP_NAME, message],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- #
# Markdown -> Pango (render incremental)
# --------------------------------------------------------------------------- #

_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_STRIKE = re.compile(r"~~(.+?)~~")
_ITALIC_STAR = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)")
_ITALIC_UNDER = re.compile(r"(?<![\w])_([^_\n]+)_(?![\w])")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_CODE_FMT = '<span font_family="monospace" foreground="#c061cb">{}</span>'


def inline_markup(text):
    """Convierte el Markdown en línea a marcado Pango (texto ya escapado)."""
    text = html.escape(text, quote=False)
    stash = []

    def keep_code(match):
        stash.append(match.group(1))
        return f"\x01{len(stash) - 1}\x02"

    text = _INLINE_CODE.sub(keep_code, text)
    text = _LINK.sub(
        lambda m: '<span foreground="#1c71d8" underline="single">{}</span>{}'.format(
            m.group(1),
            "" if m.group(2) in m.group(1) else " ({})".format(m.group(2))),
        text)
    text = _BOLD.sub(lambda m: "<b>{}</b>".format(m.group(1) or m.group(2)), text)
    text = _STRIKE.sub(lambda m: "<s>{}</s>".format(m.group(1)), text)
    text = _ITALIC_STAR.sub(lambda m: "<i>{}</i>".format(m.group(1)), text)
    text = _ITALIC_UNDER.sub(lambda m: "<i>{}</i>".format(m.group(1)), text)
    text = re.sub(r"\x01(\d+)\x02",
                  lambda m: _CODE_FMT.format(stash[int(m.group(1))]), text)
    return text


class MarkdownStream:
    """Renderiza Markdown sobre un Gtk.TextBuffer a medida que llega el texto.

    Las líneas completas se formatean; la línea en curso se redibuja al final,
    de modo que el usuario no ve los símbolos de Markdown durante la respuesta.
    """

    def __init__(self, buffer):
        self.buffer = buffer
        self.pending = ""
        self.in_code = False
        self.done = False
        self.tail = buffer.create_mark(None, buffer.get_end_iter(), True)

    def feed(self, text):
        if self.done:
            return
        self._delete_tail()
        self.pending += text
        parts = self.pending.split("\n")
        self.pending = parts.pop()
        for line in parts:
            self._emit_line(line)
        self._move_tail()
        if self.pending:
            self._render_tail()

    def finish(self):
        if self.done:
            return
        self._delete_tail()
        if self.pending:
            self._emit_line(self.pending)
            self.pending = ""
        self._move_tail()
        self.done = True
        self.buffer.delete_mark(self.tail)

    # ------------------------------ interno -------------------------------- #

    def _delete_tail(self):
        start = self.buffer.get_iter_at_mark(self.tail)
        self.buffer.delete(start, self.buffer.get_end_iter())

    def _move_tail(self):
        self.buffer.move_mark(self.tail, self.buffer.get_end_iter())

    def _insert(self, text, tags=("body",)):
        buffer = self.buffer
        offset = buffer.get_char_count()
        buffer.insert(buffer.get_iter_at_offset(offset), text)
        end = buffer.get_end_iter()
        for tag in tags:
            buffer.apply_tag_by_name(tag, buffer.get_iter_at_offset(offset), end)

    def _insert_markup(self, text, tags=("body",)):
        buffer = self.buffer
        offset = buffer.get_char_count()
        try:
            buffer.insert_markup(
                buffer.get_iter_at_offset(offset), inline_markup(text), -1)
        except Exception:  # markup inválido: nunca romper la respuesta
            buffer.insert(buffer.get_iter_at_offset(offset), text)
        end = buffer.get_end_iter()
        for tag in tags:
            buffer.apply_tag_by_name(tag, buffer.get_iter_at_offset(offset), end)

    def _emit_line(self, line):
        stripped = line.strip()

        if self.in_code:
            if stripped.startswith("```"):
                self.in_code = False
            else:
                self._insert(line + "\n", ("code",))
            return

        if stripped.startswith("```"):
            self.in_code = True
            return

        if not stripped:
            self._insert("\n")
            return

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)), 3)
            self._insert_markup(heading.group(2), ("h%d" % level,))
            self._insert("\n")
            return

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            self._insert("─" * 36 + "\n", ("rule",))
            return

        if stripped.startswith("|") and stripped.endswith("|"):
            if not re.match(r"^\|[\s:|-]+\|$", stripped):
                cells = [cell.strip() for cell in stripped.strip("|").split("|")]
                self._insert("   ".join(cells) + "\n", ("table",))
            return

        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if bullet:
            depth = min(len(bullet.group(1)) // 2, 2)
            self._insert("  " * depth + "• ")
            self._insert_markup(bullet.group(2))
            self._insert("\n")
            return

        numbered = re.match(r"^(\s*)(\d+[.)])\s+(.*)$", line)
        if numbered:
            depth = min(len(numbered.group(1)) // 2, 2)
            self._insert("  " * depth + numbered.group(2) + " ")
            self._insert_markup(numbered.group(3))
            self._insert("\n")
            return

        quote = re.match(r"^>\s?(.*)$", stripped)
        if quote:
            self._insert("│ ", ("quote",))
            self._insert_markup(quote.group(1), ("quote",))
            self._insert("\n")
            return

        self._insert_markup(line)
        self._insert("\n")

    def _render_tail(self):
        if not self.pending:
            return
        if self.in_code:
            self._insert(self.pending, ("code",))
        else:
            self._insert_markup(self.pending)


# --------------------------------------------------------------------------- #
# Interfaz
# --------------------------------------------------------------------------- #

class AssistantApp(Gtk.Application):
    def __init__(self, cfg):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.cfg = cfg
        self.messages = []
        self.busy = False
        self.start_hidden = False
        self._first_invocation = True
        self.model_menu = None
        self.stream = None
        self.context = ""
        self.session = 0

    # ---------------------------- ciclo de vida ---------------------------- #

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.context = get_context(self.cfg)
        self._build_window()
        self._build_indicator()
        threading.Thread(target=self._reload_models, daemon=True).start()

    def do_activate(self):
        self.show_window()

    def do_command_line(self, command_line):
        args = command_line.get_arguments()
        first = self._first_invocation
        self._first_invocation = False

        if first and "--tray" in args:
            self.start_hidden = True
        if "--toggle" in args:
            self.toggle()
        elif "--new" in args:
            self.new_chat()
            self.show_window()
        elif not (first and self.start_hidden):
            self.show_window()
        return 0

    # ------------------------------- ventana ------------------------------- #

    def _build_window(self):
        self.window = Gtk.ApplicationWindow(application=self, title=APP_NAME)
        self.window.set_default_size(640, 540)
        self.window.set_size_request(420, 320)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.set_border_width(0)
        self.window.connect("delete-event", self._on_close)
        self.window.connect("key-press-event", self._on_key_press)

        header = Gtk.HeaderBar(show_close_button=True)
        header.set_title(APP_NAME)
        header.set_subtitle(self.cfg["model"])
        self.header = header
        self.window.set_titlebar(header)

        self.mic_button = Gtk.Button.new_from_icon_name(
            "audio-input-microphone-symbolic", Gtk.IconSize.BUTTON)
        self.mic_button.set_tooltip_text("Dictar pregunta (voiceio: Ctrl+Alt+V)")
        self.mic_button.connect("clicked", lambda *_: self.dictate_clicked())
        header.pack_start(self.mic_button)

        self.new_button = Gtk.Button.new_from_icon_name(
            "document-new-symbolic", Gtk.IconSize.BUTTON)
        self.new_button.set_tooltip_text("Nueva conversación")
        self.new_button.connect("clicked", lambda *_: self.new_chat())
        header.pack_start(self.new_button)

        self.tts_button = Gtk.ToggleButton()
        self.tts_button.set_image(Gtk.Image.new_from_icon_name(
            "audio-volume-high-symbolic", Gtk.IconSize.BUTTON))
        self.tts_button.set_tooltip_text("Leer las respuestas en voz alta")
        self.tts_button.set_active(bool(self.cfg["tts"].get("enabled")))
        self.tts_button.connect("toggled", self._on_tts_toggled)
        header.pack_end(self.tts_button)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(8)
        self.window.add(box)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_hexpand(True)
        self.scroll.set_vexpand(True)
        box.pack_start(self.scroll, True, True, 0)

        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_left_margin(8)
        self.textview.set_right_margin(8)
        self.textview.set_top_margin(6)
        self.textview.set_bottom_margin(6)
        self.scroll.add(self.textview)

        self.buffer = self.textview.get_buffer()
        self.buffer.create_tag("user", weight=Pango.Weight.BOLD,
                               foreground="#3584e4", pixels_above_lines=12)
        self.buffer.create_tag("ai", weight=Pango.Weight.BOLD,
                               foreground="#26a269", pixels_above_lines=12)
        self.buffer.create_tag("body", pixels_below_lines=2)
        self.buffer.create_tag("error", foreground="#e01b24")
        self.buffer.create_tag("code", family="monospace",
                               foreground="#c061cb", pixels_below_lines=1)
        self.buffer.create_tag("h1", weight=Pango.Weight.BOLD,
                               scale=1.35, pixels_above_lines=10, pixels_below_lines=2)
        self.buffer.create_tag("h2", weight=Pango.Weight.BOLD,
                               scale=1.2, pixels_above_lines=8, pixels_below_lines=2)
        self.buffer.create_tag("h3", weight=Pango.Weight.BOLD,
                               scale=1.08, pixels_above_lines=6, pixels_below_lines=1)
        self.buffer.create_tag("quote", style=Pango.Style.ITALIC,
                               foreground="#77767b", pixels_below_lines=2)
        self.buffer.create_tag("rule", foreground="#9a9996")
        self.buffer.create_tag("table", family="monospace")

        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(input_row, False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Pregunta algo…  (Enter envía, Esc oculta)")
        self.entry.set_hexpand(True)
        self.entry.set_size_request(-1, 34)
        self.entry.connect("activate", lambda *_: self.send())
        input_row.pack_start(self.entry, True, True, 0)

        self.send_button = Gtk.Button(label="Enviar")
        self.send_button.get_style_context().add_class("suggested-action")
        self.send_button.connect("clicked", lambda *_: self.send())
        input_row.pack_start(self.send_button, False, False, 0)

        hint = Gtk.Label()
        hint.set_markup(
            "<small>Ctrl+Alt+A abre/oculta · micrófono o Ctrl+Alt+V dicta · "
            "Esc oculta · las respuestas se leen si activas el altavoz</small>")
        hint.set_line_wrap(True)
        hint.set_opacity(0.6)
        hint.set_xalign(0)
        box.pack_start(hint, False, False, 2)

        self._append_block("IA", "ai",
                           "Hola. Pregúntame lo que quieras; todo se ejecuta en tu "
                           "equipo con Ollama.\n")

    def _on_close(self, *_args):
        self.window.hide()
        return True

    def _on_key_press(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.hide()
            return True
        return False

    def _on_tts_toggled(self, button):
        enabled = button.get_active()
        self.cfg["tts"]["enabled"] = enabled
        save_config(self.cfg)
        if self.tts_item.get_active() != enabled:
            self.tts_item.set_active(enabled)

    # -------------------------------- bandeja ------------------------------ #

    def _build_indicator(self):
        self.indicator = AppIndicator.Indicator.new(
            "ai-assist",
            "chat-message-new-symbolic",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title(APP_NAME)

        menu = Gtk.Menu()

        ask = Gtk.MenuItem(label="Preguntar…  (Ctrl+Alt+A)")
        ask.connect("activate", lambda *_: self.toggle())
        menu.append(ask)

        voice = Gtk.MenuItem(label="Preguntar por voz")
        voice.connect("activate", lambda *_: self.ask_by_voice())
        menu.append(voice)

        self.tts_item = Gtk.CheckMenuItem(label="Leer respuestas")
        self.tts_item.set_active(bool(self.cfg["tts"].get("enabled")))
        self.tts_item.connect("toggled", self._on_tray_tts_toggled)
        menu.append(self.tts_item)

        menu.append(Gtk.SeparatorMenuItem())

        model_item = Gtk.MenuItem(label="Modelo")
        self.model_menu = Gtk.Menu()
        model_item.set_submenu(self.model_menu)
        menu.append(model_item)

        reload_models = Gtk.MenuItem(label="Recargar modelos")
        reload_models.connect(
            "activate",
            lambda *_: threading.Thread(target=self._reload_models, daemon=True).start())
        menu.append(reload_models)

        new = Gtk.MenuItem(label="Nueva conversación")
        new.connect("activate", lambda *_: (self.new_chat(), self.show_window()))
        menu.append(new)

        history = Gtk.MenuItem(label="Ver historial")
        history.connect("activate", lambda *_: self.open_history())
        menu.append(history)

        context_view = Gtk.MenuItem(label="Ver contexto del equipo")
        context_view.connect("activate", lambda *_: self.open_context())
        menu.append(context_view)

        context_reload = Gtk.MenuItem(label="Regenerar contexto")
        context_reload.connect("activate", lambda *_: self.regenerate_context())
        menu.append(context_reload)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Salir")
        quit_item.connect("activate", lambda *_: self.quit_app())
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)

    def _on_tray_tts_toggled(self, item):
        enabled = item.get_active()
        if self.tts_button.get_active() != enabled:
            self.tts_button.set_active(enabled)

    def _reload_models(self):
        models = fetch_models(self.cfg)
        GLib.idle_add(self._populate_models, models)

    def _populate_models(self, models):
        for child in self.model_menu.get_children():
            self.model_menu.remove(child)

        names = list(models)
        if self.cfg["model"] not in names:
            names.insert(0, self.cfg["model"])

        group = None
        for name in names:
            if group is None:
                item = Gtk.RadioMenuItem(label=name)
                group = item
            else:
                item = Gtk.RadioMenuItem.new_with_label_from_widget(group, name)
            item.set_active(name == self.cfg["model"])
            item.connect("toggled", self._on_model_changed, name)
            self.model_menu.append(item)
        self.model_menu.show_all()
        return False

    def _on_model_changed(self, item, name):
        if not item.get_active() or name == self.cfg["model"]:
            return
        self.cfg["model"] = name
        save_config(self.cfg)
        self.header.set_subtitle(name)
        self._append_block("Sistema", "ai", f"Modelo cambiado a {name}.\n")

    def quit_app(self):
        self.indicator.set_status(AppIndicator.IndicatorStatus.PASSIVE)
        self.quit()

    # -------------------------------- acciones ----------------------------- #

    def toggle(self):
        if self.window.get_visible() and self.window.is_active():
            logging.info("toggle: ocultando ventana")
            self.window.hide()
        else:
            self.show_window()

    def show_window(self):
        logging.info("mostrando ventana")
        self.window.show_all()
        self.window.deiconify()
        self.window.present()
        GLib.timeout_add(100, self._focus_entry)

    def _focus_entry(self):
        self.entry.grab_focus()
        return False

    def ask_by_voice(self):
        self.show_window()
        GLib.timeout_add(350, self._voice_after_show)

    def _voice_after_show(self):
        self.entry.grab_focus()
        dictate()
        return False

    def dictate_clicked(self):
        self.entry.grab_focus()
        dictate()

    def open_history(self):
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.touch(exist_ok=True)
        subprocess.Popen(["xdg-open", str(HISTORY_PATH)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def open_context(self):
        if not CONTEXT_PATH.exists():
            self.regenerate_context()
        subprocess.Popen(["xdg-open", str(CONTEXT_PATH)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def regenerate_context(self):
        self.context = generate_context(self.cfg)
        notify("Contexto del equipo actualizado")
        if self.window.get_visible():
            self._append_block("Sistema", "ai", "Contexto del equipo regenerado.\n")

    def new_chat(self):
        self.session += 1
        self.stream = None
        self.busy = False
        self.send_button.set_sensitive(True)
        self.header.set_subtitle(self.cfg["model"])
        self.buffer.set_text("")
        self.messages = []
        self._append_block("IA", "ai", "Nueva conversación.\n")

    # -------------------------------- envío -------------------------------- #

    def send(self):
        if self.busy:
            return
        text = self.entry.get_text().strip()
        if not text:
            return

        self.entry.set_text("")
        self.busy = True
        self.send_button.set_sensitive(False)
        self.header.set_subtitle("Pensando…")

        self.messages.append({"role": "user", "content": text})
        self._append_block("Tú", "user", text + "\n")
        self._append_block("IA", "ai")

        self.context = get_context(self.cfg)
        messages = build_messages(self.cfg, self.messages, self.context)
        self.stream = MarkdownStream(self.buffer)
        self.session += 1

        threading.Thread(target=self._run, args=(messages, text, self.session),
                         daemon=True).start()

    def _run(self, messages, user_text, session):
        chunks = []

        def on_chunk(piece):
            chunks.append(piece)
            GLib.idle_add(self._stream_chunk, piece)

        error = None
        try:
            stream_chat(self.cfg, messages, on_chunk)
        except Exception as exc:  # noqa: BLE001 - se muestra en la ventana
            error = str(exc)
            logging.error("fallo al consultar Ollama: %s", exc)

        GLib.idle_add(self._finish, "".join(chunks), user_text, error, session)

    def _stream_chunk(self, piece):
        if self.stream:
            self.stream.feed(piece)
        self._scroll_to_end()
        return False

    def _finish(self, answer, user_text, error, session):
        if session != self.session:
            logging.info("respuesta descartada: la conversación cambió")
            return False

        self.busy = False
        self.send_button.set_sensitive(True)
        self.header.set_subtitle(self.cfg["model"])

        if self.stream:
            self.stream.finish()
            self.stream = None

        if error and not answer:
            self._append_block("Error", "error", error + "\n")
        else:
            self.buffer.insert(self.buffer.get_end_iter(), "\n")
            if error:
                self._append_block("Error", "error", error + "\n")

        if answer:
            self.messages.append({"role": "assistant", "content": answer})
            save_history(self.cfg["model"], user_text, answer)
            if self.cfg["tts"].get("enabled") and not error:
                threading.Thread(target=speak, args=(answer,), daemon=True).start()

        self._scroll_to_end()
        return False

    def _scroll_to_end(self):
        adjustment = self.scroll.get_vadjustment()

        def scroll():
            adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
            return False

        GLib.idle_add(scroll)
        return False

    def _append_block(self, header, tag, body=""):
        self.buffer.insert_with_tags_by_name(
            self.buffer.get_end_iter(), header + "\n", tag)
        if body:
            self.buffer.insert_with_tags_by_name(
                self.buffer.get_end_iter(), body, "body")
        self._scroll_to_end()


# --------------------------------------------------------------------------- #
# Arranque
# --------------------------------------------------------------------------- #

def main(argv):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if "--context" in argv:
        print(get_context(load_config(), refresh=True), end="")
        return 0

    if "--ask" in argv:
        index = argv.index("--ask")
        if index + 1 >= len(argv):
            print("Uso: ai_assist.py --ask \"tu pregunta\"", file=sys.stderr)
            return 2
        return cli_ask(load_config(), argv[index + 1])

    if "--version" in argv:
        print("ai-assist 1.0")
        return 0

    app = AssistantApp(load_config())
    try:
        return app.run(argv)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv))
