@echo off
setlocal
cd /d "%~dp0"

echo Building IG-Media-Downloader.exe...
py -m pip install --upgrade pip
if errorlevel 1 goto :fail
py -m pip install --upgrade --force-reinstall -r requirements-build.txt
if errorlevel 1 goto :fail

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

py -m PyInstaller --noconfirm --clean --onefile --windowed --name "IG-Media-Downloader" --collect-all gallery_dl --collect-all customtkinter --collect-all playwright app.py
if errorlevel 1 goto :fail

explorer "%CD%\dist"
pause
exit /b 0

:fail
echo Build failed.
pause
exit /b 1
