@echo off
setlocal
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt
if errorlevel 1 goto :error
py -3 build_exe.py
if errorlevel 1 goto :error
pause
exit /b 0

:error
echo.
echo Build fehlgeschlagen.
pause
exit /b 1
