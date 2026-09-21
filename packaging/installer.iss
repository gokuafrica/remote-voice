; Remote Voice (rework) - Inno Setup 6 installer script
;
; Expects a staging directory prepared by packaging\build_installer.ps1:
;   app\                    the packaged Electron app (Remote Voice.exe ...)
;   app\resources\python311\   embedded Python 3.11 + all pip deps + CUDA wheels
;   app\resources\engine\      engine\engine.py (sidecar)
;   app\resources\hf_cache\    Parakeet TDT 0.6b v2 ONNX weights (~2.4 GB)
;
; Build with:
;   ISCC.exe /DSTAGEDIR="<abs stage>" /DOUTPUTDIR="<abs out>" packaging\installer.iss
;
; Architecture notes vs the old (master) installer:
;   - NO firewall rules, NO port (the engine is a stdio sidecar of the app).
;   - Autostart is a per-user Windows login item controlled from Settings.
;   - Per-user writable state (config at %APPDATA%\Remote Voice, HF model
;     cache seeded to %LOCALAPPDATA%\Remote Voice\model-cache) is handled by
;     the app itself at runtime; nothing user-specific is written to {app}.

#ifndef STAGEDIR
#define STAGEDIR "stage"
#endif

#ifndef OUTPUTDIR
#define OUTPUTDIR "..\distributable"
#endif

[Setup]
AppId={{7C4E9A31-2B5D-4F08-9A6E-1D3C5E7B9042}
AppName=Remote Voice
AppVersion=0.2.0
AppPublisher=Remote Voice
; Per-user install: this matches the app's per-user settings and autostart
; model and avoids requiring elevation for normal installation.
DefaultDirName={localappdata}\Programs\Remote Voice
DefaultGroupName=Remote Voice
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OUTPUTDIR}
OutputBaseFilename=RemoteVoiceSetup
; Use a smaller LZMA2 dictionary so the multi-gigabyte payload stays within
; Windows' single-file limit while avoiding the encoder failure from the
; maximum dictionary size.
Compression=lzma2/fast
SolidCompression=yes
DiskSpanning=yes
DiskSliceSize=2000000000
WizardStyle=modern
; Do not leave an installer log in the user's temporary directory.
SetupLogging=no
; The app runs at logon via scheduled task; no need to auto-run after setup.
CloseApplications=no

[Files]
; The packaged Electron app, including resources\python311, resources\engine,
; resources\hf_cache (staged by build_installer.ps1). Exclude pip/wheel
; caches and bytecode from the embedded python tree.
Source: "{#STAGEDIR}\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "python311\pip\cache\*,*.pyc,__pycache__"
; Uninstall cleanup helper. It runs before Inno removes {app}.
Source: "cleanup_uninstall.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Remote Voice"; Filename: "{app}\Remote Voice.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall Remote Voice"; Filename: "{uninstallexe}"

[Run]
; Release VC++ runtime DLLs are bundled beside the embedded Python runtime.
; Optional launch after install.
Filename: "{app}\Remote Voice.exe"; Description: "Launch Remote Voice"; Flags: postinstall skipifsilent nowait unchecked

[UninstallRun]
; Stop the app and delete the installing user's settings, history, Electron
; state, model cache, and temporary recordings.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\cleanup_uninstall.ps1"" -InstallDir ""{app}"""; Flags: runhidden logoutput; RunOnceId: "CleanupUserData"
; Remove the legacy scheduled task if an older Remote Voice install created it.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /F /TN RemoteVoiceAutostart"; Flags: runhidden; RunOnceId: "DelTask"

[UninstallDelete]
; Remove files created under the installation directory after installation.
Type: filesandordirs; Name: "{app}"
