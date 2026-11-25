#!/usr/bin/python3
"""Prueba del renderizador Markdown (sin Ollama, sin ventana visible)."""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

import ai_assist

buffer = Gtk.TextBuffer()
buffer.create_tag("body", pixels_below_lines=2)
buffer.create_tag("code", family="monospace")
for level, scale in ((1, 1.35), (2, 1.2), (3, 1.08)):
    buffer.create_tag(f"h{level}", weight=Pango.Weight.BOLD, scale=scale)
buffer.create_tag("quote", style=Pango.Style.ITALIC)
buffer.create_tag("rule")
buffer.create_tag("table", family="monospace")

sample = """### Título de prueba
Esto tiene **negrita**, *cursiva* y `código` en línea, además de un [enlace](https://ejemplo.com).

- Primer punto con **negrita**
- Segundo punto
1. Paso uno
2. Paso dos

> Una cita breve

```bash
mkdir -p ~/prueba
ls -la
```

| Columna | Valor |
|---|---|
| RAM | 15 GiB |

---
Texto final con ~~tachado~~.
"""

stream = ai_assist.MarkdownStream(buffer)
for i in range(0, len(sample), 7):  # simula llegada por trozos
    stream.feed(sample[i:i + 7])
stream.finish()

text = buffer.get_text(*buffer.get_bounds(), False)
print(text)
print("=" * 40)

checks = {
    "sin '###'": "###" not in text,
    "sin '**'": "**" not in text,
    "sin '```'": "```" not in text,
    "viñeta": "• " in text,
    "regla horizontal": "─" in text,
    "enlace visible": "enlace" in text,
    "tabla sin pipes": "|" not in text,
    "código conservado": "mkdir -p ~/prueba" in text,
}

start = buffer.get_start_iter()
heading_tag = False
while not start.is_end():
    if any(tag.get_property("name") == "h3" for tag in start.get_tags()):
        heading_tag = True
    start.forward_char()
checks["tag h3 aplicado"] = heading_tag

for label, ok in checks.items():
    print(("OK   " if ok else "FALLA") + "  " + label)

raise SystemExit(0 if all(checks.values()) else 1)
