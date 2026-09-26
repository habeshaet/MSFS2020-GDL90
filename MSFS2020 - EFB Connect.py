#!/usr/bin/env python3
"""
MSFS2020 → GDL90 UDP Bridge
============================
Broadcasts GDL90 position data from Microsoft Flight Simulator 2020
to FD Pro X, ForeFlight, and any GDL90-compatible EFB on your network.

Now with ATTITUDE DATA (pitch, roll, heading) for synthetic vision!
Developed By: Daniel Aregay
"""

import math
import socket
import struct
import sys
import time
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox
from datetime import datetime
import queue

# ============================================================
# GDL90 PROTOCOL — CRC, FRAMING, ENCODING
# ============================================================

GDL90_FLAG = 0x7E
GDL90_CTRL_ESC = 0x7D


def _build_crc_table():
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = (crc << 1) ^ (0x1021 if (crc & 0x8000) else 0)
        table.append(crc & 0xFFFF)
    return table


_CRC_TABLE = _build_crc_table()


def _gdl90_crc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = (_CRC_TABLE[crc >> 8] ^ ((crc << 8) & 0xFFFF) ^ b) & 0xFFFF
    return crc


def _gdl90_stuff(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b == GDL90_FLAG or b == GDL90_CTRL_ESC:
            out.append(GDL90_CTRL_ESC)
            out.append(b ^ 0x20)
        else:
            out.append(b)
    return bytes(out)


def _gdl90_frame(payload: bytes) -> bytes:
    crc = _gdl90_crc(payload)
    raw = payload + struct.pack('<H', crc)
    return bytes([GDL90_FLAG]) + _gdl90_stuff(raw) + bytes([GDL90_FLAG])


def _enc24s(deg: float) -> int:
    if deg is None:
        deg = 0.0
    lsb = 180.0 / (1 << 23)
    raw = int(round(deg / lsb))
    raw = max(-8388608, min(8388607, raw))
    return (raw + (1 << 24) if raw < 0 else raw) & 0xFFFFFF


def _to24(v: int) -> bytes:
    return bytes([(v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])


# ============================================================
# GDL90 MESSAGE BUILDERS
# ============================================================

def _build_heartbeat(utc_seconds: int) -> bytes:
    """Msg 0x00 — GDL90 Heartbeat (1 Hz)."""
    status1 = 0x81
    status2 = 0x00
    ts = utc_seconds & 0x1FFFF
    status2 |= ((ts >> 16) & 0x01) << 7
    payload = bytes([0x00, status1, status2, ts & 0xFF, (ts >> 8) & 0xFF, 0x00, 0x00])
    return _gdl90_frame(payload)


def _build_ownship(lat, lon, alt_ft, hdg_deg, gs_kt, vs_fpm,
                   icao=0xFFFFFF, callsign="MSFS", on_ground=False) -> bytes:
    """Msg 0x0A — Ownship Report."""
    lat24 = _enc24s(lat)
    lon24 = _enc24s(lon)
    alt_enc = max(0, min(0xFFE, int(round((alt_ft + 1000) / 25.0))))
    misc = 0x03 if on_ground else 0x0B
    nic_nacp = (11 << 4) | 11
    hvel = max(0, min(0xFFE, int(round(gs_kt)))) if gs_kt is not None else 0
    if vs_fpm is None:
        vvel = 0x800
    else:
        v = max(-512, min(511, int(round(vs_fpm / 64.0))))
        vvel = (v + 4096 if v < 0 else v) & 0xFFF
    track = int(round((hdg_deg % 360) * 256.0 / 360.0)) & 0xFF
    cs = (callsign.upper() + "        ")[:8].encode('ascii', errors='replace')

    p = bytearray()
    p.append(0x0A)
    p.append(0x00)
    p += _to24(icao)
    p += _to24(lat24)
    p += _to24(lon24)
    p.append((alt_enc >> 4) & 0xFF)
    p.append(((alt_enc & 0x0F) << 4) | (misc & 0x0F))
    p.append(nic_nacp)
    p.append((hvel >> 4) & 0xFF)
    p.append(((hvel & 0x0F) << 4) | ((vvel >> 8) & 0x0F))
    p.append(vvel & 0xFF)
    p.append(track)
    p.append(0x02)
    p += cs
    p.append(0x00)
    return _gdl90_frame(bytes(p))


def _build_geo_altitude(alt_ft: float) -> bytes:
    """Msg 0x0B — Ownship Geometric Altitude."""
    enc = int(round(alt_ft / 5.0))
    enc = max(-32768, min(32767, enc))
    if enc < 0:
        enc += 65536
    payload = struct.pack('>BHH', 0x0B, enc, 15)
    return _gdl90_frame(payload)


def _build_attitude(pitch_deg: float, roll_deg: float, hdg_deg: float) -> bytes:
    """Msg 0x66 — Attitude Data."""
    pitch_rad = math.radians(pitch_deg)
    roll_rad = math.radians(roll_deg)
    
    p = bytearray()
    p.append(0x66)
    p.extend(struct.pack('<f', pitch_rad))
    p.extend(struct.pack('<f', roll_rad))
    p.extend(struct.pack('<f', hdg_deg % 360))
    p.append(0x01)  # Valid attitude data
    p.append(0x00)
    p.append(0x00)
    p.append(0x00)
    
    return _gdl90_frame(bytes(p))


def _build_foreflight_id(callsign: str) -> bytes:
    """Msg 0x65 — ForeFlight/GDL90 Device ID."""
    name = (callsign.upper() + "        ")[:8].encode('ascii', errors='replace')
    long_name = (callsign.upper() + "-GDL90" + " " * 16)[:16].encode('ascii', errors='replace')
    p = bytearray()
    p.append(0x65)
    p.append(0x00)
    p.append(0x01)
    p += struct.pack('>Q', 0xFFFFFFFFFFFFFFFF)
    p += name
    p += long_name
    p += struct.pack('>I', 0x00000001)
    return _gdl90_frame(bytes(p))


# ============================================================
# SIMCONNECT DATA SOURCE
# ============================================================

class SimConnectSource:
    def __init__(self):
        self.sm = None
        self.aq = None
        self.connected = False

    def connect(self):
        from SimConnect import SimConnect, AircraftRequests
        self.sm = SimConnect()
        self.aq = AircraftRequests(self.sm, _time=200)
        self.connected = True

    def _get(self, name, default=0.0):
        v = self.aq.get(name)
        return default if v is None else v

    def read(self) -> dict:
        lat = self._get("PLANE_LATITUDE", 0.0)
        lon = self._get("PLANE_LONGITUDE", 0.0)
        alt_ft = self._get("PLANE_ALTITUDE", 0.0)
        hdg = self._get("PLANE_HEADING_DEGREES_TRUE", 0.0)
        gs_kt = self._get("GROUND_VELOCITY", 0.0)
        vs_raw = self._get("VERTICAL_SPEED", 0.0)
        on_gnd = bool(self._get("SIM_ON_GROUND", 0))
        
        pitch_rad = self._get("PLANE_PITCH_DEGREES", 0.0)
        roll_rad = self._get("PLANE_BANK_DEGREES", 0.0)
        
        pitch = -math.degrees(pitch_rad)
        roll = math.degrees(roll_rad)

        if hdg is not None and 0.0 <= hdg <= (2 * math.pi + 0.1):
            hdg = math.degrees(hdg)
        hdg = (hdg or 0.0) % 360.0

        if vs_raw is not None and abs(vs_raw) < 100:
            vs_fpm = vs_raw * 60.0
        else:
            vs_fpm = vs_raw or 0.0

        if (gs_kt is None or gs_kt == 0.0) and not on_gnd:
            gs_kt = self._get("AIRSPEED_TRUE", 0.0)

        return {
            'lat': float(lat or 0.0),
            'lon': float(lon or 0.0),
            'alt_ft': float(alt_ft or 0.0),
            'hdg_true': float(hdg),
            'gs_kt': float(gs_kt or 0.0),
            'vs_fpm': float(vs_fpm),
            'on_ground': on_gnd,
            'pitch': float(pitch or 0.0),
            'roll': float(roll or 0.0),
        }

    def close(self):
        try:
            if self.sm:
                self.sm.exit()
        except Exception:
            pass


# ============================================================
# BRIDGE ENGINE
# ============================================================

class GDL90Bridge:
    def __init__(self, ip, port, rate=5.0, icao='FFFFFF',
                 callsign='MSFS', broadcast=True, log_cb=None):
        self.ip = ip
        self.port = port
        self.rate = max(0.5, min(20.0, rate))
        self.icao = int(icao, 16) & 0xFFFFFF
        self.callsign = (callsign or "MSFS").strip()[:8]
        self.broadcast = broadcast
        self.log_cb = log_cb

        self.sock = None
        self.source = None
        self.stop_flag = threading.Event()
        self.running = False
        self.hb_thread = None

        self.pkt_count = 0
        self.bytes_sent = 0
        self.start_time = None

    def _log(self, msg, status=False):
        if self.log_cb:
            self.log_cb(msg, status)
        print(msg)

    @staticmethod
    def _utc_seconds() -> int:
        t = time.gmtime()
        return t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec

    def _setup_socket(self) -> tuple:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if self.broadcast:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            dest = self.ip if self.ip.endswith('.255') else '255.255.255.255'
        else:
            dest = self.ip
        return (dest, self.port)

    def _heartbeat_thread(self, dest):
        while not self.stop_flag.is_set():
            try:
                msg = _build_heartbeat(self._utc_seconds())
                self.sock.sendto(msg, dest)
                self.pkt_count += 1
                self.bytes_sent += len(msg)
            except Exception as e:
                self._log(f"[HB] send error: {e}")
            self.stop_flag.wait(1.0)

    def start(self) -> bool:
        if self.running:
            return False

        dest = self._setup_socket()
        mode = "broadcast" if self.broadcast else "unicast"
        self._log(f"Socket ready → {dest[0]}:{dest[1]} ({mode})")

        try:
            self._log("Connecting to MSFS2020 via SimConnect…")
            self.source = SimConnectSource()
            self.source.connect()
            self._log("✓ SimConnect connected")
        except Exception as e:
            self._log(f"✗ SimConnect failed: {e}")
            return False

        self.stop_flag.clear()
        self.start_time = time.time()
        self.hb_thread = threading.Thread(
            target=self._heartbeat_thread, args=(dest,), daemon=True)
        self.hb_thread.start()

        self.running = True
        self._log("─" * 56)
        self._log(f"▶ Broadcasting GDL90  |  callsign: {self.callsign}  |  {self.rate} Hz")
        self._log("─" * 56)
        return True

    def run_loop(self):
        if not self.running:
            return

        dest = (
            (self.ip if self.ip.endswith('.255') else '255.255.255.255')
            if self.broadcast else self.ip,
            self.port
        )

        period = 3.0 / self.rate
        last_ffid = 0.0
        last_log = 0.0
        last_stats = 0.0
        LOG_INTERVAL = 2.0
        STATS_INTERVAL = 30.0

        while self.running and not self.stop_flag.is_set():
            t0 = time.time()

            try:
                d = self.source.read()
            except Exception as e:
                self._log(f"[READ] {e}")
                time.sleep(1.0)
                continue

            lat = d['lat']
            lon = d['lon']
            alt_ft = d['alt_ft']
            hdg = d['hdg_true']
            gs = d['gs_kt']
            vs = d['vs_fpm']
            on_gnd = d['on_ground']
            pitch = d['pitch']
            roll = d['roll']

            try:
                own = _build_ownship(lat, lon, alt_ft, hdg, gs, vs,
                                     icao=self.icao, callsign=self.callsign,
                                     on_ground=on_gnd)
                self.sock.sendto(own, dest)
                self.pkt_count += 1
                self.bytes_sent += len(own)

                geo = _build_geo_altitude(alt_ft)
                self.sock.sendto(geo, dest)
                self.pkt_count += 1
                self.bytes_sent += len(geo)

                att = _build_attitude(pitch, roll, hdg)
                self.sock.sendto(att, dest)
                self.pkt_count += 1
                self.bytes_sent += len(att)

            except Exception as e:
                self._log(f"[SEND] {e}")

            now = time.time()
            if now - last_ffid >= 1.0:
                try:
                    ffid = _build_foreflight_id(self.callsign)
                    self.sock.sendto(ffid, dest)
                    self.pkt_count += 1
                    self.bytes_sent += len(ffid)
                    last_ffid = now
                except Exception as e:
                    self._log(f"[FFID] {e}")

            if now - last_log >= LOG_INTERVAL:
                status = (f"TX ▸ {self.callsign} | "
                          f"{lat:+.4f}° {lon:+.4f}° | "
                          f"ALT {alt_ft:.0f} ft | "
                          f"GS {gs:.0f} kt | HDG {hdg:.0f}° | "
                          f"Pitch {pitch:+.1f}° Roll {roll:+.1f}°")
                self._log(status, status=True)
                last_log = now

            if now - last_stats >= STATS_INTERVAL:
                elapsed = now - self.start_time
                pps = self.pkt_count / elapsed
                kbps = (self.bytes_sent * 8) / (elapsed * 1000)
                self._log(f"STATS ▸ {self.pkt_count} pkts | "
                          f"{self.bytes_sent / 1024:.1f} KB | "
                          f"{pps:.1f} pps | {kbps:.2f} kbps")
                last_stats = now

            sleep = period - (time.time() - t0)
            if sleep > 0:
                time.sleep(sleep)

    def stop(self):
        self._log("Stopping bridge…")
        self.running = False
        self.stop_flag.set()

        if self.hb_thread:
            self.hb_thread.join(timeout=2)

        if self.source:
            try:
                self.source.close()
            except Exception:
                pass

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

        if self.start_time:
            elapsed = time.time() - self.start_time
            self._log(f"Session: {self.pkt_count} packets | "
                      f"{self.bytes_sent / 1024:.1f} KB | "
                      f"{elapsed:.0f} s")
        self._log("✓ Bridge stopped")


# ============================================================
# STYLED BUTTON
# ============================================================

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


# ============================================================
# MAIN GUI
# ============================================================

class GDL90BridgeGUI:
    C = {
        'bg': '#0b1220',
        'card': '#111827',
        'input': '#1e293b',
        'border': '#1e3a5f',
        'accent': '#38bdf8',
        'accent2': '#818cf8',
        'success': '#22c55e',
        'warning': '#f59e0b',
        'danger': '#ef4444',
        'txt': '#e2e8f0',
        'txt2': '#94a3b8',
        'log_bg': '#020617',
        'log_txt': '#86efac',
        'separator': '#1e3a5f',
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("MSFS2020 → GDL90 Bridge")
        self.root.geometry("1000x700")
        self.root.minsize(800, 600)
        self.root.configure(bg=self.C['bg'])

        self.bridge = None
        self.bridge_thread = None
        self.running = False
        self.msg_queue = queue.Queue()

        self._build_ui()
        self._poll_queue()

        self._log("MSFS2020 → GDL90 Bridge ready")
        self._log("SimConnect required · pip install SimConnect")
        self._log("─" * 56)
        self._log("✓ Attitude data (pitch/roll/heading) included")
        self._log("─" * 56)

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
            new_width = max(180, min(260, int(event.width * 0.18)))
            sidebar.config(width=new_width)
        
        root_frame.bind("<Configure>", update_sidebar_width)
        sidebar.config(width=200)

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
        tk.Label(top, text="MSFS2020 → EFB",
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

        # Header
        hdr = tk.Frame(main, bg=C['bg'])
        hdr.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(hdr, text="MSFS2020 → GDL90 Bridge",
                 font=("Segoe UI", 14, "bold"), fg=C['txt'], bg=C['bg']).pack(side=tk.LEFT)
        tk.Label(hdr, text="FD Pro X · ForeFlight · Any GDL90 EFB",
                 font=("Segoe UI", 8), fg=C['txt2'], bg=C['bg']).pack(
            side=tk.LEFT, padx=(10, 0), pady=(3, 0))

        # Settings card
        sc, si = self._card(main, pad_x=15, pad_y=12)
        sc.pack(fill=tk.X, pady=(0, 10))

        tk.Label(si, text="CONNECTION", font=("Segoe UI", 8, "bold"),
                 fg=C['accent'], bg=C['card']).pack(anchor="w", pady=(0, 8))

        grid = tk.Frame(si, bg=C['card'])
        grid.pack(fill=tk.X)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        fields = [
            ("Target IP", "ip_var", "192.168.1.255", 0, 0),
            ("Port", "port_var", "4000", 0, 1),
            ("Callsign", "callsign_var", "MSFS", 1, 0),
            ("Rate (Hz)", "rate_var", "8", 1, 1),
        ]
        self._entries = {}
        for label, vname, default, row, col in fields:
            cell = tk.Frame(grid, bg=C['card'])
            cell.grid(row=row, column=col, padx=(0, 15), pady=(0, 8), sticky="w")
            cell.grid_columnconfigure(0, weight=1)
            
            tk.Label(cell, text=label, font=("Segoe UI", 7, "bold"),
                     fg=C['txt2'], bg=C['card']).pack(anchor="w")
            var = tk.StringVar(value=default)
            setattr(self, vname, var)
            e = tk.Entry(cell, textvariable=var,
                         font=("Segoe UI", 10),
                         bg=C['input'], fg=C['txt'],
                         insertbackground=C['accent'],
                         relief=tk.FLAT, bd=0,
                         highlightthickness=1,
                         highlightbackground=C['border'],
                         highlightcolor=C['accent'])
            e.pack(fill=tk.X, ipady=5, pady=(2, 0))
            self._entries[vname] = e

        # Broadcast checkbox
        bc_row = tk.Frame(si, bg=C['card'])
        bc_row.pack(fill=tk.X, pady=(0, 2))
        self.broadcast_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bc_row,
                       text="Broadcast Mode (recommended)",
                       variable=self.broadcast_var,
                       font=("Segoe UI", 8, "bold"),
                       fg=C['success'], bg=C['card'],
                       selectcolor=C['card'],
                       activebackground=C['card'],
                       activeforeground=C['success']).pack(side=tk.LEFT)

        # Button row
        btn_row = tk.Frame(main, bg=C['bg'])
        btn_row.pack(fill=tk.X, pady=(0, 10))

        self._start_btn = _StyledButton(
            btn_row, text="▶   START BROADCAST",
            command=self._toggle,
            bg="#065f46", fg="#6ee7b7",
            hover_bg="#047857", hover_fg="#a7f3d0",
            font=("Segoe UI", 10, "bold")
        )
        self._start_btn.pack(side=tk.LEFT, padx=(0, 8))

        clear_btn = _StyledButton(
            btn_row, text="✕  Clear Log",
            command=self._clear_log,
            bg="#1e293b", fg="#94a3b8",
            hover_bg="#334155", hover_fg="#e2e8f0",
            font=("Segoe UI", 9)
        )
        clear_btn.pack(side=tk.RIGHT)

        # Log card
        lc, li = self._card(main, pad_x=12, pad_y=10)
        lc.pack(fill=tk.BOTH, expand=True)

        log_hdr = tk.Frame(li, bg=C['card'])
        log_hdr.pack(fill=tk.X, pady=(0, 5))
        tk.Label(log_hdr, text="ACTIVITY LOG",
                 font=("Segoe UI", 9, "bold"),
                 fg=C['accent'], bg=C['card']).pack(side=tk.LEFT)
        self._live_dot = tk.Label(log_hdr, text="● LIVE",
                                   font=("Segoe UI", 7, "bold"),
                                   fg=C['txt2'], bg=C['card'])
        self._live_dot.pack(side=tk.RIGHT)

        self._log_box = scrolledtext.ScrolledText(
            li,
            font=("Cascadia Code", 8),
            bg=C['log_bg'], fg=C['log_txt'],
            insertbackground=C['log_txt'],
            relief=tk.FLAT, bd=0,
            wrap=tk.WORD,
            padx=8, pady=8,
        )
        self._log_box.pack(fill=tk.BOTH, expand=True)

        self._log_box.tag_config("ts", foreground="#334155")
        self._log_box.tag_config("ok", foreground="#22c55e")
        self._log_box.tag_config("err", foreground="#ef4444")
        self._log_box.tag_config("info", foreground="#38bdf8")
        self._log_box.tag_config("stat", foreground="#a78bfa")
        self._log_box.tag_config("warn", foreground="#f59e0b")
        self._log_box.tag_config("dim", foreground="#475569")

    def _log(self, msg: str, status: bool = False):
        self.msg_queue.put(("log", msg))
        if status:
            self.msg_queue.put(("sub", msg))

    def _clear_log(self):
        self._log_box.delete("1.0", tk.END)

    def _classify_tag(self, msg: str) -> str:
        m = msg.lower()
        if "✓" in msg or "success" in m or "connected" in m:
            return "ok"
        if "✗" in msg or "error" in m or "fail" in m:
            return "err"
        if "tx ▸" in msg or "stats ▸" in msg:
            return "stat"
        if "warning" in m or "warn" in m:
            return "warn"
        if "─" in msg or "═" in msg:
            return "dim"
        return None

    def _poll_queue(self):
        try:
            while True:
                kind, val = self.msg_queue.get_nowait()
                if kind == "log":
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    tag = self._classify_tag(val)
                    self._log_box.insert(tk.END, f"[{ts}] ", "ts")
                    self._log_box.insert(tk.END, val + "\n", tag or "")
                    self._log_box.see(tk.END)
                    self._parse_live(val)
                elif kind == "sub":
                    self._substatus.config(text=val[:60])
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _parse_live(self, msg: str):
        if "TX ▸" not in msg:
            return
        try:
            parts = [p.strip() for p in msg.split("|")]
            
            if len(parts) >= 6:
                coord_parts = parts[1].split()
                if len(coord_parts) >= 2:
                    self._badges['lat'].update(coord_parts[0], "#38bdf8")
                    self._badges['lon'].update(coord_parts[1], "#38bdf8")
                
                alt_str = parts[2].replace("ALT", "").replace("ft", "").strip()
                if alt_str:
                    self._badges['alt'].update(f"{float(alt_str):.0f} ft", "#22c55e")
                
                gs_str = parts[3].replace("GS", "").replace("kt", "").strip()
                if gs_str:
                    self._badges['gs'].update(f"{float(gs_str):.0f} kt", "#a78bfa")
                
                hdg_str = parts[4].replace("HDG", "").replace("°", "").strip()
                if hdg_str:
                    self._badges['hdg'].update(f"{float(hdg_str):.0f}°", "#f59e0b")
                
                att_str = parts[5]
                if "Pitch" in att_str:
                    pitch_part = att_str.split("Pitch")[1].split("Roll")[0].replace("°", "").strip()
                    if pitch_part:
                        pitch_val = float(pitch_part)
                        self._badges['pitch'].update(f"{pitch_val:+.1f}°", "#818cf8")
                if "Roll" in att_str:
                    roll_part = att_str.split("Roll")[1].replace("°", "").strip()
                    if roll_part:
                        roll_val = float(roll_part)
                        self._badges['roll'].update(f"{roll_val:+.1f}°", "#f472b6")
                        
        except Exception:
            pass

    def _set_ui_running(self, running: bool):
        C = self.C
        if running:
            self._start_btn.set_text("■   STOP")
            self._start_btn.set_colors("#7f1d1d", "#fca5a5", "#991b1b", "#fecaca")
            self._pulse_dot.set_color(C['success'], pulse=True)
            self._status_lbl.config(text="Broadcasting", fg=C['success'])
            self._live_dot.config(fg=C['success'])
            for e in self._entries.values():
                e.config(state="disabled")
        else:
            self._start_btn.set_text("▶   START")
            self._start_btn.set_colors("#065f46", "#6ee7b7", "#047857", "#a7f3d0")
            self._pulse_dot.set_color(C['warning'], pulse=False)
            self._status_lbl.config(text="Ready", fg=C['warning'])
            self._substatus.config(text="Configure and start")
            self._live_dot.config(fg=C['txt2'])
            for e in self._entries.values():
                e.config(state="normal")
            for b in self._badges.values():
                b.update("—")

    def _toggle(self):
        if self.running:
            self._do_stop()
        else:
            self._do_start()

    def _do_start(self):
        ip = self.ip_var.get().strip()
        raw_port = self.port_var.get().strip()
        callsign = self.callsign_var.get().strip() or "MSFS"
        raw_rate = self.rate_var.get().strip()

        if not ip:
            messagebox.showerror("Invalid", "Please enter a target IP address.")
            return
        try:
            port = int(raw_port)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid", "Port must be an integer 1–65535.")
            return
        try:
            rate = float(raw_rate)
            if not (0.1 <= rate <= 20):
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid", "Rate must be between 0.1 and 20 Hz.")
            return

        self._clear_log()
        self._log("═" * 56)
        self._log(f"Starting → {ip}:{port} | {callsign} | {rate} Hz")

        self.bridge = GDL90Bridge(
            ip=ip, port=port, rate=rate,
            callsign=callsign,
            broadcast=self.broadcast_var.get(),
            log_cb=self._log
        )

        if not self.bridge.start():
            messagebox.showerror(
                "Connection Failed",
                "Could not connect to MSFS2020.\n\n"
                "Make sure:\n"
                "  • MSFS2020 is running\n"
                "  • You are in a flight (not the main menu)\n"
                "  • SimConnect is installed:\n"
                "    pip install SimConnect"
            )
            self.bridge = None
            return

        self.running = True
        self._set_ui_running(True)

        self.bridge_thread = threading.Thread(
            target=self.bridge.run_loop, daemon=True)
        self.bridge_thread.start()

    def _do_stop(self):
        if self.bridge:
            self.bridge.stop()
            self.bridge = None
        if self.bridge_thread:
            self.bridge_thread.join(timeout=3)
            self.bridge_thread = None
        self.running = False
        self._set_ui_running(False)
        self._log("═" * 56)
        self._log("Bridge stopped — ready for new session")

    def on_closing(self):
        if self.running:
            if messagebox.askyesno("Exit", "Bridge is broadcasting.\nStop and exit?"):
                self._do_stop()
                self.root.destroy()
        else:
            self.root.destroy()


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default="app_icon.ico")
    except Exception:
        pass

    app = GDL90BridgeGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.mainloop()


if __name__ == "__main__":
    main()