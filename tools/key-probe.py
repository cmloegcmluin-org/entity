"""What does this machine actually send when he presses the key by his spacebar?

Three fixes for "Windows key + Enter" have failed, each written from a guess about how that key
arrives - as a modifier bit, as a keysym, under one spelling or another. This asks the question
instead: it opens a small window, records every key event Tk sees, and writes them to a file, so
the next fix is built on what his keyboard and his KVM actually send rather than on what they
ought to.

Run it, press the combination a few times, close the window. It writes key-probe.txt beside itself.
"""
import tkinter as tk
from pathlib import Path

REPORT = Path(__file__).with_name("key-probe.txt")
seen = []


def note(event, kind):
    seen.append(f"{kind:10} keysym={event.keysym!r:14} keycode={event.keycode:<5} state=0x{event.state:x}")
    label.configure(text="\n".join(seen[-12:]) or "press the keys")


root = tk.Tk()
root.title("Entity key probe - press your submit combination, then close this window")
root.geometry("640x360")
root.configure(bg="#161616")
tk.Label(root, bg="#161616", fg="#7fff00", font=("Segoe UI", 11), justify="left", padx=12, pady=8,
         text="Press the key by your spacebar together with Enter, a few times.\n"
              "Then close this window and tell Claude it's done.").pack(anchor="w")
label = tk.Label(root, text="press the keys", bg="#1f1f1f", fg="#d6d6d6", justify="left",
                 font=("Consolas", 10), anchor="nw", padx=12, pady=8)
label.pack(fill="both", expand=True, padx=12, pady=(0, 12))

root.bind_all("<KeyPress>", lambda event: note(event, "press"))
root.bind_all("<KeyRelease>", lambda event: note(event, "release"))
root.mainloop()

REPORT.write_text("\n".join(seen) + "\n", encoding="utf-8")
print(f"wrote {REPORT}")
