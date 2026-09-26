# FSX → FD Pro X / ForeFlight bridge

A separate Python bridge for **FSX SP2 / Acceleration and FSX: Steam Edition
on Windows**. It sends simulated ownship position and attitude over UDP. The
original MSFS Python connector is unchanged. The CLI uses only the standard
library; the desktop GUI adds psutil for network-adapter discovery.

**Status:** the user has confirmed the 0.2 CLI works with their FSX setup.
Version **0.3.0** adds a desktop interface, automatic subnet broadcast selection,
and Windows EXE build scripts. **0.3.1** fixes the x86 dependency installation by
pinning a psutil release with a verified Windows 32-bit wheel and refusing local
source compilation. The new GUI and EXE still require live Windows validation.
Offline protocol, receive-handler, network calculation, worker lifecycle and
build-command tests are included.

**Simulation use only.** This supplies telemetry, not charts, subscriptions,
weather or traffic. Your EFB must already provide the charts/features you want.
Do not use simulated data for real flight navigation.

## Desktop app and EXE (0.3.0)

**Want an EXE instead of a batch launcher?** Double-click **`fsx/build_exe.bat`**
on Windows. It finds your 32-bit Python and builds:

```text
fsx\dist\FSX - EFB Connect.exe
```

After that, just double-click the EXE. It bundles Python, Tk, psutil and the
activation manifest, so Python is not needed to run it. Your existing FSX
SimConnect runtime is still required. **[Full build/use instructions](BUILD_WINDOWS.md)**
include the no-`py`-launcher option and troubleshooting. If an older build fails
compiling `psutil` with a missing `basetsd.h`, see the **0.3.1 fix** at the top
of that guide: use `psutil==7.1.1` and prebuilt wheels, not a C++ compiler.

The desktop interface reuses the MSFS connector's dark palette, sidebar,
status indicator and live data badges. Start/Stop, errors and telemetry run
through a thread-safe queue; the UI never calls SimConnect from its Tk thread.

Automatic mode uses the chosen active adapter's IP **and netmask**:

| PC address | Mask | Automatic target |
| --- | --- | --- |
| `192.168.1.13` | `255.255.255.0` | `192.168.1.255` |
| `192.168.1.13` | `255.255.0.0` | `192.168.255.255` |
| `192.168.1.13` | `255.255.255.128` | `192.168.1.127` |

The default-route adapter is preferred when available. Choose Wi-Fi/Ethernet
explicitly if a VPN or another adapter is selected. The sender binds to the
chosen local IP. Detection runs on launch and **Refresh**; stop and refresh
after changing networks. No usable adapter means Auto cannot start: the app
does not guess a `/24` mask or silently send a global broadcast.

Manual unicast remains available: disable Auto and Broadcast and enter the
iPad IP. This can be more reliable for ForeFlight than broadcast. The CLI
continues working unchanged, with an optional `--bind-ip` to choose its local
source address.

To run the GUI from source instead of building (from the repository root):

```bat
py -3-32 -m pip install -r fsx\requirements-gui.txt
py -3-32 -m fsx.gui
```

Use your x86 interpreter's full path if `py` is unavailable. EXE builds must
run on Windows; this repository supplies source/build scripts, not a Windows
binary built in the Linux sandbox.

## Earlier fix (0.2.0): "SimConnect opened" followed by a 15-second timeout

