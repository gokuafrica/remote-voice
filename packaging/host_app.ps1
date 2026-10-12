# Use a same-user, non-elevated Windows worker for host installation and launch.
# Packaged development tools can redirect AppData writes even when
# GetCurrentPackageFullName says NO_PACKAGE. Checking from that same process
# can therefore falsely report that Start shortcuts were installed correctly.
# No recurring task or startup task is created. Evidence is retained outside
# AppData so both the caller and ordinary Windows processes can read it.
[CmdletBinding()]
param(
    [ValidateSet('Inspect', 'Start', 'Install')][string]$Action = 'Inspect',
    [string]$SetupPath,
    [switch]$Worker,
    [string]$EvidenceDirectory
)
$ErrorActionPreference = 'Stop'

if ($Worker) {
    try {
        $request = Get-Content (Join-Path $PSScriptRoot 'request.json') -Raw | ConvertFrom-Json
        $install = Join-Path $env:LOCALAPPDATA 'Programs\Remote Voice'
        $exe = Join-Path $install 'Remote Voice.exe'
        if ($request.Action -eq 'Install') {
            $running = @(Get-Process -Name 'Remote Voice' -ErrorAction SilentlyContinue | Where-Object Path -eq $exe)
            if ($running.Count) { throw 'Quit Remote Voice before installing. User data will be retained by this in-place update.' }
            foreach ($part in $request.Parts) {
                if ((Get-FileHash -LiteralPath $part.Path).Hash -ne $part.SHA256) { throw 'Installer changed after dispatch.' }
            }
            $p = Start-Process -FilePath $request.SetupPath -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', ('/LOG="' + $PSScriptRoot + '\install.log"') -WindowStyle Hidden -Wait -PassThru
            if ($p.ExitCode -ne 0) { throw "Installer returned $($p.ExitCode)" }
        }
        if ($request.Action -eq 'Start') {
            if (-not (Test-Path -LiteralPath $exe)) { throw 'Remote Voice is not installed.' }
            if (-not @(Get-Process -Name 'Remote Voice' -ErrorAction SilentlyContinue | Where-Object Path -eq $exe).Count) {
                Start-Process -FilePath $exe -WorkingDirectory $install -WindowStyle Hidden
                Start-Sleep -Seconds 5
            }
        }
        $config = Join-Path $env:APPDATA 'Remote Voice\config.json'
        $shell = New-Object -ComObject WScript.Shell
        $links = @(Get-ChildItem ([Environment]::GetFolderPath('Programs')) -Recurse -Filter 'Remote Voice.lnk' | ForEach-Object {
            $link = $shell.CreateShortcut($_.FullName)
            [pscustomobject]@{ Path = $_.FullName; Target = $link.TargetPath; TargetExists = (Test-Path -LiteralPath $link.TargetPath) }
        })
        $result = [pscustomobject]@{
            User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
            Installed = (Test-Path -LiteralPath $exe)
            Shortcuts = $links
            StartApps = @(Get-StartApps | Where-Object Name -eq 'Remote Voice')
            ConfigExists = (Test-Path -LiteralPath $config)
            ConfigHash = if (Test-Path -LiteralPath $config) { (Get-FileHash -LiteralPath $config).Hash } else { $null }
            AutoStart = if (Test-Path -LiteralPath $config) { (Get-Content $config -Raw | ConvertFrom-Json).auto_start } else { $null }
            LoginEntries = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' | Select-Object '*Voice*')
            Processes = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $exe -and $_.CommandLine -notmatch '--type=' } | Select-Object ProcessId, ParentProcessId, CreationDate)
        }
        if ($request.Action -eq 'Install' -and (-not $result.Installed -or -not @($links | Where-Object { $_.Target -eq $exe -and $_.TargetExists }).Count)) {
            throw 'Setup returned success but the ordinary Windows installation/shortcut check failed.'
        }
        if ($request.Action -eq 'Start' -and $result.Processes.Count -ne 1) {
            throw 'Expected one running main process after launch. Inspect application diagnostics.'
        }
        $result | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $PSScriptRoot 'result.json')
        exit 0
    } catch {
        $_.Exception.Message | Set-Content (Join-Path $PSScriptRoot 'error.txt')
        exit 1
    }
}

