# Asistente IA local para GNOME

Consulta rápida a tu propia IA (Ollama + `qwen3.5:9b`) sin abrir el navegador:
icono en la bandeja del sistema, atajo global y dictado por voz con `voiceio`.

## Qué hace

- **Icono en la bandeja** (junto a ZapZap/Telegram): clic → *Preguntar*, elegir
  modelo, leer respuestas, historial.
- **Ctrl+Alt+A** en cualquier aplicación: abre u oculta una ventana flotante de
  consulta.
- **Micrófono** (botón o `Ctrl+Alt+V`): dicta la pregunta con `voiceio`; el texto
  aparece en el campo y Enter la envía.
- **Altavoz** (botón o menú de bandeja): lee la respuesta en voz alta con
  `spd-say`.
- **Markdown formateado**: títulos, listas, negritas, código, tablas y citas se
  muestran con estilo, no como símbolos crudos.
- **Contexto real del equipo**: antes de responder, el asistente recibe datos
  verificados de esta máquina (Debian 13, GNOME 48/Wayland, CPU, GPU, RAM,
  Nautilus, atajos de GNOME). Así responde para *tu* sistema y no con
  generalidades de Windows/macOS.
- Historial en `~/.local/share/ai-assist/history.jsonl`.

Todo se ejecuta en el equipo: Ollama escucha solo en `127.0.0.1:11434`, no hay
tráfico a ningún servicio externo. El contexto del equipo también se queda local.

## Uso diario

| Acción | Cómo |
|---|---|
| Abrir/ocultar | `Ctrl+Alt+A` o clic en el icono |
| Preguntar | Escribe y pulsa `Enter` |
| Dictar | Botón del micrófono, o enfoca el campo y pulsa `Ctrl+Alt+V` |
| Nueva conversación | Botón de hoja en blanco |
| Cerrar | `Esc` (la app sigue en la bandeja) |
| Leer respuesta | Botón del altavoz |
| Cambiar modelo | Menú de la bandeja → *Modelo* (lee los instalados en Ollama) |
| Ver/actualizar contexto | Bandeja → *Ver contexto del equipo* / *Regenerar contexto* |
| Salir del todo | Bandeja → *Salir* |

También funciona desde la terminal:

```bash
ai-assist --ask "resume este error de systemd"
ai-assist --toggle        # abre/oculta la ventana
ai-assist --new           # nueva conversación
ai-assist --context       # muestra el contexto que recibe el modelo
```

## Contexto del equipo

Antes de cada consulta se añade al *system prompt* un bloque generado desde
datos reales del equipo (`~/.local/share/ai-assist/system-context.md`):

- SO, kernel, escritorio y versión de GNOME, sesión Wayland.
- CPU, RAM, GPU/VRAM, disco libre.
- Usuario, shell y herramientas instaladas (ollama, docker, voiceio...).
- Atajos propios del asistente y de GNOME/Nautilus.
- Regla explícita: responder para Debian/GNOME salvo que se pida otro sistema.

Se regenera al arrancar y cuando tiene más de una hora; también a mano desde la
bandeja. Si quieres desactivarlo, pon `"include_system_context": false` en la
configuración. Al ser datos locales, no salen del equipo.

## Configuración

`~/.config/ai-assist/config.json`:

```json
{
  "base_url": "http://127.0.0.1:11434",
  "model": "qwen3.5:9b",
  "system_prompt": "Eres un asistente útil, directo y preciso...",
  "temperature": 0.7,
  "timeout": 600,
  "disable_thinking": true,
  "include_system_context": true,
  "tts": { "enabled": false, "language": "es" }
}
```

- `disable_thinking`: evita que los modelos con razonamiento (Qwen 3.5) gasten
  tokens "pensando" antes de responder. Si quieres ver el razonamiento, ponlo en
  `false`.
- `include_system_context`: inyecta el contexto real del equipo en cada consulta.
- Los cambios se aplican al reiniciar la app (bandeja → *Salir* y vuelve a
  abrirla, o cierra sesión).

Cambiar el atajo global:

```bash
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ai-assist/ binding '<Ctrl><Alt>space'
```

## Modelos

Con 16 GB de VRAM caben enteros modelos de hasta ~14B en Q4. Sugerencias:

```bash
ollama pull qwen3.5:9b          # el actual: rápido y equilibrado
ollama pull qwen3.5:14b         # más calidad, algo más lento
ollama pull qwen2.5-coder:14b   # para código
```

Después de descargar, bandeja → *Recargar modelos*.

## Requisitos (ya instalados en este equipo)

| Pieza | Comprobación |
|---|---|
| Ollama activo | `systemctl status ollama` |
| Python 3 + PyGObject | `/usr/bin/python3 -c "import gi"` |
| AppIndicator | `gnome-extensions list \| grep appindicator` |
| Dictado | `voiceio doctor` |
| TTS | `spd-say "prueba"` |

La bandeja en GNOME depende de la extensión *AppIndicator and KStatusNotifierItem
Support*; sin ella el icono no aparece (el atajo sigue funcionando).

## Integración con OpenCode

OpenCode ya usa el mismo Ollama. Lo que consultes aquí y lo que uses en OpenCode
comparten modelo y GPU; no hay que configurar nada más.

## Desinstalar

```bash
./uninstall.sh
```

Borra lanzador, autostart, atajo y enlace `~/.local/bin/ai-assist`. Conserva el
código y la configuración.

## Pruebas

```bash
./test_render.py   # valida el renderizador Markdown (sin Ollama)
./selftest.py      # prueba real de la ventana contra Ollama
```

## Problemas conocidos

- **El atajo no abre la ventana**: otro atajo puede estar ocupando
  `Ctrl+Alt+A`. Cámbialo con el comando de arriba.
- **El dictado no escribe**: `voiceio` escribe en la ventana con foco; haz clic
  en el campo de texto antes de dictar (el botón del micrófono ya lo enfoca).
- **Respuestas vacías**: revisa `~/.local/share/ai-assist/ai-assist.log` y que
  Ollama tenga el modelo cargado (`ollama ps`).
