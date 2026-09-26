@echo off
setlocal
cd /d "%~dp0.."
set "RESULT=1"
echo Build FSX EFB Connect - standalone Windows desktop EXE
echo This keeps your existing Python installations unchanged.
echo Internet access is needed to download build dependencies.
echo.
call "%~dp0find_python32.bat"
if errorlevel 1 goto finished
"%PYTHON_EXE%" %PYTHON_SWITCHES% "%~dp0build_exe.py"
set "RESULT=%ERRORLEVEL%"
:finished
echo.
pause
exit /b %RESULT%