$id = [Guid]::NewGuid().ToString('N')
if (-not $EvidenceDirectory) { $EvidenceDirectory = Join-Path $env:USERPROFILE "RemoteVoiceMaintenance\$id" }
$EvidenceDirectory = [IO.Path]::GetFullPath($EvidenceDirectory)
if (Test-Path -LiteralPath $EvidenceDirectory) { throw 'Choose a new evidence directory for each run.' }
$parts = @()
if ($Action -eq 'Install') {
    if (-not $SetupPath) { throw 'Install requires the freshly built multipart RemoteVoiceSetup.exe path.' }
    $SetupPath = (Resolve-Path -LiteralPath $SetupPath).Path
    if ([IO.Path]::GetFileName($SetupPath) -ne 'RemoteVoiceSetup.exe') { throw 'Unexpected setup filename.' }
    $parts = @(Get-ChildItem (Split-Path $SetupPath) -File | Where-Object Name -match '^RemoteVoiceSetup(?:-\d+\.bin|\.exe)$' | ForEach-Object {
        [pscustomobject]@{ Path = $_.FullName; SHA256 = (Get-FileHash -LiteralPath $_.FullName).Hash }
    })
    if ($parts.Count -lt 2) { throw 'Multipart installer BIN files are missing.' }
    $slices = @($parts | Where-Object Path -match '-\d+\.bin$' | ForEach-Object { [int]([regex]::Match($_.Path, '-(\d+)\.bin$').Groups[1].Value) } | Sort-Object)
    for ($i = 0; $i -lt $slices.Count; $i++) {
        if ($slices[$i] -ne $i + 1) { throw 'Installer BIN numbering contains a gap.' }
    }
}
New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null
Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $EvidenceDirectory 'worker.ps1')
@{ Action = $Action; SetupPath = $SetupPath; Parts = $parts } | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $EvidenceDirectory 'request.json')
$taskName = "RemoteVoice-Maintenance-$id"
try {
    $taskAction = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $EvidenceDirectory + '\worker.ps1" -Worker')
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Principal $principal | Out-Null
    Start-ScheduledTask -TaskName $taskName
    $deadline = (Get-Date).AddMinutes(20)
    while (-not (Test-Path "$EvidenceDirectory\result.json") -and -not (Test-Path "$EvidenceDirectory\error.txt")) {
        if ((Get-Date) -gt $deadline) { throw "Worker timed out; inspect $EvidenceDirectory" }
        Start-Sleep -Milliseconds 500
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        if ((Get-ScheduledTask -TaskName $taskName).State -ne 'Running' -and $info.LastTaskResult -ne 0 -and $info.LastTaskResult -ne 267011) {
            throw "Worker failed with task result $($info.LastTaskResult); inspect $EvidenceDirectory"
        }
    }
    if (Test-Path "$EvidenceDirectory\error.txt") { throw (Get-Content "$EvidenceDirectory\error.txt" -Raw) }
    Get-Content "$EvidenceDirectory\result.json" -Raw
} finally {
    # Do not stop a still-running installer on a timeout or interruption.
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    for ($attempt = 0; $task -and $task.State -eq 'Running' -and $attempt -lt 10 -and (Test-Path "$EvidenceDirectory\result.json"); $attempt++) {
        Start-Sleep -Milliseconds 100
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }
    if ($task -and $task.State -ne 'Running') { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
    elseif ($task) { Write-Warning "Worker is still running; retain task $taskName and inspect it before cleanup." }
    Write-Host "Evidence: $EvidenceDirectory"
}
