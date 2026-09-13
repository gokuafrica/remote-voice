; Remote Voice (rework) - Inno Setup 6 installer script
;
; Expects a staging directory prepared by packaging\build_installer.ps1:
;   app\                    the packaged Electron app (Remote Voice.exe ...)
;   app\resources\python311\   embedded Python 3.11 + all pip deps + CUDA wheels
;   app\resources\engine\      engine\engine.py (sidecar)
;   app\resources\hf_cache\    Parakeet TDT 0.6b v2 ONNX weights (~2.4 GB)
;   vc_redist.x64.exe       Microsoft-signed Visual C++ runtime prerequisite
;
; Build with:
;   ISCC.exe /DSTAGEDIR="<abs stage>" /DOUTPUTDIR="<abs out>" packaging\installer.iss
;
; Architecture notes vs the old (master) installer:
;   - NO firewall rules, NO port (the engine is a stdio sidecar of the app).
;   - Autostart is a logon scheduled task that launches "Remote Voice.exe"
;     with the Users-group principal (signed-in user's token).
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
AppVersion=0.1.0
AppPublisher=Remote Voice
; Per-machine install: immutable application files live in Program Files.
DefaultDirName={autopf}\Remote Voice
DefaultGroupName=Remote Voice
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OUTPUTDIR}
OutputBaseFilename=RemoteVoiceSetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
; The app runs at logon via scheduled task; no need to auto-run after setup.
CloseApplications=no

[Files]
; The packaged Electron app, including resources\python311, resources\engine,
; resources\hf_cache (staged by build_installer.ps1). Exclude pip/wheel
; caches and bytecode from the embedded python tree.
Source: "{#STAGEDIR}\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "python311\pip\cache\*,*.pyc,__pycache__"
; Elevated helper used only during installation.
Source: "register_task.ps1"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "{#STAGEDIR}\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\Remote Voice"; Filename: "{app}\Remote Voice.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall Remote Voice"; Filename: "{uninstallexe}"

[Run]
; ONNX Runtime requires the Microsoft Visual C++ runtime on clean Windows.
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; Flags: runhidden; StatusMsg: "Installing Microsoft Visual C++ runtime..."
; Register from the elevated installer, but use the built-in Users group as
; the task principal. Task Scheduler then runs it with the signed-in user's
; token, even when a standard user supplied separate admin credentials.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{tmp}\register_task.ps1"" -AppDir ""{app}"""; Flags: runhidden logoutput; StatusMsg: "Creating Remote Voice autostart task..."
; Optional launch after install.
Filename: "{app}\Remote Voice.exe"; Description: "Launch Remote Voice"; Flags: postinstall skipifsilent nowait unchecked

[UninstallRun]
; Remove the autostart task. Per-user data (%APPDATA%\Remote Voice config,
; %LOCALAPPDATA%\Remote Voice model cache + history) is deliberately kept.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /F /TN RemoteVoiceAutostart"; Flags: runhidden; RunOnceId: "DelTask"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  { [Run] programs do not make Setup fail on a non-zero exit code. Verify the
    essential things explicitly so a silent deployment can never report a
    false success. }
  if CurStep = ssPostInstall then
  begin
    { 1. The packaged exe must exist. }
    if not FileExists(ExpandConstant('{app}\Remote Voice.exe')) then
      RaiseException('Remote Voice.exe is missing from the install directory. ' +
        'Setup has not completed successfully.');

    { 2. The bundled Python runtime must be able to import onnxruntime (this
       exercises the CUDA wheel DLL layout too). }
    if (not Exec(ExpandConstant('{app}\resources\python311\python.exe'),
      '-c "import onnxruntime, onnx_asr; print(onnxruntime.get_available_providers())"',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode)) or
      (ResultCode <> 0) then
      RaiseException('Remote Voice could not load its bundled Python runtime ' +
        '(onnxruntime import failed). Setup has not completed successfully.');

    { 3. The model weights must have been installed. }
    if not DirExists(ExpandConstant('{app}\resources\hf_cache\hub')) then
      RaiseException('The bundled speech model is missing from the install ' +
        'directory. Setup has not completed successfully.');

    { 4. The autostart task must have been registered. }
    if (not Exec(ExpandConstant('{sys}\schtasks.exe'),
      '/Query /TN "RemoteVoiceAutostart"', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
      RaiseException('Remote Voice could not create its autostart task. ' +
        'Setup has not completed successfully.');
  end;
end;
