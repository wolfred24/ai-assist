#!/usr/bin/python3
"""Prueba de humo de la interfaz: pide Markdown y revisa que se renderice."""
import sys

from gi.repository import GLib

import ai_assist

app = ai_assist.AssistantApp(ai_assist.load_config())
result = {}


def start():
    app.show_window()
    app.entry.set_text(
        "Responde solo en Markdown: un título de nivel 2 que diga Prueba, "
        "una lista de tres puntos con al menos una palabra en negrita, y un "
        "bloque de código bash con el comando ls. Sin introducción.")
    app.send()
    return False


def finish():
    bounds = app.buffer.get_bounds()
    text = app.buffer.get_text(*bounds, False)
    checks = {
        "sin '##'": "##" not in text,
        "sin '**'": "**" not in text,
        "sin '```'": "```" not in text,
        "tiene viñetas": "• " in text,
        "tiene el comando": "ls" in text,
        "respuesta guardada": len(app.messages) == 2,
        "no está ocupado": app.busy is False,
    }
    print("--- BUFFER ---")
    print(text)
    print("--------------")
    for label, ok in checks.items():
        print(("OK   " if ok else "FALLA") + "  " + label)
    app.quit_app()
    result["ok"] = all(checks.values())
    return False


GLib.timeout_add(1200, start)
GLib.timeout_add(30000, finish)
app.run([sys.argv[0]])
sys.exit(0 if result.get("ok") else 1)