The initial receive handler incorrectly required the returned aircraft object
ID to be `0`. That value is the **request alias** for the user aircraft, not a
required ID in the reply. FSX commonly returns `1`, and the actual ID can vary
in multiplayer [2](https://www.fsdeveloper.com/forum/threads/who-is-object-id-1.8833/).
The old check silently discarded those valid samples and could produce a timeout
even with an active, unpaused flight.

Version **0.2.0** matches replies by our request and data-definition IDs instead.
The subscription still requests only the user aircraft; it does not accept
unrelated traffic subscriptions. Regression tests reproduce the old failure and
verify nonzero object IDs now reach valid GDL90 position/AHRS output. This fix
remains in the 0.3 desktop app and CLI.

**Replace the entire `fsx` folder with the updated version**, not just the batch
file. Keep your installed Python and SimConnect runtime. On startup the console
must show `FSX EFB Connect 0.3.1` for the current version. With working data, it will also show:

```text
SimConnect server acknowledged OPEN.
Accepted first FSX flight sample (object ID 1).
Receiving live FSX data.
```

The actual object ID can differ. If it still times out, start from Command
Prompt in the repository/extracted folder:

```bat
fsx\start_fsx.bat --debug
```

Share the full output, starting with the version and DLL path. The timeout now
includes receive diagnostics: whether the server acknowledged OPEN, received
message types/counts, accepted/ignored sample counts, and the last data header.
`OPEN=yes` with only type `2` means the server handshake arrived but no aircraft
data messages did. Type `8` is an aircraft-data response. A successful
`SimConnect_Open` call alone is not proof that aircraft samples are arriving.

## Why a different FSX client?

The original script imports `SimConnect` from PyPI. MSFS-oriented wrappers can
ship a 64-bit MSFS DLL; that is not a drop-in FSX client. FSX needs a matching
native 32-bit client and a **32-bit Python process**, even on 64-bit Windows.
This mismatch and the x86 SDK DLL workaround are documented in the FSDeveloper
discussion [2](https://www.fsdeveloper.com/forum/threads/msfs2020-cockpit-companion-an-http-interface-for-simconnect.449024/).

This implementation uses `ctypes` directly, an installed FSX SP2 SimConnect
assembly, and explicit FSX variable units. No `pip install SimConnect`, FSUIPC,
.NET wrapper, or edits to your MSFS Python installation are needed.

## 1. Windows prerequisites

1. **Keep your existing 64-bit Python installed.** Install **32-bit (x86)
   Python 3.9 or newer** alongside it in a **different folder**. For example,
   use a Python 3.13 Windows **32-bit** installer from python.org, not the
   64-bit or ARM64 installer. A typical per-user installation folder is
   `%LocalAppData%\Programs\Python\Python313-32`.

   You can leave **Add Python to PATH unchecked** to avoid changing which
   Python your existing tools use. The `py` launcher is optional, and you do
   not need to uninstall, replace or modify your 64-bit Python/MSFS setup.
2. Verify the interpreter from **Command Prompt** using its full path (adjust
   the folder to match your installation):

   ```bat
   "%LocalAppData%\Programs\Python\Python313-32\python.exe" -c "import struct; print(struct.calcsize('P') * 8)"
   ```

   This must print `32`. If you have the `py` launcher, this also works:

   ```bat
   py -3-32 -c "import struct; print(struct.calcsize('P') * 8)"
   ```

   If `py` is not recognized, **that only means the launcher is unavailable**.
   Use the full path to your x86 `python.exe` in place of `py -3-32` in all
   commands below, or use the updated `start_fsx.bat`. A 64-bit Python cannot
   load the native 32-bit FSX DLL in the same process; a 64-bit virtual
   environment does not change that.
3. Install the **FSX SP2/Acceleration SimConnect redistributable** from your
   licensed FSX media/SDK. For Steam Edition, the usual installer is:

   ```text
   <Steam library>\steamapps\common\FSX\SDK\Core Utilities Kit\SimConnect SDK\LegacyInterfaces\FSX-XPACK\SimConnect.msi
   ```

   Run this MSI, rather than merely copying it. It installs the x86 assembly
   `Microsoft.FlightSimulator.SimConnect` version `10.0.61259.0`. Auto-detection
   searches `%WINDIR%\WinSxS` for that assembly. The included manifest activates
   it for DLL loading without changing `python.exe` or the system installation.
   Side-by-side assembly behavior is described here
   [1](https://www.fsdeveloper.com/forum/threads/simconnect-lib-file.435817/).
4. Keep the entire `fsx` folder together. No Microsoft DLLs or installers are
   redistributed here. Obtain them from your FSX installation, not third-party
   DLL download sites.

Original FSX RTM/SP1-only installations are not the intended target; update to
SP2/Acceleration or use Steam Edition. `--dll` permits an explicit native FSX
SDK DLL, but other runtime versions are not validated.

## 2. Connect your EFB

1. Run the bridge **on the same Windows PC as FSX**. Start FSX and load an active,
   unpaused flight. No remote SimConnect configuration is required for this
   setup; the UDP connection to the iPad is separate from SimConnect.
2. Put the PC and iPad on the same trusted LAN/Wi-Fi. Avoid guest networks,
   wireless client isolation and VPN routes between them.
3. On the iPad, open **Settings → Wi-Fi → your network's information button**
   and note its IPv4 address (for example, `192.168.1.42`).
4. Allow the EFB's **Local Network** permission in iPadOS. Open the EFB in the
   foreground and enable its external/GDL90 data source if required. In
   ForeFlight, inspect **More → Devices** for the GDL90 connection. FD Pro X
   menu names and accepted device types may differ by version/organization.
5. If Windows Firewall prompts, allow this Python interpreter on your **private
   network**. Permit outbound UDP to the selected iPad/port. Do not disable your
   firewall or expose this bridge to the Internet.
6. From the repository root, run:

   ```bat
   py -3-32 -m fsx.bridge --target 192.168.1.42
   ```

   Or double-click **`fsx\start_fsx.bat`** and enter the iPad address. The
   launcher checks for a compatible 32-bit Python before asking for the IP.
   It tries the optional `py` launcher, standard Python installation folders,
   and then `python.exe` on PATH. It rejects 64-bit interpreters.

   For a custom installation folder, set an explicit override in Command
   Prompt before starting it (do not put extra quotes inside the value):

   ```bat
   set "FSX_PYTHON=C:\MyPython32\python.exe"
   fsx\start_fsx.bat
   ```

   Without either launcher, you can run directly from the repository root:

   ```bat
   "%LocalAppData%\Programs\Python\Python313-32\python.exe" -m fsx.bridge --target 192.168.1.16
   ```

   Replace the Python path and iPad address as needed. Stop with **Ctrl+C**.
   The console should report `Receiving live FSX data` and show position,
   altitude, heading, pitch and roll.

Default UDP port is **4000**. Unicast is preferred: ForeFlight explicitly warns
that iOS can lose broadcast packets [1](https://www.foreflight.com/connect/spec/).
The target address is always used exactly as entered; it is never silently
replaced with `255.255.255.255`. There is no automatic ForeFlight discovery;
update `--target` if your iPad's DHCP address changes.

### Command examples

```bat
REM Only ForeFlight's documented AHRS extension
py -3-32 -m fsx.bridge --target 192.168.1.42 --ahrs foreflight

REM Preserve only the original script's 0x66 attitude extension
py -3-32 -m fsx.bridge --target 192.168.1.42 --ahrs legacy

REM Two EFB devices on the same UDP port
py -3-32 -m fsx.bridge --target 192.168.1.42 --target 192.168.1.43

REM Explicit native FSX SDK DLL (NOT Microsoft.FlightSimulator.SimConnect.dll)
py -3-32 -m fsx.bridge --target 192.168.1.42 --dll "C:\FSX SDK\SDK\Core Utilities Kit\SimConnect SDK\lib\SimConnect.dll"

REM Optional identity / rate / custom receiver port
py -3-32 -m fsx.bridge --target 192.168.1.42 --callsign N123AB --icao ABCDEF --rate 5 --port 4000

REM Optional broadcast: use your actual subnet's broadcast address
py -3-32 -m fsx.bridge --target 192.168.1.255 --broadcast

REM Full option list (also works without FSX, on any OS)
py -3-32 -m fsx.bridge --help
```

Do not guess the subnet broadcast address solely from a `.255` suffix; it
depends on your network mask. Unicast to the actual iPad address is simplest.
Direct invocation, `py -3-32 fsx\bridge.py --target ...`, is also supported.

## Telemetry and AHRS details

| Output | Source / behavior |
| --- | --- |
| Heartbeat `0x00` | 1 Hz, host UTC seconds, GPS-valid only while samples are fresh |
| Ownship `0x0A` | Latitude/longitude, **pressure** altitude, true **ground track**, ground speed, vertical speed, ground flag |
| Geometric altitude `0x0B` | FSX altitude in feet MSL; ForeFlight device capability bit 0 declares MSL; vertical figure of merit unavailable |
| ForeFlight ID `0x65/0x00` | 1 Hz; default name `FSX`, MSL capability |
| ForeFlight AHRS `0x65/0x01` | Big-endian tenths of degrees: roll, pitch, true **heading**, IAS and TAS |
| Legacy attitude `0x66` | Original script's little-endian float radians for pitch/bank, heading in degrees, validity bytes |

By default, **both attitude extensions** are sent (`--ahrs both`). This preserves
the original MSFS script's compatibility packet while adding ForeFlight's
published AHRS message. The legacy packet is **not** the documented ForeFlight
AHRS format, and no published FD Pro X-specific AHRS specification was used.
Its inclusion does not guarantee that every FD Pro X version will accept it.

- Default update rate is **5 Hz** for position/altitude/AHRS, independent of how
  many packets are emitted. ForeFlight specifies 5 Hz for AHRS
  [1](https://www.foreflight.com/connect/spec/).
- Native data is requested as one coherent FLOAT64 group every simulator frame.
  Units are explicitly `degrees`, `feet`, `knots`, and `feet per minute`;
  heading near north and high vertical speeds are not subject to unit guesses.
- FSX nose-down/left-bank positive values are converted to nose-up/right-bank
  positive for ForeFlight. The legacy packet retains the original script's
  left-positive bank convention. Check both bank directions during validation.
- Ownship track is not substituted for heading in AHRS. Zero ground speed stays
  zero, rather than falling back to true airspeed.
- Default participant address is `000000` for GPS telemetry without a supplied
  ICAO identity, per ForeFlight's extension specification
  [1](https://www.foreflight.com/connect/spec/).
- Before the first sample, and after **2 seconds without samples**, the bridge
  emits invalid-GPS heartbeats and **stops position/attitude output**. It resumes
  when fresh samples arrive. `--stale-after` adjusts this threshold. An EFB may
  retain its last displayed position briefly; the bridge does not fabricate
  zero coordinates or keep marking stale data valid.
- If no first sample arrives within 15 seconds, the program exits with an error
  including receive diagnostics. Use `--debug` for request and header logging.
  A simulator quit or SimConnect exception also stops it. Restart the bridge
  after restarting FSX; automatic reconnect is not implemented. Pausing FSX may
  suspend frame updates and trigger the stale-data behavior.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `'py' is not recognized` | The optional Python launcher is missing. Keep 64-bit Python, install x86 Python alongside it, then use the updated batch file or the full path to the x86 `python.exe`. No PATH change is necessary. |
| Batch file cannot find an existing x86 Python | Set `FSX_PYTHON` to its full executable path. The override must be Python 3.9+ and 32-bit; an invalid override produces an error rather than silently choosing something else. |
| `FSX requires 32-bit (x86) Python` / WinError 193 | Verify the chosen interpreter prints 32; do not load a 64-bit MSFS DLL or managed .NET DLL. |
| Runtime not found / WinError 126 or 14001 / side-by-side error | Install/repair the FSX-XPACK `SimConnect.msi` and its prerequisites. Keep `SimConnect.manifest` beside the source. A DLL alone may lack its native runtime dependencies. |
| `SimConnect_Open ... 0x80004005` | Start FSX, load a flight, verify the matching runtime. Remove unintended remote `SimConnect.cfg` settings from your launch directory. |
| `SimConnect opened` then a 15-second timeout on an unpaused flight | Replace the whole `fsx` folder with version 0.2.0 or newer to fix rejection of nonzero aircraft object IDs. If it persists, run `fsx\start_fsx.bat --debug` and share the entire log, including receive diagnostics. Do not reinstall Python solely because of this timeout. |
| No data / invalid GPS | Unpause/load a flight; inspect the console for a SimConnect exception. No valid packets are sent without a complete sample. |
| Console data correct, EFB disconnected | Verify iPad IP, UDP port, Local Network permission, private-network firewall rule, app foreground state, and Wi-Fi isolation/VPN settings. Prefer unicast. Stop other bridges/receivers competing for the EFB data source. |
| Position works, attitude missing/reversed | Try `--ahrs foreflight` for ForeFlight or `--ahrs legacy` for the original compatibility behavior. Verify app feature entitlement/settings and compare left/right bank with the FSX instruments. Record which mode/app version fails. |
| iPad IP changes | Update `--target`, or reserve its address in your router's DHCP settings. |

## Validation before relying on the simulator display

On Windows with your real FSX/EFB setup:

- [ ] Confirm connection and aircraft position at a known airport.
- [ ] Taxi and stop: ground speed should return to zero.
- [ ] Climb/descend: compare MSL altitude and vertical-speed sign/magnitude.
- [ ] Test nose up/down and left/right banks; the EFB horizon must agree with FSX.
- [ ] Turn through north; verify heading wraps normally.
- [ ] With crosswind, verify map track differs from AHRS heading as expected.
- [ ] Pause/stop FSX: stale-data suppression should appear in the console; then
      unpause to verify recovery. Closing FSX should stop the bridge.
- [ ] Repeat on FD Pro X and ForeFlight, recording app versions and AHRS mode.

## Offline developer tests

From the repository root (any OS/Python 3.9+; core tests need no external packages,
Tk interaction tests skip if Tkinter or a display is unavailable):

```sh
python -m unittest discover -s fsx/tests -v
python -m compileall -q fsx
python -m fsx.bridge --help
```

Tests cover GDL90 CRC/escaping, packet fields and attitude conventions,
SimConnect binary dispatch layout, malformed data and failures, stale-data
handling, 5 Hz scheduling, cleanup, CLI validation, and local UDP transmission.
Desktop tests cover subnet masks, active adapters, multiple interfaces,
source-address binding, cooperative worker shutdown and packaging arguments.
When Tk/display are available, they also exercise Auto/manual controls,
queued live data and UI recovery after a connection failure.
They do not execute the Windows batch launcher or replace live validation of
Windows DLL loading or the EFB display. On Windows, also check launcher startup
with (1) no `py` command and a standard x86 installation, (2) only 64-bit Python,
(3) a custom `FSX_PYTHON` path containing spaces, and (4) an invalid or 64-bit
`FSX_PYTHON` override. Cases 2 and 4 must stop with setup instructions.

Packet encoders are adapted from Daniel Aregay's original script in this
repository. Additional references:

- Garmin GDL90 Data Interface Specification, Rev A (CRC, framing and standard
  messages): [1](https://www.faa.gov/sites/faa.gov/files/air_traffic/technology/adsb/archival/GDL90_Public_ICD_RevA.PDF).
- ForeFlight extended specification (device ID, AHRS, connectivity):
  [1](https://www.foreflight.com/connect/spec/).
- Legacy SimConnect API reference (data definitions and dispatch structures):
  [3](https://www.prepar3d.com/SDK/Core%20Utilities%20Kit/SimConnect%20SDK/SimConnect.htm).
