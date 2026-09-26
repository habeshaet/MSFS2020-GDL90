#!/usr/bin/env python3
"""Build the standalone windowed FSX EXE on Windows using 32-bit Python.

The isolated build environment never modifies the user's existing Python
packages. Microsoft SimConnect remains an installed runtime, not a bundled DLL.
"""

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import venv


def pyinstaller_command(python, folder):
    folder = Path(folder).resolve()
    return [
        str(python), "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--name", "FSX - EFB Connect",
        "--distpath", str(folder / "dist"),
        "--workpath", str(folder / "build" / "work"),
        "--specpath", str(folder / "build"),
        "--paths", str(folder.parent),
        "--add-data", f"{folder / 'SimConnect.manifest'}{os.pathsep}fsx",
        str(folder / "gui.py"),
    ]


def main():
    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 4:
        print("Build on Windows with 32-bit Python 3.9+ (keep your 64-bit Python installed).")
        return 1
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is required.")
        return 1
    try:
        import tkinter  # noqa: F401 - fail early if the Python install omits Tk.
    except ImportError:
        print("Modify your x86 Python installation and enable Tcl/Tk and IDLE, then retry.")
        return 1
    folder = Path(__file__).resolve().parent
    environment = folder / ".venv" / "build-exe"
    python = environment / "Scripts" / "python.exe"
    try:
        print(f"Building with {sys.executable} (32-bit).", flush=True)
        if not python.exists():
            print("Creating an isolated build environment…", flush=True)
            venv.EnvBuilder(with_pip=True).create(environment)
        # Reject a pre-existing environment built using an incompatible interpreter.
        subprocess.run([str(python), "-c",
                        "import struct,sys; sys.exit(0 if struct.calcsize('P') == 4 "
                        "and sys.version_info >= (3,9) else 1)"], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r",
                        str(folder / "requirements-build.txt")], check=True)
        subprocess.run(pyinstaller_command(python, folder), check=True, cwd=folder.parent)
        output = folder / "dist" / "FSX - EFB Connect.exe"
        if not output.is_file():
            raise OSError("PyInstaller did not produce the expected EXE.")
        print(f"\nSUCCESS: {output}\nDouble-click that EXE to open the desktop interface.")
        print("No Python or batch launcher is needed to RUN it; the FSX SimConnect runtime is still required.")
        return 0
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"\nBuild failed: {exc}")
        print("Read the error above. Check Internet access for pip and the x86 Python installation.")
        print("If an old build environment is incompatible, remove fsx\\.venv\\build-exe and retry.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
