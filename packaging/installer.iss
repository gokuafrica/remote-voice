; Remote Voice - Inno Setup 6 installer script
;
; Expects a staging directory prepared by packaging\build_installer.ps1 containing:
;   python311\            (embedded Python 3.11 with all deps pre-installed)
;   ffmpeg\ffmpeg.exe
;   server.py, gui.py, tray.py
;   Remote Voice.bat, Remote Voice Tray.bat, start.bat
;   config.json, tray_config.json   (FRESH default configs)
;   handy-prompt.txt
;
; Build with:
;   ISCC.exe /DSTAGEDIR="<abs path to stage>" /DOUTPUTDIR="<abs path to distributable>" packaging\installer.iss
;
; NOTE: with lzma2/max + solid compression the resulting installer is still
; ~1 GB (PyTorch-free but onnxruntime-gpu + CUDA wheels + Parakeet model deps
; dominate). This is expected.

#ifndef STAGEDIR
#define STAGEDIR "..\stage"
#endif

#ifndef OUTPUTDIR
#define OUTPUTDIR "..\distributable"
#endif

[Setup]
AppId={{8A6C1E52-9F0B-4A17-9E5D-3F2B7C4D1A88}
AppName=Remote Voice
AppVersion=1.0
AppPublisher=Remote Voice
DefaultDirName={autopf}\Remote Voice
DefaultGroupName=Remote Voice
; Per-machine install: firewall + scheduled task need admin.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OUTPUTDIR}
OutputBaseFilename=RemoteVoiceSetup
Compression=lzma2/max
SolidCompression=yes
; No license file, so no license page; keep the wizard minimal.
WizardStyle=modern
SetupLogging=yes

[Files]
; Python 3.11 embedded tree with all dependencies pre-installed.
; Excludes the pip cache if present inside the python311 tree.
Source: "{#STAGEDIR}\python311\*"; DestDir: "{app}\python311"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "pip\cache\*,*.pyc,__pycache__"
; Bundled ffmpeg (server source looks for ffmpeg\ffmpeg.exe next to the scripts first).
Source: "{#STAGEDIR}\ffmpeg\ffmpeg.exe"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion
; Application scripts.
Source: "{#STAGEDIR}\server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\gui.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\tray.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\Remote Voice.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\Remote Voice Tray.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\start.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\handy-prompt.txt"; DestDir: "{app}"; Flags: ignoreversion
; FRESH default configs (not the developer's personal ones).
; NOTE: uninstall removes ALL files under {app}, including config.json and
; tray_config.json (user settings are not preserved). This is acceptable for
; this app; if preservation is ever wanted, move configs to {userappdata}
; or use [UninstallDelete] exclusions.
Source: "{#STAGEDIR}\config.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\tray_config.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Remote Voice"; Filename: "{app}\Remote Voice.bat"; WorkingDir: "{app}"
Name: "{group}\Remote Voice Tray"; Filename: "{app}\Remote Voice Tray.bat"; WorkingDir: "{app}"
Name: "{group}\Uninstall Remote Voice"; Filename: "{uninstallexe}"

[Run]
; Idempotent firewall rule: delete then re-add so reinstalls don't duplicate.
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Remote Voice Server"""; Flags: runhidden; StatusMsg: "Removing old firewall rule..."
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Remote Voice Server"" dir=in action=allow protocol=TCP localport=8787"; Flags: runhidden; StatusMsg: "Adding firewall rule (TCP 8787)..."
; Scheduled task: start server at logon with highest privileges.
Filename: "schtasks"; Parameters: "/Create /F /TN RemoteVoiceServer /SC ONLOGON /RL HIGHEST /TR """"{app}\python311\pythonw.exe"" ""{app}\server.py"""""; Flags: runhidden; StatusMsg: "Creating RemoteVoiceServer scheduled task..."
; Optional launch after install (unchecked by default).
Filename: "{app}\Remote Voice.bat"; Description: "Launch Remote Voice"; Flags: postinstall skipifsilent nowait unchecked

[UninstallRun]
; Order matters: Inno runs these top to bottom.
Filename: "schtasks"; Parameters: "/Delete /F /TN RemoteVoiceServer"; Flags: runhidden; RunOnceId: "DelTask"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Remote Voice Server"""; Flags: runhidden; RunOnceId: "DelFirewall"
