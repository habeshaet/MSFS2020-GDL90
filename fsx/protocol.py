"""GDL90 encoders adapted from Daniel Aregay's MSFS2020 EFB Connect.

The legacy 0x66 encoder is preserved; ForeFlight uses 0x65/0x01.
"""

import math
import struct

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

def _build_heartbeat(utc_seconds: int, valid=True) -> bytes:
    """Msg 0x00 — GDL90 Heartbeat (1 Hz)."""
    status1 = 0x81 if valid else 0x01
    status2 = 0x00
    ts = utc_seconds & 0x1FFFF
    status2 |= ((ts >> 16) & 0x01) << 7
    payload = bytes([0x00, status1, status2, ts & 0xFF, (ts >> 8) & 0xFF, 0x00, 0x00])
    return _gdl90_frame(payload)


def _build_ownship(lat, lon, alt_ft, track_deg, gs_kt, vs_fpm,
                   icao=0x000000, callsign="FSX", on_ground=False) -> bytes:
    """Msg 0x0A — Ownship Report; alt_ft is PRESSURE altitude."""
    lat24 = _enc24s(lat)
    lon24 = _enc24s(lon)
    alt_enc = max(0, min(0xFFE, int(round((alt_ft + 1000) / 25.0))))
    misc = 0x01 if on_ground else 0x09  # true track, airborne bit
    nic_nacp = (11 << 4) | 11
    hvel = max(0, min(0xFFE, int(round(gs_kt)))) if gs_kt is not None else 0
    if vs_fpm is None:
        vvel = 0x800
    else:
        v = max(-510, min(510, int(round(vs_fpm / 64.0))))
        vvel = (v + 4096 if v < 0 else v) & 0xFFF
    track = int(round((track_deg % 360) * 256.0 / 360.0)) & 0xFF
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
    payload = struct.pack('>BHH', 0x0B, enc, 0x7FFF)
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


def _build_foreflight_ahrs(pitch_deg, roll_deg, hdg_deg, ias_kt, tas_kt):
    """ForeFlight 0x65/0x01: nose up/right wing down, true heading."""
    def angle(value):
        return max(-1800, min(1800, round(value * 10)))

    def speed(value):
        return max(0, min(0xFFFE, round(value)))

    return _gdl90_frame(struct.pack(
        ">BBhhHHH", 0x65, 0x01, angle(roll_deg), angle(pitch_deg),
        round((hdg_deg % 360) * 10) % 3600, speed(ias_kt), speed(tas_kt)))
