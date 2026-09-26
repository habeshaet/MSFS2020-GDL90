"""Minimal, read-only FSX SimConnect client using the native 32-bit SDK ABI.

No dependency on the MSFS-oriented PyPI SimConnect package. All native calls
and dispatch run on the same thread. Explicit units avoid value-based guesses.
"""

import ctypes as C
from dataclasses import dataclass
import math
import os
from pathlib import Path
import struct
import time


# Order MUST match the packed FLOAT64 array returned by SimConnect.
VARIABLES = (
    ("lat", "PLANE LATITUDE", "degrees"),
    ("lon", "PLANE LONGITUDE", "degrees"),
    ("alt_ft", "PLANE ALTITUDE", "feet"),
    ("pressure_alt_ft", "PRESSURE ALTITUDE", "feet"),
    ("hdg_true", "PLANE HEADING DEGREES TRUE", "degrees"),
    ("track_true", "GPS GROUND TRUE TRACK", "degrees"),
    ("gs_kt", "GROUND VELOCITY", "knots"),
    ("vs_fpm", "VERTICAL SPEED", "feet per minute"),
    ("on_ground", "SIM ON GROUND", "bool"),
    ("pitch", "PLANE PITCH DEGREES", "degrees"),
    ("roll", "PLANE BANK DEGREES", "degrees"),
    ("ias_kt", "AIRSPEED INDICATED", "knots"),
    ("tas_kt", "AIRSPEED TRUE", "knots"),
)
DATA = struct.Struct("<" + "d" * len(VARIABLES))
# SIMCONNECT_RECV_SIMOBJECT_DATA: 3 base DWORDs + 7 metadata DWORDs.
# dwData starts at byte 40, NOT at the end of a padded ctypes struct.
HEADER = struct.Struct("<10I")
REQUEST_ID = DEFINITION_ID = 1
RECV_EXCEPTION, RECV_QUIT, RECV_SIMOBJECT_DATA = 1, 3, 8
E_FAIL = 0x80004005  # GetNextDispatch returns E_FAIL for an empty queue.


@dataclass(frozen=True)
class FlightData:
    lat: float
    lon: float
    alt_ft: float
    pressure_alt_ft: float
    hdg_true: float
    track_true: float
    gs_kt: float
    vs_fpm: float
    on_ground: bool
    pitch: float  # nose up positive
    roll: float  # right wing down positive
    ias_kt: float
    tas_kt: float

    @classmethod
    def from_values(cls, values):
        if len(values) != len(VARIABLES) or not all(map(math.isfinite, values)):
            raise ValueError("FSX returned incomplete or non-finite flight data")
        data = dict(zip((v[0] for v in VARIABLES), values))
        if not -90 <= data["lat"] <= 90 or not -180 <= data["lon"] <= 180:
            raise ValueError("FSX returned invalid coordinates")
        if abs(data["pitch"]) > 180 or abs(data["roll"]) > 180:
            raise ValueError("FSX returned invalid attitude")
        # FSX: nose down and left bank positive. ForeFlight: up and right.
        data["pitch"] = -data["pitch"]
        data["roll"] = -data["roll"]
        data["hdg_true"] %= 360
        data["track_true"] %= 360
        data["on_ground"] = bool(data["on_ground"])
        return cls(**data)


