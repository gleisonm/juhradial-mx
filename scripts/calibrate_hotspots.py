#!/usr/bin/env python3
"""Interactive hotspot calibration for the Buttons-page mouse image.

Shows the device photo (brightness-boosted so the buttons are easy to see) and
asks you to click each button in turn. Writes the picked positions (as image
fractions) to /tmp/hotspots.json, which the assistant then applies to
overlay/settings_constants.py.

Run:  python3 scripts/calibrate_hotspots.py
"""
import json
import os
import tkinter as tk

from PIL import Image, ImageEnhance, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(HERE, "..", "assets", "devices", "logitechmouse.png")
OUT = "/tmp/hotspots.json"

# (key, friendly prompt) in click order
BUTTONS = [
    ("middle", "Botão do meio  →  clique na RODA DE SCROLL"),
    ("shift_wheel", "Shift Wheel Mode  →  botão no TOPO, perto do LED verde"),
    ("forward", "Forward / Avançar  →  botão lateral de CIMA (pill superior)"),
    ("back", "Back / Voltar  →  botão lateral de BAIXO (pill inferior)"),
    ("horizontal_scroll", "Scroll Horizontal  →  RODA DO POLEGAR (ribbed, à esquerda)"),
    ("gesture", "Gestos  →  APOIO DO POLEGAR / botão de gesto"),
    ("thumb", "Show Actions Ring  →  área HAPTIC (ícone de 6 pontos)"),
]

SCALE = 1.7  # display upscale for easier clicking

# Load + brighten so the dark graphite buttons are visible
base = Image.open(IMG).convert("RGBA")
bg = Image.new("RGBA", base.size, (30, 30, 46, 255))
bg.alpha_composite(base)
disp = bg.convert("RGB")
disp = ImageEnhance.Brightness(disp).enhance(1.35)
disp = ImageEnhance.Contrast(disp).enhance(1.4)
DW, DH = int(disp.width * SCALE), int(disp.height * SCALE)
disp = disp.resize((DW, DH))

root = tk.Tk()
root.title("Calibração de hotspots - JuhRadial MX")
photo = ImageTk.PhotoImage(disp)

header = tk.Label(root, font=("Sans", 15, "bold"), fg="#00d4ff", bg="#1e1e2e",
                  pady=10, wraplength=DW)
header.pack(fill="x")
canvas = tk.Canvas(root, width=DW, height=DH, highlightthickness=0, bg="#1e1e2e")
canvas.pack()
canvas.create_image(0, 0, anchor="nw", image=photo)
hint = tk.Label(root, font=("Sans", 11), fg="#cccccc", bg="#1e1e2e", pady=6,
                text="Clique esquerdo = marca o ponto  |  Clique direito = refaz o último")
hint.pack(fill="x")

state = {"i": 0, "picks": {}, "markers": []}


def show_prompt():
    i = state["i"]
    if i < len(BUTTONS):
        header.config(text=f"[{i+1}/{len(BUTTONS)}]  {BUTTONS[i][1]}")
    else:
        header.config(text="✅ Concluído! Coordenadas salvas. Pode FECHAR a janela.",
                      fg="#00ff66")
        with open(OUT, "w") as f:
            json.dump(state["picks"], f, indent=2)


def on_click(ev):
    i = state["i"]
    if i >= len(BUTTONS):
        return
    key = BUTTONS[i][0]
    fx, fy = ev.x / DW, ev.y / DH
    state["picks"][key] = [round(fx, 4), round(fy, 4)]
    r = 7
    m1 = canvas.create_oval(ev.x - r, ev.y - r, ev.x + r, ev.y + r,
                            fill="#ff00c8", outline="white", width=2)
    m2 = canvas.create_text(ev.x + 10, ev.y - 10, text=key, anchor="w",
                            fill="#00ff66", font=("Sans", 11, "bold"))
    state["markers"].append((key, m1, m2))
    state["i"] += 1
    show_prompt()


def on_redo(ev):
    if state["markers"]:
        key, m1, m2 = state["markers"].pop()
        canvas.delete(m1)
        canvas.delete(m2)
        state["picks"].pop(key, None)
        state["i"] = max(0, state["i"] - 1)
        show_prompt()


canvas.bind("<Button-1>", on_click)
canvas.bind("<Button-3>", on_redo)
show_prompt()
root.configure(bg="#1e1e2e")
root.mainloop()
print("Saved:", OUT)
