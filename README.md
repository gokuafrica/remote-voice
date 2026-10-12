# Remote Voice

Remote Voice is a Windows desktop voice-dictation application. The installed
product is an Electron shell with a Python transcription sidecar, packaged by
the installer sources in `packaging/`.

## Source layout

```text
app/                         Electron main, preload, renderer, and tests
config.json                  Seed configuration for development and packaging
engine/                      Python sidecar, requirements, tests, and test data
packaging/                   Electron packaging and Inno Setup sources
  build_installer.ps1
  installer.iss
  package_app.js
  cleanup_uninstall.ps1
```

The root Python server, GUI, tray clients, and their dependency files are not
part of this application. The only Python runtime source is `engine/engine.py`,
which the Electron app starts as a stdio JSON sidecar.

## Development

Install the locked Electron dependencies from the repository root:

```powershell
npm.cmd ci --prefix app
```

Run the Electron shell directly when working on the source:

```powershell
npm.cmd --prefix app run start
```

The app finds `engine/engine.py` and the seed `config.json` from the repository
in development. Packaged runs use the copies placed in the Electron resources
directory.

## Packaging

The optional Electron package is written to the ignored `dist/` directory:

```powershell
npm.cmd --prefix app run package
```

The complete Windows installer is built with:

```powershell
pwsh packaging/build_installer.ps1
```

The installer build assembles the Electron package, embedded Python runtime,
Python dependencies from `engine/requirements.txt`, the speech model cache,
and the uninstall helper. Its local cache and staging data live under the
ignored `packaging/cache/` and `packaging/stage/` directories. The split
installer output is written to ignored `distributable/`.

### Installing and launching on the development host

From a packaged development tool such as Codex, use the host worker rather
than launching Setup or the installed app directly from the tool process:

```powershell
pwsh packaging/host_app.ps1 -Action Install -SetupPath "C:\absolute\path\RemoteVoiceSetup.exe"
pwsh packaging/host_app.ps1 -Action Start
pwsh packaging/host_app.ps1 -Action Inspect
```

Quit the app before installing. Keep all installer BIN parts alongside Setup.
This is an in-place update; do not uninstall first if retaining user data.
The worker runs once as the current interactive user, without elevation or a
recurring task. It records evidence under `~/RemoteVoiceMaintenance/` and removes
its temporary task when finished. If interrupted while installing, it retains
the running task and names it in a warning; inspect that task before retrying.

Windows can redirect a packaged tool's AppData writes into its private storage.
A successful installer exit and a shortcut visible from the same tool do not
prove that Explorer can see that shortcut. In the October 2026 incident, even
`GetPackageFullName` returned `NO_PACKAGE` in affected child processes. Verify
from the independent worker: the real Start shortcut, `Get-StartApps`, the
real user configuration, login entry, and running application. A login entry
alone does not prove that a login launch completed successfully.

If recovering a redirected installation, preserve both real and redirected
settings/history before migrating; do not silently overwrite either copy.
The host worker deliberately does not guess which copy is newer or migrate it.

## Runtime design

The Electron main process owns the tray, hotkey, recorder, overlay, settings,
history, and paste workflow. It launches the Python sidecar over newline-
delimited JSON on standard input/output. The sidecar owns model loading,
provider selection, transcription, and text cleanup.

Per-user configuration and history are stored under the normal Windows user
data directories; they are not committed to the repository.
