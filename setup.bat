@echo off
:: Run this once as Administrator to set up firewall and auto-start

echo === Remote Voice Server Setup ===
echo.

:: Check admin privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Please run this script as Administrator.
    echo Right-click and select "Run as administrator".
    pause
    exit /b 1
)

:: 1. Firewall rule
echo [1/2] Creating firewall rule for port 8787...
netsh advfirewall firewall delete rule name="Remote Voice Server" >nul 2>&1
netsh advfirewall firewall add rule name="Remote Voice Server" dir=in action=allow protocol=TCP localport=8787 profile=any
echo Done.
echo.

:: 2. Scheduled task for auto-start at login
echo [2/2] Creating scheduled task for auto-start...
set SCRIPT_DIR=%~dp0
schtasks /delete /tn "RemoteVoiceServer" /f >nul 2>&1
schtasks /create /tn "RemoteVoiceServer" /tr "pythonw \"%SCRIPT_DIR%server.py\"" /sc onlogon /rl highest /f
echo Done.
echo.

echo === Setup complete! ===
echo - Firewall: port 8787 open for all profiles
echo - Auto-start: server will start at login
echo.
echo To start the server now, run: start.bat
pause
