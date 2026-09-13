# Register Remote Voice to start at logon for interactive desktop users.
#
# Run by the elevated installer. A group principal is intentional: the task
# receives the limited token of the user who is signing in, so the app reads
# that user's %APPDATA%/%LOCALAPPDATA% state instead of the administrator
# account used to approve Setup.

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$AppDir
)

$ErrorActionPreference = 'Stop'

$exe = Join-Path $AppDir 'Remote Voice.exe'

if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Remote Voice executable not found: $exe"
}

$action = New-ScheduledTaskAction `
    -Execute $exe `
    -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal `
    -GroupId 'S-1-5-32-545' `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName 'RemoteVoiceAutostart' `
    -Description 'Starts the Remote Voice dictation app for the signed-in user.' `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null

Write-Output 'RemoteVoiceAutostart scheduled task registered.'
