@echo off
setlocal
cd /d "%~dp0.."
set "RESULT=1"
echo FSX EFB Connect - simulation use only
echo Start FSX and load a flight before continuing.
echo.
call "%~dp0find_python32.bat"
if errorlevel 1 goto finished
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
