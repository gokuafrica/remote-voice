# Remote Voice Windows application

## Goal

Provide one Windows desktop application with an Electron shell and an embedded-
capable Python transcription sidecar. There is no separate HTTP server,
source-based tray application, firewall rule, or scheduled-task installer
path in this worktree.

## Repository layout

```text
app/
  main/                       Electron main-process modules
  renderer/                   Overlay, settings, and recorder UI
  preload.js                  Renderer bridge
  package.json                Electron dependencies and scripts
  package-lock.json           Locked JavaScript dependency graph
config.json                   Seed configuration
engine/
  engine.py                   Stdio transcription sidecar
  engine_tests.py             Sidecar/pipeline tests
  smoke_client.py             Sidecar protocol client
  requirements.txt            Python dependencies for the sidecar
packaging/
  package_app.js              Electron packaging
  build_installer.ps1         Full installer assembly
  installer.iss               Inno Setup definition
  cleanup_uninstall.ps1       Per-user uninstall cleanup
README.md                     Development and packaging instructions
```

Generated Electron packages, installer staging trees, download caches, and
installer outputs are deliberately excluded by `.gitignore`.

## Runtime contract

The Electron main process starts `engine/engine.py` with a configuration path.
The sidecar communicates through newline-delimited JSON on standard input and
output. Supported operations include:

- `ping` for process/model readiness
- `transcribe` for PCM audio or a WAV path
- `set_fixes` for hot-reloaded pronunciation replacements
- `shutdown` for orderly termination

Diagnostics go to standard error so standard output remains protocol-only.

## Packaging contract

`packaging/package_app.js` packages `app/` for Windows and includes the seed
configuration and engine resources. `packaging/build_installer.ps1` then adds
the embedded Python runtime, sidecar dependencies, and model cache before
`packaging/installer.iss` creates the per-user installer.
