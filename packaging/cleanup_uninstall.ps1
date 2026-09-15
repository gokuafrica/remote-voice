# Remove the installing user's Remote Voice state during uninstall.
#
# The installer is per-user and the app stores its settings, history, Electron
# profile, and model cache per Windows user. Do not scan or modify other users'
# profiles during an uninstall.

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$InstallDir
)

$ErrorActionPreference = 'Continue'

function Remove-Tree([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Remove-RemoteVoiceTempFiles([string]$TempDir) {
    if (-not (Test-Path -LiteralPath $TempDir -PathType Container)) { return }
    Get-ChildItem -LiteralPath $TempDir -Filter 'remote-voice-*.wav' -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

# Delete the task first so it cannot start a new copy while files are being
# removed. The direct schtasks call is also repeated by installer.iss as a
# fallback if PowerShell itself cannot start this helper.
& "$env:WINDIR\System32\schtasks.exe" /Delete /F /TN 'RemoteVoiceAutostart' *> $null

# Remove the per-user login item used by current releases. The uninstaller
# The uninstall helper runs for the installing user, so clear only that user's
# login item.
$runKeyPath = 'Software\Microsoft\Windows\CurrentVersion\Run'
$runValueName = 'Remote Voice'
& "$env:WINDIR\System32\reg.exe" delete "HKCU\$runKeyPath" /v $runValueName /f *> $null

# Inno's CloseApplications setting is a best effort. Stop the exact product
# process as a final guard against locked Program Files files and state files.
$exePath = [IO.Path]::GetFullPath((Join-Path $InstallDir 'Remote Voice.exe'))
Get-Process -Name 'Remote Voice' -ErrorAction SilentlyContinue |
    Where-Object {
        try { $_.Path -eq $exePath } catch { $false }
    } |
    Stop-Process -Force -ErrorAction SilentlyContinue

$profile = $env:USERPROFILE
if ($profile) {
    Remove-Tree (Join-Path $profile 'AppData\Roaming\Remote Voice')
    # Older releases used this name before the Remote Voice rebrand.
    Remove-Tree (Join-Path $profile 'AppData\Roaming\SpokenlyV2')
    Remove-Tree (Join-Path $profile 'AppData\Local\Remote Voice')
    Remove-RemoteVoiceTempFiles (Join-Path $profile 'AppData\Local\Temp')
}

# Also cover nonstandard TEMP/TMP locations used by the running process.
@($env:TEMP, $env:TMP, (Join-Path $env:WINDIR 'Temp')) |
    Where-Object { $_ } |
    Sort-Object -Unique |
    ForEach-Object { Remove-RemoteVoiceTempFiles $_ }

exit 0
