@echo off
setlocal
cd /d "%~dp0.."
set "PYTHON_EXE="
set "PYTHON_SWITCHES="
set "RESULT=1"
echo FSX EFB Connect - simulation use only
echo Start FSX and load a flight before continuing.
echo.

REM An explicit override takes priority. Never silently use a different Python.
if defined FSX_PYTHON call :check_python "%FSX_PYTHON%"
if defined PYTHON_EXE goto ready
if defined FSX_PYTHON goto bad_override

REM The launcher is optional: check it quietly, then try installed executables.
py -3-32 -c "import struct,sys; sys.exit(0 if struct.calcsize('P') == 4 and sys.version_info >= (3,9) else 1)" >nul 2>&1
if errorlevel 1 goto find_executable
set "PYTHON_EXE=py"
set "PYTHON_SWITCHES=-3-32"
goto ready

:find_executable
REM Standard python.org per-user and all-users installation directories.
for /d %%D in ("%LocalAppData%\Programs\Python\Python*-32") do call :check_python "%%~fD\python.exe"
for /d %%D in ("%ProgramFiles(x86)%\Python*") do call :check_python "%%~fD\python.exe"
for /d %%D in ("%SystemDrive%\Python*") do call :check_python "%%~fD\python.exe"
if defined PYTHON_EXE goto ready
REM Also accept an x86 interpreter on PATH; reject 64-bit interpreters.
for /f "delims=" %%P in ('where python.exe 2^>nul') do call :check_python "%%P"
if defined PYTHON_EXE goto ready

echo No compatible 32-bit Python 3.9 or newer was found.
echo KEEP your 64-bit Python. Install 32-bit Python alongside it.
echo Use the Windows 32-bit installer from python.org in a separate folder.
echo You do not need to change PATH or install the py launcher.
echo.
echo If x86 Python is already in a custom folder, run in Command Prompt:
echo   set "FSX_PYTHON=C:\path\to\32-bit\python.exe"
echo   fsx\start_fsx.bat
goto finished

:bad_override
echo FSX_PYTHON is not a working 32-bit Python 3.9 or newer:
echo   "%FSX_PYTHON%"
echo Set it to the full path of your x86 python.exe, without embedded quotes.
echo Or run: set "FSX_PYTHON="
goto finished

:ready
echo Using: "%PYTHON_EXE%" %PYTHON_SWITCHES%
echo.
set "IPAD_IP="
set /p "IPAD_IP=Enter the iPad IPv4 address (example 192.168.1.42): "
if not defined IPAD_IP goto finished
"%PYTHON_EXE%" %PYTHON_SWITCHES% -m fsx.bridge --target "%IPAD_IP%" %*
set "RESULT=%ERRORLEVEL%"

:finished
echo.
echo See fsx\README.md for setup and troubleshooting.
pause
exit /b %RESULT%

:check_python
if defined PYTHON_EXE exit /b 0
if not exist "%~1" exit /b 1
"%~1" -c "import struct,sys; sys.exit(0 if struct.calcsize('P') == 4 and sys.version_info >= (3,9) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "PYTHON_EXE=%~1"
exit /b 0
