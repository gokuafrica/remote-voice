@echo off
rem Remote Voice launcher - delegates to the .vbs helper so no console window
rem stays open and the app starts fully detached.
wscript.exe "%~dp0remote-voice-launch.vbs"
