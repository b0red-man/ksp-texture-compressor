"""Texture Compressor GUI.  Run:  python src/gui.py

Settings are saved to settings.cfg in the project root before each run; the
backends read that file.
"""
import queue
import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

import compress_textures as ct
import query

ROUNDING = ["Off", "Round Down", "Round Up", "Round Nearest"]
HELP_TEXT = (
    "Compress makes large textures (DDS files) smaller, in place in your game's GameData folder.\n\n"
    "Before a texture is changed, the original is copied to the backup folder "
    "(default: a texture_backup folder next to GameData), in the same layout as GameData. "
    "Only the textures that get compressed are backed up.\n\n"
    "How to use it:\n"
    "1. Click Query to see what would change.\n"
    "2. Click Compress and wait for the 'Final' line in the log.\n\n"
    "To undo: copy the contents of the backup folder over your Kerbal Space Program GameData folder "
    "(Steam\\steamapps\\common\\Kerbal Space Program\\GameData), replacing the files when asked."
)
DONE = object()  # sentinel on the log queue when a background task finishes
PAD = 4          # standard gap between widgets
BOX = 14         # checkbox size in px

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Texture Compressor")
        self.geometry("460x520")
        self.minsize(420, 460)
        self.logq = queue.Queue()
        self.busy = False

        self._build()
        self._load(ct.load_settings())
        self.after(100, self._drain_log)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Paths
        paths = ctk.CTkFrame(self)
        paths.grid(row=0, column=0, padx=PAD * 2, pady=PAD, sticky="ew")
        paths.grid_columnconfigure(1, weight=1)
        self.src = self._path_row(paths, "Source", 0)
        self.bak = self._path_row(paths, "Backup folder", 1)

        # Downscaling options
        opts = ctk.CTkFrame(self)
        opts.grid(row=1, column=0, padx=PAD * 2, pady=PAD, sticky="ew")
        opts.grid_columnconfigure(1, weight=1)

        self.enabled = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(opts, text="Enable downscaling", variable=self.enabled,
                        checkbox_width=BOX, checkbox_height=BOX).grid(
            row=0, column=0, columnspan=2, padx=PAD * 2, pady=PAD, sticky="w")

        ctk.CTkLabel(opts, text="Threshold (w x h)").grid(row=1, column=0, padx=PAD * 2, pady=PAD, sticky="w")
        box = ctk.CTkFrame(opts, fg_color="transparent")
        box.grid(row=1, column=1, sticky="w")
        self.tw = tk.StringVar()
        self.th = tk.StringVar()
        ctk.CTkEntry(box, textvariable=self.tw, width=90).pack(side="left")
        ctk.CTkLabel(box, text="x").pack(side="left", padx=PAD * 2)
        ctk.CTkEntry(box, textvariable=self.th, width=90).pack(side="left")
        self.px_hint = ctk.CTkLabel(opts, text="", text_color="gray")
        self.px_hint.grid(row=2, column=1, sticky="w")
        self.tw.trace_add("write", self._update_hint)
        self.th.trace_add("write", self._update_hint)

        ctk.CTkLabel(opts, text="Downscale loops").grid(row=3, column=0, padx=PAD * 2, pady=PAD, sticky="w")
        self.loops = tk.StringVar()
        ctk.CTkEntry(opts, textvariable=self.loops, width=90).grid(row=3, column=1, sticky="w")
        ctk.CTkLabel(opts, text="Halvings per texture. 0 = until under the threshold.",
                     text_color="gray").grid(row=4, column=1, sticky="w")

        ctk.CTkLabel(opts, text="Round to power of two").grid(row=5, column=0, padx=PAD * 2, pady=PAD, sticky="w")
        self.rounding = tk.StringVar()
        ctk.CTkOptionMenu(opts, values=ROUNDING, variable=self.rounding, width=150).grid(
            row=5, column=1, sticky="w")

        ctk.CTkLabel(opts, text="Skip files under (KB)").grid(row=6, column=0, padx=PAD * 2, pady=PAD, sticky="w")
        self.min_kb = tk.StringVar()
        ctk.CTkEntry(opts, textvariable=self.min_kb, width=90).grid(row=6, column=1, sticky="w")

        self.dxt = tk.BooleanVar()
        ctk.CTkCheckBox(opts, text="Convert to DXT / BC3", variable=self.dxt,
                        checkbox_width=BOX, checkbox_height=BOX).grid(
            row=7, column=0, columnspan=2, padx=PAD * 2, pady=(PAD, PAD * 2), sticky="w")

        # Buttons
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=2, column=0, padx=PAD * 2, pady=PAD, sticky="w")
        self.query_btn = ctk.CTkButton(btns, text="Query", width=110, command=self.on_query)
        self.query_btn.pack(side="left")
        self.compress_btn = ctk.CTkButton(btns, text="Compress", width=110, command=self.on_compress)
        self.compress_btn.pack(side="left", padx=PAD * 2)
        ctk.CTkButton(btns, text="How to use", width=110, fg_color="gray", command=self.show_help).pack(side="left")

        # Log
        self.log_box = ctk.CTkTextbox(self, state="disabled", wrap="none")
        self.log_box.grid(row=3, column=0, padx=PAD * 2, pady=(PAD, PAD * 2), sticky="nsew")

    def _path_row(self, parent, label, row):
        ctk.CTkLabel(parent, text=label).grid(row=row, column=0, padx=PAD * 2, pady=PAD, sticky="w")
        entry = ctk.CTkEntry(parent)
        entry.grid(row=row, column=1, padx=(0, PAD * 2), pady=PAD, sticky="ew")
        return entry

    # ---------- settings <-> widgets ----------
    def _load(self, s):
        self.src.insert(0, s.source_dir)
        self.bak.insert(0, s.backup_dir)
        self.enabled.set(s.downscale)
        self.tw.set(str(s.threshold_w))
        self.th.set(str(s.threshold_h))
        self.loops.set(str(s.loops))
        self.rounding.set(s.rounding if s.rounding in ROUNDING else ct.Settings().rounding)
        self.min_kb.set(str(s.min_size_kb))
        self.dxt.set(s.dxt)
        self._update_hint()

    def _update_hint(self, *_):
        try:
            self.px_hint.configure(text=f"= {int(self.tw.get()) * int(self.th.get()):,} pixels")
        except ValueError:
            self.px_hint.configure(text="enter whole numbers")

    def _collect(self):
        try:
            w, h, loops = int(self.tw.get()), int(self.th.get()), int(self.loops.get())
            min_kb = int(self.min_kb.get())
        except ValueError:
            raise ValueError("Threshold, loops and min size must be whole numbers.")
        if w <= 0 or h <= 0 or loops < 0 or min_kb < 0:
            raise ValueError("Threshold must be above 0; loops and min size must be 0 or more.")
        s = ct.Settings(
            downscale=self.enabled.get(),
            threshold_w=w,
            threshold_h=h,
            loops=loops,
            rounding=self.rounding.get(),
            dxt=self.dxt.get(),
            min_size_kb=min_kb,
            source_dir=self.src.get().strip(),
            backup_dir=self.bak.get().strip(),
        )
        err = ct.check_paths(s)
        if err:
            raise ValueError(err)
        return s

    # ---------- actions ----------
    def _save_and_run(self, task):
        try:
            s = self._collect()
        except ValueError as e:
            messagebox.showerror("Invalid setting", str(e))
            return
        try:
            ct.save_settings(s)
        except OSError as e:
            messagebox.showerror("Could not save settings.cfg", str(e))
            return
        self._start(task)

    def show_help(self):
        messagebox.showinfo("How to use", HELP_TEXT)

    def on_query(self):
        # Backends read settings.cfg, which was just saved.
        self._save_and_run(lambda: query.scan(log=self.log_msg))

    def on_compress(self):
        if not messagebox.askyesno(
                "Replace textures",
                "This replaces textures in your game's GameData folder.\n"
                "Originals are copied to the backup folder first.\n\nContinue?"):
            return
        self._save_and_run(lambda: ct.compress_all(log=self.log_msg))

    def _start(self, task):
        if self.busy:
            return
        self.busy = True
        self._set_buttons(False)
        self._log_clear()

        def worker():
            try:
                task()
            except Exception as e:
                self.log_msg(f"ERROR: {e}")
            finally:
                self.logq.put(DONE)

        threading.Thread(target=worker, daemon=True).start()

    def _set_buttons(self, enabled):
        state = "normal" if enabled else "disabled"
        self.query_btn.configure(state=state)
        self.compress_btn.configure(state=state)

    # ---------- log (background thread -> UI thread) ----------
    def log_msg(self, msg):
        self.logq.put(str(msg))

    def _log_clear(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _drain_log(self):
        try:
            while True:
                item = self.logq.get_nowait()
                if item is DONE:
                    self.busy = False
                    self._set_buttons(True)
                    continue
                self.log_box.configure(state="normal")
                self.log_box.insert("end", item + "\n")
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_log)


if __name__ == "__main__":
    App().mainloop()
