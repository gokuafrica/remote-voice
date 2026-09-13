# Register the Remote Voice server for interactive desktop users.
#
# This script is run by the elevated installer.  A group principal is
# intentional: the task itself receives the limited token of the user who is
# signing in, so the server reads that user's %LOCALAPPDATA% configuration
# instead of the administrator account used to approve Setup.

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$AppDir
)

$ErrorActionPreference = 'Stop'

$python = Join-Path $AppDir 'python311\pythonw.exe'
$server = Join-Path $AppDir 'server.py'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Bundled Python executable not found: $python"
}
if (-not (Test-Path -LiteralPath $server -PathType Leaf)) {
    throw "Remote Voice server script not found: $server"
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}"' -f $server) `
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
    -TaskName 'RemoteVoiceServer' `
    -Description 'Remote Voice ASR server for the signed-in user.' `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null

Write-Output 'RemoteVoiceServer scheduled task registered.'
