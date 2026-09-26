"""Tk widgets adapted from Daniel Aregay's MSFS2020 EFB Connect UI."""

import tkinter as tk


class _StyledButton(tk.Button):
    def __init__(self, parent, text, command=None,
                 bg="#065f46", fg="#6ee7b7",
                 hover_bg="#047857", hover_fg="#a7f3d0",
                 font=("Segoe UI", 10, "bold")):

        super().__init__(parent, text=text, command=command,
                         font=font, bg=bg, fg=fg,
                         relief=tk.FLAT, bd=0,
                         padx=15, pady=8,
                         cursor="hand2",
                         activebackground=hover_bg,
                         activeforeground=hover_fg)

        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg
        self._hover_fg = hover_fg

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_enter(self, event):
        self.config(bg=self._hover_bg, fg=self._hover_fg)

    def _on_leave(self, event):
        self.config(bg=self._bg, fg=self._fg)

    def set_text(self, text):
        self.config(text=text)

    def set_colors(self, bg, fg, hover_bg, hover_fg):
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg
        self._hover_fg = hover_fg
        self.config(bg=bg, fg=fg, activebackground=hover_bg, activeforeground=hover_fg)


# ============================================================
# LIVE DATA DISPLAY WIDGET
# ============================================================

class _DataBadge(tk.Frame):
    def __init__(self, parent, label: str, icon: str, bg: str, accent: str):
        super().__init__(parent, bg=bg)
        self._accent = accent
        self._bg = bg

        tk.Label(self, text=icon, font=("Segoe UI", 10),
                 fg=accent, bg=bg).pack(anchor="w")
        tk.Label(self, text=label.upper(), font=("Segoe UI", 6, "bold"),
                 fg="#64748b", bg=bg).pack(anchor="w")
        self._val = tk.Label(self, text="—", font=("Segoe UI", 13, "bold"),
                             fg="#e2e8f0", bg=bg)
        self._val.pack(anchor="w")

    def update(self, text: str, color: str = "#e2e8f0"):
        self._val.config(text=text, fg=color)


# ============================================================
# PULSING STATUS DOT
# ============================================================

class _PulseDot(tk.Canvas):
    def __init__(self, parent, size=14, bg="#111827"):
        super().__init__(parent, width=size, height=size,
                         bg=bg, highlightthickness=0)
        self._s = size
        self._color = "#f59e0b"
        self._dot = self.create_oval(3, 3, size-3, size-3,
                                      fill=self._color, outline="")
        self._alpha = 1.0
        self._dir = -1
        self._pulsing = False
        self._anim_id = None

    def set_color(self, color: str, pulse: bool = False):
        self._color = color
        self._pulsing = pulse
        if self._anim_id:
            self.after_cancel(self._anim_id)
            self._anim_id = None
        self._alpha = 1.0
        self.itemconfig(self._dot, fill=color)
        if pulse:
            self._pulse()

    def _pulse(self):
        self._alpha += self._dir * 0.06
        if self._alpha <= 0.3:
            self._alpha = 0.3
            self._dir = 1
        elif self._alpha >= 1.0:
            self._alpha = 1.0
            self._dir = -1
        c = self._color.lstrip('#')
        br, bg_c, bb = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        a = self._alpha
        r = int(br * a + 17 * (1 - a))
        g = int(bg_c * a + 24 * (1 - a))
        b = int(bb * a + 39 * (1 - a))
        self.itemconfig(self._dot, fill=f"#{r:02x}{g:02x}{b:02x}")
        if self._pulsing:
            self._anim_id = self.after(40, self._pulse)
