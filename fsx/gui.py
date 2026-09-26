#!/usr/bin/env python3
"""FSX EFB Connect desktop app; source entry point and PyInstaller entry point."""

import argparse
from datetime import datetime
import logging
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

if __package__ in (None, "") and not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsx import __version__, bridge
from fsx.bridge import connection_options
from fsx.network import discover_adapters
from fsx.widgets import _StyledButton, _DataBadge, _PulseDot


class QueueLogHandler(logging.Handler):
    def __init__(self, messages):
        super().__init__()
        self.messages = messages

    def emit(self, record):
        self.messages.put(("log", (record.levelno, self.format(record))))


class GDL90BridgeGUI:
    # Same palette, sidebar, live-data badges and controls as the MSFS connector.
    C = {
        'bg': '#0b1220', 'card': '#111827', 'input': '#1e293b',
        'border': '#1e3a5f', 'accent': '#38bdf8', 'accent2': '#818cf8',
        'success': '#22c55e', 'warning': '#f59e0b', 'danger': '#ef4444',
        'txt': '#e2e8f0', 'txt2': '#94a3b8',
        'log_bg': '#020617', 'log_txt': '#86efac', 'separator': '#1e3a5f',
    }

    def __init__(self, root, auto_discover=True):
        self.root = root
        root.title(f"FSX - EFB Connect {__version__}")
        width = max(940, min(1120, root.winfo_screenwidth() - 60))
        height = max(640, min(820, root.winfo_screenheight() - 100))
        root.geometry(f"{width}x{height}")
        root.minsize(940, 640)
        root.configure(bg=self.C['bg'])
        self.messages = queue.Queue()
        self.worker = None
        self.stop_event = None
        self.adapters = []
        self.detecting = False
        self.closing = False
        self.had_error = False
        self._build_ui()
        self.logger = logging.getLogger("fsx")
        self.old_log_level = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.log_handler = QueueLogHandler(self.messages)
        self.logger.addHandler(self.log_handler)
        self._append_log(logging.INFO, f"FSX EFB Connect {__version__} — simulation use only")
        self._append_log(logging.INFO, "Load an FSX flight, select your LAN adapter, then Start.")
        self._update_controls()
        self.poll_id = root.after(80, self._poll_queue)
        root.protocol("WM_DELETE_WINDOW", self.on_closing)
        if auto_discover:
            self.refresh_networks()

    def _card(self, parent, pad_x=15, pad_y=12) -> tuple:
        outer = tk.Frame(parent,
                         bg=self.C['card'],
                         highlightbackground=self.C['border'],
                         highlightthickness=1, bd=0)
        inner = tk.Frame(outer, bg=self.C['card'])
        inner.pack(fill=tk.BOTH, expand=True, padx=pad_x, pady=pad_y)
        return outer, inner

    def _build_ui(self):
        C = self.C
        root_frame = tk.Frame(self.root, bg=C['bg'])
        root_frame.pack(fill=tk.BOTH, expand=True)

        # LEFT SIDEBAR - Responsive width
        sidebar = tk.Frame(root_frame, bg="#080e1a")
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        def update_sidebar_width(event):
            new_width = max(240, min(290, int(event.width * 0.24)))
            sidebar.config(width=new_width)

        root_frame.bind("<Configure>", update_sidebar_width)
        sidebar.config(width=260)

        self._build_sidebar(sidebar)

        # RIGHT MAIN AREA
        main = tk.Frame(root_frame, bg=C['bg'])
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=15, pady=15)
        self._build_main(main)

    def _build_sidebar(self, sb):
        C = self.C

        sb.grid_rowconfigure(0, weight=0)  # logo
        sb.grid_rowconfigure(1, weight=0)  # divider
        sb.grid_rowconfigure(2, weight=0)  # status
        sb.grid_rowconfigure(3, weight=0)  # divider
        sb.grid_rowconfigure(4, weight=1)  # badges
        sb.grid_rowconfigure(5, weight=0)  # version

        # Logo
        top = tk.Frame(sb, bg="#080e1a")
        top.grid(row=0, column=0, sticky="ew", padx=15, pady=(20, 0))

        ico = tk.Canvas(top, width=36, height=36, bg="#080e1a", highlightthickness=0)
        ico.pack(anchor="w")
        ico.create_polygon(18, 3, 22, 18, 32, 22, 32, 25, 22, 23, 24, 33,
                           28, 35, 28, 37, 18, 33, 8, 37, 8, 35, 12, 33,
                           14, 23, 4, 25, 4, 22, 14, 18,
                           smooth=False, fill=C['accent'], outline="")

        tk.Label(top, text="GDL90", font=("Segoe UI", 16, "bold"),
                 fg=C['accent'], bg="#080e1a").pack(anchor="w", pady=(5, 0))
        tk.Label(top, text="Bridge", font=("Segoe UI", 16, "bold"),
                 fg=C['accent2'], bg="#080e1a").pack(anchor="w")
        tk.Label(top, text="FSX → EFB",
                 font=("Segoe UI", 8), fg=C['txt2'], bg="#080e1a").pack(anchor="w", pady=(2, 0))

        tk.Frame(sb, bg=C['border'], height=1).grid(row=1, column=0, sticky="ew", padx=15, pady=10)

        # Status
        status_frame = tk.Frame(sb, bg="#080e1a")
        status_frame.grid(row=2, column=0, sticky="ew", padx=15)

        tk.Label(status_frame, text="STATUS", font=("Segoe UI", 7, "bold"),
                 fg=C['txt2'], bg="#080e1a").pack(anchor="w")
        dot_row = tk.Frame(status_frame, bg="#080e1a")
        dot_row.pack(anchor="w", pady=(4, 0))
        self._pulse_dot = _PulseDot(dot_row, size=12, bg="#080e1a")
        self._pulse_dot.pack(side=tk.LEFT, padx=(0, 6))
        self._status_lbl = tk.Label(dot_row, text="Ready",
                                     font=("Segoe UI", 9, "bold"),
                                     fg=C['warning'], bg="#080e1a")
        self._status_lbl.pack(side=tk.LEFT)

        self._substatus = tk.Label(status_frame, text="Configure and start",
                                    font=("Segoe UI", 7), fg=C['txt2'],
                                    bg="#080e1a", wraplength=180, justify="left")
        self._substatus.pack(anchor="w", pady=(3, 0))

        tk.Frame(sb, bg=C['border'], height=1).grid(row=3, column=0, sticky="ew", padx=15, pady=10)

        # Live Data Badges - 2x4 grid (8 items)
        badges_container = tk.Frame(sb, bg="#080e1a")
        badges_container.grid(row=4, column=0, sticky="nsew", padx=15, pady=(0, 10))

        tk.Label(badges_container, text="LIVE DATA", font=("Segoe UI", 7, "bold"),
                 fg=C['txt2'], bg="#080e1a").pack(anchor="w", pady=(0, 5))

        badges_frame = tk.Frame(badges_container, bg="#080e1a")
        badges_frame.pack(fill=tk.BOTH, expand=True)

        # 2 columns
        self._badges = {}
        specs = [
            ("Latitude", "◎", "lat"),
            ("Longitude", "◎", "lon"),
            ("Altitude", "▲", "alt"),
            ("Heading", "➤", "hdg"),
            ("Gnd Speed", "⚡", "gs"),
            ("Pitch", "↗", "pitch"),
            ("Roll", "↘", "roll"),
        ]

        for i, (lbl, icon, key) in enumerate(specs):
            b = _DataBadge(badges_frame, lbl, icon, "#080e1a", C['accent'])
            row = i // 2
            col = i % 2
            b.grid(row=row, column=col, padx=(0 if col == 0 else 8, 0),
                   pady=4, sticky="w")
            self._badges[key] = b

        # Version and credit
        credit_frame = tk.Frame(sb, bg="#080e1a")
        credit_frame.grid(row=5, column=0, pady=8)

        tk.Label(credit_frame, text="Developed By: Daniel Aregay",
                 font=("Segoe UI", 7), fg="#2d3f55", bg="#080e1a").pack()

    def _build_main(self, main):
        C = self.C
        header = tk.Frame(main, bg=C['bg'])
        header.pack(fill=tk.X, pady=(0, 12))
        tk.Label(header, text="FSX → GDL90 Bridge", font=("Segoe UI", 16, "bold"),
                 fg=C['txt'], bg=C['bg']).pack(anchor="w")
        tk.Label(header, text="FD Pro X · ForeFlight · Live position & AHRS",
                 font=("Segoe UI", 9), fg=C['txt2'], bg=C['bg']).pack(anchor="w")

        card, inner = self._card(main)
        card.pack(fill=tk.X, pady=(0, 12))
        tk.Label(inner, text="CONNECTION", font=("Segoe UI", 9, "bold"),
                 fg=C['accent'], bg=C['card']).pack(anchor="w", pady=(0, 8))
        self.auto_var = tk.BooleanVar(value=True)
        self.auto_check = tk.Checkbutton(
            inner, text="Auto-detect subnet broadcast from this computer",
            variable=self.auto_var, command=self._mode_changed,
            font=("Segoe UI", 10, "bold"), bg=C['card'], fg=C['success'],
            selectcolor=C['input'], activebackground=C['card'], activeforeground=C['success'])
        self.auto_check.pack(anchor="w")

        adapter_row = tk.Frame(inner, bg=C['card'])
        adapter_row.pack(fill=tk.X, pady=(8, 4))
        # The readonly combobox allows explicit selection on multi-adapter PCs.
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("FSX.TCombobox", fieldbackground=C['input'], background=C['input'],
                        foreground=C['txt'], arrowcolor=C['accent'], bordercolor=C['border'])
        style.map("FSX.TCombobox", fieldbackground=[("readonly", C['input'])],
                  foreground=[("readonly", C['txt']), ("disabled", C['txt2'])])
        self.adapter_var = tk.StringVar(value="Detecting active IPv4 adapters…")
        self.adapter_box = ttk.Combobox(adapter_row, textvariable=self.adapter_var,
                                       state="readonly", style="FSX.TCombobox",
                                       font=("Segoe UI", 10))
        self.adapter_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.adapter_box.bind("<<ComboboxSelected>>", self._adapter_changed)
        self.refresh_btn = _StyledButton(adapter_row, text="↻ Refresh", command=self.refresh_networks,
                                         bg=C['input'], fg=C['accent'],
                                         hover_bg=C['border'], hover_fg=C['txt'])
        self.refresh_btn.pack(side=tk.RIGHT)
        self.network_note = tk.StringVar(value="Automatic mode uses the adapter's actual subnet mask.")
        tk.Label(inner, textvariable=self.network_note, font=("Segoe UI", 8),
                 fg=C['txt2'], bg=C['card'], anchor="w", justify="left",
                 wraplength=650).pack(fill=tk.X, pady=(2, 10))

        grid = tk.Frame(inner, bg=C['card'])
        grid.pack(fill=tk.X)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        fields = [("Target IP", "ip_var", "", 0, 0),
                  ("Port", "port_var", "4000", 0, 1),
                  ("Callsign", "callsign_var", "FSX", 1, 0),
                  ("Rate (Hz)", "rate_var", "5", 1, 1)]
        self._entries = {}
        for label, name, default, row, column in fields:
            cell = tk.Frame(grid, bg=C['card'])
            cell.grid(row=row, column=column, sticky="ew", padx=(0, 12), pady=(0, 8))
            tk.Label(cell, text=label, font=("Segoe UI", 8, "bold"),
                     fg=C['txt2'], bg=C['card']).pack(anchor="w")
            var = tk.StringVar(value=default)
            setattr(self, name, var)
            entry = tk.Entry(cell, textvariable=var, font=("Segoe UI", 11),
                             bg=C['input'], readonlybackground=C['input'], fg=C['txt'],
                             disabledbackground=C['input'], disabledforeground=C['txt2'],
                             insertbackground=C['accent'], relief=tk.FLAT,
                             highlightthickness=1, highlightbackground=C['border'],
                             highlightcolor=C['accent'])
            entry.pack(fill=tk.X, ipady=5, pady=(3, 0))
            self._entries[name] = entry

        options = tk.Frame(inner, bg=C['card'])
        options.pack(fill=tk.X, pady=(3, 6))
        self.broadcast_var = tk.BooleanVar(value=True)
        self.broadcast_check = tk.Checkbutton(
            options, text="Broadcast", variable=self.broadcast_var,
            font=("Segoe UI", 9, "bold"), bg=C['card'], fg=C['success'],
            selectcolor=C['input'], activebackground=C['card'])
        self.broadcast_check.pack(side=tk.LEFT)
        tk.Label(options, text="AHRS", font=("Segoe UI", 8, "bold"),
                 fg=C['txt2'], bg=C['card']).pack(side=tk.LEFT, padx=(16, 6))
        self.ahrs_var = tk.StringVar(value="both")
        self.ahrs_box = ttk.Combobox(options, textvariable=self.ahrs_var,
                                    values=("both", "foreflight", "legacy"), width=12,
                                    state="readonly", style="FSX.TCombobox")
        self.ahrs_box.pack(side=tk.LEFT)
        tk.Label(inner, text="Broadcast reaches your subnet. For direct iPad unicast, turn off Auto and Broadcast.\n"
                            "If a VPN is active, select the Wi-Fi/Ethernet adapter shared with your iPad.",
                 font=("Segoe UI", 8), fg=C['txt2'], bg=C['card'], justify="left",
                 wraplength=600).pack(anchor="w")

        buttons = tk.Frame(main, bg=C['bg'])
        buttons.pack(fill=tk.X, pady=(0, 12))
        self._start_btn = _StyledButton(buttons, text="▶   START BROADCAST", command=self._toggle)
        self._start_btn.pack(side=tk.LEFT)
        _StyledButton(buttons, text="Clear Log", command=lambda: self._log_box.delete("1.0", tk.END),
                      bg=C['input'], fg=C['txt2'], hover_bg=C['border'], hover_fg=C['txt']).pack(side=tk.RIGHT)
        _StyledButton(buttons, text="Copy Log", command=self._copy_log,
                      bg=C['input'], fg=C['txt2'], hover_bg=C['border'], hover_fg=C['txt']).pack(side=tk.RIGHT, padx=8)
        card, inner = self._card(main, pad_x=12, pad_y=10)
        card.pack(fill=tk.BOTH, expand=True)
        tk.Label(inner, text="ACTIVITY LOG", font=("Segoe UI", 9, "bold"),
                 fg=C['accent'], bg=C['card']).pack(anchor="w", pady=(0, 6))
        self._log_box = scrolledtext.ScrolledText(
            inner, height=7, font=("Consolas", 9), bg=C['log_bg'], fg=C['log_txt'],
            wrap=tk.WORD, relief=tk.FLAT, padx=8, pady=8)
        self._log_box.pack(fill=tk.BOTH, expand=True)
        for name, color in (("info", C['log_txt']), ("warn", C['warning']), ("error", C['danger'])):
            self._log_box.tag_configure(name, foreground=color)

    def _append_log(self, level, text):
        tag = "error" if level >= logging.ERROR else "warn" if level >= logging.WARNING else "info"
        self._log_box.insert(tk.END, f"[{datetime.now():%H:%M:%S}] {text}\n", tag)
        # Bound the widget's memory on long simulator sessions.
        lines = int(self._log_box.index("end-1c").split(".")[0])
        if lines > 1000:
            self._log_box.delete("1.0", f"{lines - 1000}.0")
        self._log_box.see(tk.END)

    def _copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._log_box.get("1.0", "end-1c"))

    def _selected_adapter(self):
        index = self.adapter_box.current()
        return self.adapters[index] if 0 <= index < len(self.adapters) else None

    def _adapter_changed(self, event=None):
        adapter = self._selected_adapter()
        if adapter:
            self.network_note.set(f"PC {adapter.address}  ·  mask {adapter.netmask}  →  {adapter.broadcast}")
            if self.auto_var.get():
                self.ip_var.set(adapter.broadcast)
        self._update_controls()

    def _mode_changed(self):
        if self.auto_var.get():
            self.broadcast_var.set(True)
            adapter = self._selected_adapter()
            self.ip_var.set(adapter.broadcast if adapter else "")
        self._adapter_changed()

    def refresh_networks(self):
        if self.worker is not None or self.detecting:
            return
        self.detecting = True
        self.network_note.set("Looking for active Wi-Fi / Ethernet IPv4 addresses…")
        self._update_controls()
        # Discovery can be slow on Windows. Never block Tk's main thread.
        def discover():
            try:
                self.messages.put(("adapters", discover_adapters()))
            except Exception as exc:
                self.messages.put(("network_error", str(exc)))
        threading.Thread(target=discover, name="FSX network discovery", daemon=True).start()

    def _apply_adapters(self, adapters):
        previous = self._selected_adapter()
        self.adapters = adapters
        self.detecting = False
        self.adapter_box.configure(values=[a.label for a in adapters])
        if adapters:
            # Keep an explicit selection on Refresh when it still exists.
            selected = next((i for i, a in enumerate(adapters)
                             if previous and (a.name, a.address) == (previous.name, previous.address)), 0)
            self.adapter_box.current(selected)
            self._adapter_changed()
            self._append_log(logging.INFO, "Network selected: " + self.network_note.get())
        else:
            self.adapter_var.set("No usable active IPv4 adapter")
            self.network_note.set("Connect to your LAN and Refresh, or disable Auto and enter a target manually.")
            if self.auto_var.get():
                self.ip_var.set("")
            self._append_log(logging.WARNING, self.network_note.get())
        self._update_controls()

    def _update_controls(self):
        busy = self.worker is not None
        automatic = self.auto_var.get()
        self.auto_check.configure(state="disabled" if busy else "normal")
        self.adapter_box.configure(state="disabled" if busy or self.detecting or not self.adapters else "readonly")
        self.refresh_btn.configure(state="disabled" if busy or self.detecting else "normal")
        self.broadcast_check.configure(state="disabled" if busy or automatic else "normal")
        self.ahrs_box.configure(state="disabled" if busy else "readonly")
        for name, entry in self._entries.items():
            entry.configure(state="disabled" if busy else "readonly" if name == "ip_var" and automatic else "normal")
        if busy:
            self._start_btn.set_text("Stopping…" if self.stop_event.is_set() else "■   STOP")
            self._start_btn.set_colors("#7f1d1d", "#fca5a5", "#991b1b", "#fecaca")
            self._start_btn.configure(state="disabled" if self.stop_event.is_set() else "normal")
        else:
            self._start_btn.set_text("▶   START")
            self._start_btn.set_colors("#065f46", "#6ee7b7", "#047857", "#a7f3d0")
            unavailable = self.detecting or (automatic and self._selected_adapter() is None)
            self._start_btn.configure(state="disabled" if unavailable else "normal")

    def _set_status(self, text, color, pulse=False):
        self._status_lbl.configure(text=text, fg=self.C[color])
        self._pulse_dot.set_color(self.C[color], pulse=pulse)

    def _clear_badges(self):
        for badge in self._badges.values():
            badge.update("—")

    def _show_sample(self, data):
        fields = {'lat': f"{data.lat:+.4f}°", 'lon': f"{data.lon:+.4f}°",
                  'alt': f"{data.alt_ft:.0f} ft", 'hdg': f"{data.hdg_true:.1f}°",
                  'gs': f"{data.gs_kt:.0f} kt", 'pitch': f"{data.pitch:+.1f}°",
                  'roll': f"{data.roll:+.1f}°"}
        for key, value in fields.items():
            self._badges[key].update(value, self.C['accent'])

    def _toggle(self):
        if self.worker is not None:
            self.stop_event.set()
            self._set_status("Stopping", 'warning')
            self._update_controls()
            return
        adapter = self._selected_adapter()
        if self.detecting or (self.auto_var.get() and adapter is None):
            messagebox.showerror("Network unavailable", "Wait for discovery, then select a LAN adapter or disable Auto.", parent=self.root)
            return
        try:
            # Derive again at Start so editable widget text cannot override Auto.
            automatic = self.auto_var.get()
            args = connection_options(
                adapter.broadcast if automatic else self.ip_var.get(), self.port_var.get(),
                self.callsign_var.get(), self.rate_var.get(), self.ahrs_var.get(),
                True if automatic else self.broadcast_var.get(),
                adapter.address if adapter else None)
        except (ValueError, argparse.ArgumentTypeError) as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self.root)
            return
        self.had_error = False
        self.stop_event = threading.Event()
        self._clear_badges()
        self._set_status("Connecting", 'warning', True)
        self._substatus.configure(text=f"{args.target[0]}:{args.port}")
        self._append_log(logging.INFO, f"Starting → {args.target[0]}:{args.port} | {args.rate:g} Hz | AHRS {args.ahrs}")
        def work():
            try:
                bridge.run(args, stop_event=self.stop_event,
                           on_sample=lambda d: self.messages.put(("sample", d)),
                           on_state=lambda state: self.messages.put(("state", state)))
            except Exception as exc:
                self.messages.put(("error", str(exc)))
            finally:
                self.messages.put(("finished", None))
        self.worker = threading.Thread(target=work, name="FSX SimConnect bridge", daemon=True)
        self._update_controls()
        self.worker.start()

    def _poll_queue(self):
        # All Tk operations happen here or in main-thread button callbacks.
        for _ in range(200):
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append_log(*value)
            elif kind == "adapters":
                self._apply_adapters(value)
            elif kind == "network_error":
                self._apply_adapters([])
                self._append_log(logging.ERROR, value)
            elif kind == "sample":
                if not self.stop_event.is_set():
                    self._show_sample(value)
            elif kind == "state":
                if not self.stop_event.is_set():
                    text, color = {"connecting": ("Connecting", 'warning'), "waiting": ("Waiting for FSX", 'warning'),
                                   "stale": ("Data stale", 'warning'), "live": ("Broadcasting" if self.broadcast_var.get() else "Transmitting", 'success')}[value]
                    self._set_status(text, color, value == "live")
                    if value != "live":
                        self._clear_badges()
            elif kind == "error":
                self.had_error = True
                self._append_log(logging.ERROR, value)
            elif kind == "finished":
                # The worker enqueues this only after bridge.run's finally closed
                # native/socket resources. Do not join/block the GUI event loop.
                self.worker = None
                self._clear_badges()
                self._set_status("Error" if self.had_error else "Ready", 'danger' if self.had_error else 'warning')
                self._substatus.configure(text="See activity log" if self.had_error else "Configure and start")
                self._append_log(logging.INFO, "Bridge stopped — resources closed.")
                self._update_controls()
                if self.closing:
                    self._destroy()
                    return
        self.poll_id = self.root.after(80, self._poll_queue)

    def on_closing(self):
        if self.worker is not None:
            if not self.closing and not messagebox.askyesno("Exit", "Stop the bridge and exit?", parent=self.root):
                return
            self.closing = True
            self.stop_event.set()
            self._set_status("Stopping", 'warning')
            self._update_controls()
        else:
            self._destroy()

    def _destroy(self):
        self.root.after_cancel(self.poll_id)
        self._pulse_dot.set_color(self.C['warning'])
        self.logger.removeHandler(self.log_handler)
        self.log_handler.close()
        self.logger.setLevel(self.old_log_level)
        self.root.destroy()


def main():
    root = tk.Tk()
    app = GDL90BridgeGUI(root)
    # Surface callback errors in the GUI rather than a missing --windowed console.
    def callback_error(exc_type, exc_value, traceback):
        app._append_log(logging.ERROR, f"Interface error: {exc_value}")
        messagebox.showerror("Interface error", str(exc_value), parent=root)
    root.report_callback_exception = callback_error
    root.mainloop()


if __name__ == "__main__":
    main()
