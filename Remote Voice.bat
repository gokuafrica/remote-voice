@echo off
set "PYTHONW=pythonw"
if exist "%~dp0python311\pythonw.exe" set "PYTHONW=%~dp0python311\pythonw.exe"
start "" "%PYTHONW%" "%~dp0gui.py"
