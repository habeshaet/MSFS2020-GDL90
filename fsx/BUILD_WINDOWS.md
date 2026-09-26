# Build FSX EFB Connect as a Windows EXE

## 0.3.1 fix: psutil compilation / missing `basetsd.h`

If your earlier build downloaded `psutil-7.2.2.tar.gz` and failed while running
`cl.exe` with a missing `basetsd.h`, pip had fallen back to **compiling psutil
from source**. Python was detected correctly; do not reinstall Python or repair
Visual Studio just to build this app.

The updated requirements pin **`psutil==7.1.1`**, whose published
`psutil-7.1.1-cp37-abi3-win32.whl` was verified downloadable for Windows x86
Python 3.11 and 3.13. The builder now installs **only prebuilt wheels**, so a
missing wheel produces a clear pip error instead of invoking a C++ compiler.

For an existing download, the quick fix is to replace `fsx/requirements-gui.txt`
with these contents and rerun **`build_exe.bat`**:

```text
--only-binary=psutil
psutil==7.1.1
```

Or use the complete updated 0.3.1 source package, which also enforces wheel-only
installation for all build dependencies. **You can reuse the failed build's
`fsx/.venv/build-exe` folder.** No deletion or manual pip upgrade is needed for
this particular error. Look for a `psutil-7.1.1-...-win32.whl` download, not a
`.tar.gz` archive. This verifies package availability, not execution of the
Windows EXE in the Linux development environment.

## Easiest method — build once, then just double-click the EXE

Your working **32-bit Python** and **FSX SimConnect runtime** can stay installed.
Do not uninstall or modify your 64-bit Python.

1. Download the updated project/source ZIP and **extract it completely** to a
   writable folder. Keep all files in the `fsx` folder together.
2. Open `fsx` and double-click **`build_exe.bat`**.
3. Wait for `SUCCESS`. The first build needs Internet access to install
   PyInstaller and psutil in an isolated build environment.
4. Your app is:

   ```text
   fsx\dist\FSX - EFB Connect.exe
   ```

5. Double-click that EXE whenever you want to connect. **No batch file or Python
   installation is needed to run the built EXE.** You can create a desktop
   shortcut or copy the EXE elsewhere. The compatible FSX SimConnect runtime
   must still be installed on the computer running it.

This builds a **32-bit, single-file, windowed app**: the Python runtime, Tk
interface, psutil, and the SimConnect activation manifest are bundled. It does
**not** bundle a Microsoft DLL or installer. It does not display a console
window during ordinary use; connection errors appear in the GUI activity log.

### If the builder cannot find 32-bit Python

Open **Command Prompt** in the extracted project's root folder and run:

```bat
set "FSX_PYTHON=C:\path\to\your\32-bit\python.exe"
fsx\build_exe.bat
```

Replace that path with the actual executable. Alternatively, run the Python
builder directly with the full path (example for Python 3.13):

```bat
"%LocalAppData%\Programs\Python\Python313-32\python.exe" fsx\build_exe.py
```

If the Python launcher is installed, this is equivalent:

```bat
py -3-32 fsx\build_exe.py
```

Do not build with your 64-bit interpreter: that would produce an EXE unable to
load the native FSX DLL. The builder checks architecture before doing any work.
Python must include the **Tcl/Tk and IDLE** installation component.

## Using the desktop interface

1. Start FSX and load a flight. If switching windows pauses FSX, disable FSX's
   **Pause on task switch** setting.
2. Start **FSX - EFB Connect.exe**. It opens the dark interface adapted from the
   original MSFS connector: sidebar status, live-data badges, connection fields,
   Start/Stop button and activity log.
3. Leave **Auto-detect subnet broadcast** enabled. The app lists active IPv4
   adapters and initially prefers the adapter used by the default IPv4 route.
4. Check the selected adapter is on the **same LAN as the iPad**. If you have a
   VPN, virtual adapters, or multiple network connections, choose your actual
   Wi-Fi/Ethernet adapter from the list.
5. For `192.168.1.13` with mask `255.255.255.0`, the target automatically becomes
   **`192.168.1.255`**. The socket is bound to the selected local IP so broadcasts
   use that source address. The destination uses the actual subnet mask; on a
   `/16` network it would be `192.168.255.255`, not an assumed `/24` address.
6. Click **Start**. The live values and status update as FSX samples arrive.
   Click **Stop** before changing settings. Closing an active app asks to stop
   and waits for the worker to release SimConnect and its UDP socket.

Detection runs on launch and when you click **Refresh**. It does not silently
change a running session if the PC changes Wi-Fi networks. Stop, reconnect,
Refresh and Start again after a network change. Settings reset to automatic
mode on a new launch; a previous subnet's target is not saved as a default.

### Direct iPad connection is still available

Some iOS/network combinations receive unicast more reliably than broadcast.
If the iPad is not receiving:

- Turn off **Auto-detect** and **Broadcast**.
- Enter the iPad IP (for example `192.168.1.16`).
- Keep port `4000`, choose the correct local adapter, and click Start.

Make sure iPadOS Local Network permission is enabled and both devices are on
the same non-isolated LAN. Windows Firewall may require a **new private-network
permission for the EXE**, even if Python was already permitted. Do not disable
firewall/antivirus protection to run the app.

## Build details and troubleshooting

- `build_exe.py` creates `fsx/.venv/build-exe` and installs only the listed
  `requirements-build.txt` dependencies there. Your existing Python packages
  are not modified.
- PyInstaller's temporary files/spec go in `fsx/build`; the final EXE goes in
  `fsx/dist`. All build outputs and environments are ignored by Git.
- The initial build needs network access for pip. Subsequent builds can reuse
  installed dependencies. Dependencies are installed with `--only-binary=:all:`;
  no local C/C++ compiler is needed. If pip reports no matching distribution,
  check the pinned requirements, Python version and access to an index/mirror
  containing Windows x86 wheels. Do not remove the wheel-only protection as a
  workaround. Other pip failures may indicate proxy/network issues.
- If an old build environment is incompatible, delete only
  `fsx/.venv/build-exe` and rebuild.
- This is an **unsigned** local build. Windows may show a publisher/reputation
  warning. Review the source and only run builds you trust; the build process
  does not add a code-signing certificate.
- Use **Copy Log** in the GUI when reporting a connection problem.
- If the EXE cannot launch, run the GUI from source in Command Prompt to expose
  startup errors:

  ```bat
  py -3-32 -m pip install -r fsx\requirements-gui.txt
  py -3-32 -m fsx.gui
  ```

  Use the full x86 interpreter path instead of `py -3-32` if needed.
- A Windows EXE must be built **on Windows**. The Linux development sandbox
  cannot cross-build it using PyInstaller. The source/build scripts are supplied;
  they are not a precompiled Windows binary.

## Windows acceptance checks

- [ ] Build using x86 Python and confirm the output EXE opens without a console.
- [ ] Check `192.168.1.13/24 → 192.168.1.255` (or your actual adapter/subnet).
- [ ] Try another adapter, manual/unicast mode and Refresh after reconnecting Wi-Fi.
- [ ] Start, Stop and Start again; verify position/AHRS on the iPad and live badges.
- [ ] Stop or close FSX; ensure stale/error states do not continue valid telemetry.
- [ ] Close the GUI while connected and confirm it exits after cleanup.
- [ ] Move just the EXE to another folder and confirm the bundled manifest is found
      (the installed FSX runtime is still required).

For protocol details and FSX runtime setup, see [README.md](README.md).
