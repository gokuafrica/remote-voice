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

## Runtime design

The Electron main process owns the tray, hotkey, recorder, overlay, settings,
history, and paste workflow. It launches the Python sidecar over newline-
delimited JSON on standard input/output. The sidecar owns model loading,
provider selection, transcription, and text cleanup.

Per-user configuration and history are stored under the normal Windows user
data directories; they are not committed to the repository.
