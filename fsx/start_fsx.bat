@echo off
setlocal
cd /d "%~dp0.."
echo FSX EFB Connect - simulation use only
echo Start FSX and load a flight before continuing.
echo.
set /p "IPAD_IP=Enter the iPad IPv4 address (example 192.168.1.42): "
if not defined IPAD_IP exit /b 1
py -3-32 -m fsx.bridge --target "%IPAD_IP%" %*
echo.
echo If Python was not found, install 32-bit Python with the Python launcher.
echo See fsx\README.md for setup and troubleshooting.
pause