def find_dll(explicit=None):
    """Prefer an explicit SDK DLL, otherwise the installed FSX SxS client."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise OSError(f"SimConnect DLL does not exist: {path}")
        return path
    sxs = Path(os.environ.get("WINDIR", r"C:\Windows")) / "WinSxS"
    # SP2/Acceleration is also shipped with FSX Steam Edition. Avoid accidentally
    # loading a 64-bit MSFS DLL or a managed Microsoft.*.SimConnect assembly.
    matches = sorted(sxs.glob(
        "x86_microsoft.flightsimulator.simconnect_*_10.0.61259.0_*/SimConnect.dll"))
    if matches:
        return matches[-1]
    raise OSError(
        "FSX SP2 SimConnect runtime not found. Install the FSX SP2/Acceleration "
        "SimConnect.msi (FSX-SE: SDK\\Core Utilities Kit\\SimConnect SDK\\"
        "LegacyInterfaces\\FSX-XPACK), or pass --dll with the full path to "
        "the native 32-bit FSX SDK SimConnect.dll. See fsx/README.md.")


def load_dll(path, use_manifest):
    """Activate the installed SP2 SxS assembly without modifying python.exe."""
    if not use_manifest:
        return C.WinDLL(str(path))

    class ActCtx(C.Structure):
        _fields_ = [
            ("cbSize", C.c_uint32), ("dwFlags", C.c_uint32),
            ("lpSource", C.c_wchar_p), ("wProcessorArchitecture", C.c_uint16),
            ("wLangId", C.c_uint16), ("lpAssemblyDirectory", C.c_wchar_p),
            ("lpResourceName", C.c_wchar_p), ("lpApplicationName", C.c_wchar_p),
            ("hModule", C.c_void_p),
        ]

    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateActCtxW.argtypes = [C.POINTER(ActCtx)]
    kernel.CreateActCtxW.restype = C.c_void_p
    kernel.ActivateActCtx.argtypes = [C.c_void_p, C.POINTER(C.c_size_t)]
    kernel.ActivateActCtx.restype = C.c_int32
    kernel.DeactivateActCtx.argtypes = [C.c_uint32, C.c_size_t]
    kernel.DeactivateActCtx.restype = C.c_int32
    kernel.ReleaseActCtx.argtypes = [C.c_void_p]
    kernel.ReleaseActCtx.restype = None
    context = ActCtx()
    context.cbSize = C.sizeof(context)
    context.lpSource = str(Path(__file__).with_name("SimConnect.manifest"))
    handle = kernel.CreateActCtxW(C.byref(context))
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    cookie = C.c_size_t()
    active = False
    try:
        if not kernel.ActivateActCtx(handle, C.byref(cookie)):
            raise C.WinError(C.get_last_error())
        active = True
        return C.WinDLL(str(path))
    finally:
        if active:
            kernel.DeactivateActCtx(0, cookie)
        kernel.ReleaseActCtx(handle)


class SimConnectSource:
    def __init__(self, dll_path=None):
        self.dll_path = dll_path
        self.dll = None
        self.handle = C.c_void_p()
        self.latest = None
        self.last_received = None

    @staticmethod
    def _check(result, operation):
        if result & 0x80000000:
            raise OSError(f"{operation} failed (HRESULT 0x{result & 0xFFFFFFFF:08X})")

    def _bind(self):
        u32, handle = C.c_uint32, C.c_void_p
        signatures = {
            "Open": [C.POINTER(handle), C.c_char_p, handle, u32, handle, u32],
            "Close": [handle],
            "AddToDataDefinition": [handle, u32, C.c_char_p, C.c_char_p,
                                    u32, C.c_float, u32],
            "RequestDataOnSimObject": [handle] + [u32] * 8,
            "GetNextDispatch": [handle, C.POINTER(handle), C.POINTER(u32)],
        }
        for name, args in signatures.items():
            fn = getattr(self.dll, "SimConnect_" + name)
            fn.argtypes = args
            fn.restype = C.c_int32  # HRESULT is always 32 bits.

    def connect(self):
        if os.name != "nt":
            raise OSError("Live FSX connection requires Windows and 32-bit Python.")
        if C.sizeof(C.c_void_p) != 4:
            raise OSError("FSX requires 32-bit (x86) Python, even on 64-bit Windows. "
                          "Use py -3-32 or the full path to your x86 python.exe.")
        path = find_dll(self.dll_path)
        try:
            self.dll = load_dll(path, use_manifest=self.dll_path is None)
        except OSError as exc:
            raise OSError(f"Cannot load {path}: {exc}. Use the native x86 FSX "
                          "DLL and install its SimConnect.msi runtime, not the "
                          "MSFS or managed DLL. See fsx/README.md.") from exc
        self._bind()
        try:
            self._check(self.dll.SimConnect_Open(
                C.byref(self.handle), b"FSX EFB Connect", None, 0, None, 0),
                "SimConnect_Open (start FSX and load a flight first)")
            for _, name, unit in VARIABLES:
                self._check(self.dll.SimConnect_AddToDataDefinition(
                    self.handle, DEFINITION_ID, name.encode("ascii"),
                    unit.encode("ascii"), 4, 0.0, 0xFFFFFFFF),  # FLOAT64 = 4
                    f"AddToDataDefinition({name})")
            self._check(self.dll.SimConnect_RequestDataOnSimObject(
                self.handle, REQUEST_ID, DEFINITION_ID, 0, 3, 0, 0, 0, 0),
                "RequestDataOnSimObject")  # USER = 0, SIM_FRAME = 3
        except BaseException:
            self.close()
            raise

    def _receive(self, packet, now):
        if len(packet) < 12:
            raise OSError("Truncated SimConnect receive header")
        size, _, kind = struct.unpack_from("<III", packet)
        if size < 12 or size > len(packet):
            raise OSError("Invalid SimConnect receive size")
        packet = packet[:size]
        if kind == RECV_QUIT:
            self.latest = None
            raise ConnectionError("FSX closed. Restart FSX, then restart the bridge.")
        if kind == RECV_EXCEPTION:
            if size < 24:
                raise OSError("Truncated SimConnect exception")
            code, send_id, index = struct.unpack_from("<III", packet, 12)
            raise OSError(f"SimConnect exception {code} (send {send_id}, index {index})")
        if kind != RECV_SIMOBJECT_DATA:
            return  # e.g. SIMCONNECT_RECV_OPEN
        if size < HEADER.size:
            raise OSError("Truncated SimConnect data header")
        fields = HEADER.unpack_from(packet)
        if fields[3:6] != (REQUEST_ID, 0, DEFINITION_ID):
            return
        if fields[6] != 0 or fields[9] != len(VARIABLES):
            raise OSError("Unexpected SimConnect data definition/flags")
        if size < HEADER.size + DATA.size:
            raise OSError("Truncated SimConnect flight data")
        self.latest = FlightData.from_values(DATA.unpack_from(packet, HEADER.size))
        self.last_received = now

    def poll(self):
        # Copy before the next native call invalidates the receive buffer.
        for _ in range(1000):
            pointer, size = C.c_void_p(), C.c_uint32()
            result = self.dll.SimConnect_GetNextDispatch(
                self.handle, C.byref(pointer), C.byref(size))
            if result & 0xFFFFFFFF == E_FAIL:
                break
            self._check(result, "GetNextDispatch")
            if not pointer.value or not 12 <= size.value <= 1024 * 1024:
                raise OSError("Invalid SimConnect dispatch buffer")
            self._receive(C.string_at(pointer, size.value), time.monotonic())
        return self.latest

    def close(self):
        if self.dll is not None and self.handle.value:
            self.dll.SimConnect_Close(self.handle)
        self.handle = C.c_void_p()
        self.latest = None
        self.last_received = None
