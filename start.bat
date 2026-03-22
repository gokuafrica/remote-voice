@echo off
echo Starting Remote Voice Server...
echo Listening on http://0.0.0.0:8787
echo.
python "%~dp0server.py"
pause
