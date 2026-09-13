; Remote Voice - Inno Setup 6 installer script
;
; Expects a staging directory prepared by packaging\build_installer.ps1 containing:
;   python311\            (embedded Python 3.11 with all deps pre-installed)
;   ffmpeg\ffmpeg.exe
;   vc_redist.x64.exe     (Microsoft-signed Visual C++ runtime prerequisite)
;   app_paths.py, server.py, gui.py, tray.py
;   Remote Voice.bat, Remote Voice Tray.bat, start.bat
;   defaults\config.json, defaults\tray_config.json (fresh templates)
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
; Per-machine install: immutable application files live in Program Files.
; Runtime code seeds writable per-user configs in %LOCALAPPDATA%\Remote Voice.
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
Source: "{#STAGEDIR}\app_paths.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\Remote Voice.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\Remote Voice Tray.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\start.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#STAGEDIR}\handy-prompt.txt"; DestDir: "{app}"; Flags: ignoreversion
; Fresh templates only. The app copies these to the current user's writable
; local app-data directory on first run. Uninstall therefore preserves settings.
Source: "{#STAGEDIR}\defaults\config.json"; DestDir: "{app}\defaults"; Flags: ignoreversion
Source: "{#STAGEDIR}\defaults\tray_config.json"; DestDir: "{app}\defaults"; Flags: ignoreversion
; Elevated helper used only during installation.
Source: "register_task.ps1"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "{#STAGEDIR}\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\Remote Voice"; Filename: "{app}\Remote Voice.bat"; WorkingDir: "{app}"
Name: "{group}\Remote Voice Tray"; Filename: "{app}\Remote Voice Tray.bat"; WorkingDir: "{app}"
Name: "{group}\Uninstall Remote Voice"; Filename: "{uninstallexe}"

[Run]
; ONNX Runtime requires the Microsoft Visual C++ runtime on clean Windows.
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; Flags: runhidden; StatusMsg: "Installing Microsoft Visual C++ runtime..."
; Idempotent firewall rule: delete then re-add so reinstalls don't duplicate.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Remote Voice Server"""; Flags: runhidden; StatusMsg: "Removing old firewall rule..."
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Remote Voice Server"" dir=in action=allow protocol=TCP localport=8787"; Flags: runhidden; StatusMsg: "Adding firewall rule (TCP 8787)..."
; Register from the elevated installer, but use the built-in Users group as the
; task principal. Task Scheduler then runs it with the signed-in user's limited
; token, even when a standard user supplied separate administrator credentials.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{tmp}\register_task.ps1"" -AppDir ""{app}"""; Flags: runhidden logoutput; StatusMsg: "Creating RemoteVoiceServer scheduled task..."
; Optional launch after install (unchecked by default).
Filename: "{app}\Remote Voice.bat"; Description: "Launch Remote Voice"; Flags: postinstall skipifsilent nowait unchecked

[UninstallRun]
; Order matters: Inno runs these top to bottom.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /F /TN RemoteVoiceServer"; Flags: runhidden; RunOnceId: "DelTask"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Remote Voice Server"""; Flags: runhidden; RunOnceId: "DelFirewall"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  { [Run] programs do not make Setup fail on a non-zero exit code. Verify the
    essential task explicitly so a silent deployment can never report a false
    success again. }
  if CurStep = ssPostInstall then
  begin
    if (not Exec(ExpandConstant('{app}\python311\python.exe'),
      '-c "import onnxruntime, app_paths"', ExpandConstant('{app}'), SW_HIDE,
      ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
    begin
      RaiseException('Remote Voice could not load its bundled Python runtime. ' +
        'Setup has not completed successfully.');
    end;
    if (not Exec(ExpandConstant('{sys}\schtasks.exe'),
      '/Query /TN "RemoteVoiceServer"', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
    begin
      RaiseException('Remote Voice could not create its scheduled task. ' +
        'Setup has not completed successfully.');
    end;
  end;
end;
