@echo off
echo Starting Remote Voice Server...
echo Listening on http://0.0.0.0:8787
echo.
set "PYTHON=python"
if exist "%~dp0python311\python.exe" set "PYTHON=%~dp0python311\python.exe"
"%PYTHON%" "%~dp0server.py"
pause
