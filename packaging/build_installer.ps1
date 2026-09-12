# Remote Voice - installer build script (PowerShell 7+)
# Produces distributable\RemoteVoiceSetup.exe containing an embedded Python 3.11
# with all dependencies pre-installed, bundled ffmpeg.exe, and the Windows-only
# source files with FRESH default configs.
#
# Usage (from repo root or this directory):
#   pwsh packaging\build_installer.ps1 [-PythonVersion '3.11.9'] [-NoPrompt]
# Requires: pwsh 7+, internet access for first-run downloads. Admin NOT required
# to build (the installer itself elevates at install time).

param(
    [string]$PythonVersion = '3.11.9',
    [switch]$NoPrompt
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is unusably slow with the progress bar

function Write-Step([int]$n, [string]$msg) {
    Write-Host ""
    Write-Host "=== Step $n : $msg ===" -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
# Resolve repo root (script may be run from repo root or packaging\)
# ---------------------------------------------------------------------------
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'requirements.txt'))) {
    # PSScriptRoot wasn't packaging\ (e.g. dot-sourced oddly); fall back to cwd
    $repoRoot = (Get-Location).Path
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'requirements.txt'))) {
        throw "Cannot locate repo root (requirements.txt not found). Run this script from the repo root or packaging\."
    }
}

$packagingDir  = Join-Path $repoRoot 'packaging'
$cacheDir      = Join-Path $packagingDir 'cache'
$stageDir      = Join-Path $packagingDir 'stage'
$defaultsDir   = Join-Path $packagingDir 'defaults'
$distributable = Join-Path $repoRoot 'distributable'
$stagePython   = Join-Path $stageDir 'python311'
$pythonZipName = "python-$PythonVersion-embed-amd64.zip"

Write-Host "Repo root: $repoRoot"
Write-Host "Python:    $PythonVersion (embeddable amd64)"

# ---------------------------------------------------------------------------
# Step 1: Ensure cache directory + downloads
# ---------------------------------------------------------------------------
Write-Step 1 "Ensure downloads (cached in packaging\cache)"
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null

$pythonZip = Join-Path $cacheDir $pythonZipName
$getPip    = Join-Path $cacheDir 'get-pip.py'
$ffmpegZip = Join-Path $cacheDir 'ffmpeg-release-essentials.zip'
$ffmpegExe = Join-Path $cacheDir 'ffmpeg.exe'

function Get-Download([string]$Url, [string]$Dest, [string]$Label) {
    if (Test-Path -LiteralPath $Dest) {
        Write-Host "  [cached] $Label"
        return
    }
    Write-Host "  [downloading] $Label from $Url"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Dest -TimeoutSec 600 -UseBasicParsing
    } catch {
        if (Test-Path -LiteralPath $Dest) { Remove-Item -LiteralPath $Dest -Force }
        throw "FAILED to download ${Label}: $($_.Exception.Message)"
    }
    if (-not (Test-Path -LiteralPath $Dest)) {
        throw "FAILED to download ${Label}: file missing after download."
    }
}

Get-Download "https://www.python.org/ftp/python/$PythonVersion/$pythonZipName" $pythonZip "Python $PythonVersion embeddable"
Get-Download "https://bootstrap.pypa.io/get-pip.py" $getPip "get-pip.py"
Get-Download "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $ffmpegZip "ffmpeg essentials build"

# Validate ffmpeg zip contains bin/ffmpeg.exe and extract just that binary.
if (-not (Test-Path -LiteralPath $ffmpegExe)) {
    Write-Host "  [extracting] ffmpeg.exe from zip"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($ffmpegZip)
    try {
        $entry = $zip.Entries | Where-Object { $_.FullName -imatch '(^|/)bin/ffmpeg\.exe$' } | Select-Object -First 1
        if (-not $entry) {
            throw "ffmpeg zip does not contain bin/ffmpeg.exe (got: $(($zip.Entries.FullName | Select-Object -First 20) -join ', ')...)"
        }
        [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $ffmpegExe, $true)
    } finally {
        $zip.Dispose()
    }
}
if (-not (Test-Path -LiteralPath $ffmpegExe)) { throw "ffmpeg.exe missing after extraction." }
Write-Host "  ffmpeg.exe ready: $((Get-Item $ffmpegExe).Length / 1MB -as [int]) MB"

# ---------------------------------------------------------------------------
# Step 2: Prepare staging directory (clean rebuild) + embedded python
# ---------------------------------------------------------------------------
Write-Step 2 "Prepare staging directory"
if (Test-Path -LiteralPath $stageDir) {
    Write-Host "  deleting old stage dir..."
    Remove-Item -LiteralPath $stageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null
Write-Host "  expanding $pythonZipName into stage\python311..."
Expand-Archive -LiteralPath $pythonZip -DestinationPath $stagePython -Force

# Bootstrap the embeddable distribution for pip + site-packages:
#  - python311._pth: uncomment 'import site' and append 'Lib\site-packages'
$pthPath = Join-Path $stagePython 'python311._pth'
$pth = Get-Content -LiteralPath $pthPath -Raw
$pth = $pth -replace '(?m)^#\s*import\s+site\s*$', 'import site'
if ($pth -notmatch '(?m)^Lib\\site-packages\s*$') {
    $pth = $pth.TrimEnd() + "`r`nLib`r`nDLLs`r`nLib\site-packages`r`n"
} elseif ($pth -notmatch '(?m)^Lib\s*$') {
    $pth = $pth -replace '(?m)^(Lib\\site-packages\s*)$', "Lib`r`nDLLs`r`n`$1"
}
if ($pth -notmatch '(?m)^DLLs\s*$') {
    $pth = $pth -replace '(?m)^(Lib\s*)$', "`$1DLLs`r`n"
    if ($pth -notmatch '(?m)^DLLs\s*$') { $pth = $pth.TrimEnd() + "`r`nDLLs`r`n" }
}
Set-Content -LiteralPath $pthPath -Value $pth -NoNewline
Write-Host "  python311._pth patched:"
Get-Content -LiteralPath $pthPath | ForEach-Object { Write-Host "    | $_" }

# get-pip + dependencies. Run from repo root so requirements.txt resolves.
Write-Host "  installing pip into embedded python..."
& (Join-Path $stagePython 'python.exe') $getPip --no-warn-script-location 2>&1 | ForEach-Object { Write-Host "    | $_" }
if ($LASTEXITCODE -ne 0) { throw "get-pip.py failed (exit $LASTEXITCODE)." }

Write-Host "  installing python dependencies (this can take several minutes)..."
# onnxruntime-gpu is pinned to 1.24.4: the CUDA-12 build. Newer releases target
# CUDA 13, which the nvidia-*-cu12 wheels below do not provide.
& (Join-Path $stagePython 'python.exe') -m pip install --no-warn-script-location `
    -r (Join-Path $repoRoot 'requirements.txt') `
    "onnxruntime-gpu==1.24.4" `
    keyboard pystray requests sounddevice Pillow pynput `
    nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 `
    nvidia-cufft-cu12 nvidia-cusparse-cu12 nvidia-cusolver-cu12 `
    nvidia-curand-cu12 nvidia-nvjitlink-cu12 `
    2>&1 | ForEach-Object { Write-Host "    | $_" }
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)." }

# ---------------------------------------------------------------------------
# Step 3: Copy tkinter from a system Python 3.11 (embeddable zip lacks it)
# ---------------------------------------------------------------------------
Write-Step 3 "Copy tkinter from system Python 3.11"

function Get-SystemPython311Prefix {
    # Try py -3.11 first, then python/python3 filtering for 3.11.x
    foreach ($cmd in @(@('py', '-3.11'), @('python'), @('python3'))) {
        $exe = $cmd[0]
        $args = @()
        if ($cmd.Count -gt 1) { $args += $cmd[1] }
        try {
            $out = & $exe @args -c "import sys, sysconfig; print(sys.prefix)" 2>$null
        } catch { $out = $null }
        if (-not $out -or $LASTEXITCODE -ne 0) { continue }
        $prefix = ($out | Select-Object -Last 1).Trim()
        if ($cmd[0] -eq 'py') { return $prefix }  # py -3.11 is already version-filtered
        try {
            $ver = & $exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        } catch { $ver = $null }
        $ver = ($ver | Select-Object -Last 1).Trim()
        if ($ver -eq '3.11') { return $prefix }
    }
    return $null
}

$sysPy = Get-SystemPython311Prefix
if (-not $sysPy) {
    Write-Warning "  NO system Python 3.11 found. gui.py needs tkinter but the embeddable"
    Write-Warning "  distribution does not ship it. Install Python 3.11 from python.org"
    Write-Warning "  (with tcl/tk enabled) and re-run this script."
    throw "tkinter source (system Python 3.11) not found; cannot build a working GUI."
}
Write-Host "  using system Python at: $sysPy"

$tkCopied = $true
function Copy-IfExists([string]$From, [string]$To, [string]$Label) {
    if (Test-Path -LiteralPath $From) {
        $parent = Split-Path -Parent $To
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Copy-Item -LiteralPath $From -Destination $To -Recurse -Force
        Write-Host "  copied: $Label"
    } else {
        Write-Warning "  MISSING (skipped): $From ($Label)"
        $script:tkCopied = $false
    }
}

Copy-IfExists (Join-Path $sysPy 'Lib\tkinter')                            (Join-Path $stagePython 'Lib\tkinter')  'Lib\tkinter\'
Copy-IfExists (Join-Path $sysPy 'DLLs\_tkinter.pyd')                      (Join-Path $stagePython 'DLLs\_tkinter.pyd') 'DLLs\_tkinter.pyd'
Get-ChildItem -LiteralPath (Join-Path $sysPy 'DLLs') -Filter 'tcl*.dll' -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-IfExists $_.FullName (Join-Path $stagePython "DLLs\$($_.Name)") "DLLs\$($_.Name)" }
Get-ChildItem -LiteralPath (Join-Path $sysPy 'DLLs') -Filter 'tk*.dll' -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-IfExists $_.FullName (Join-Path $stagePython "DLLs\$($_.Name)") "DLLs\$($_.Name)" }

# Tcl/Tk data folders: prefer DLLs\tcl + DLLs\tk (newer layout), else <prefix>\tcl
$sysDlls = Join-Path $sysPy 'DLLs'
if ((Test-Path (Join-Path $sysDlls 'tcl')) -and (Test-Path (Join-Path $sysDlls 'tk'))) {
    Copy-IfExists (Join-Path $sysDlls 'tcl') (Join-Path $stagePython 'DLLs\tcl') 'DLLs\tcl\'
    Copy-IfExists (Join-Path $sysDlls 'tk')  (Join-Path $stagePython 'DLLs\tk')  'DLLs\tk\'
} elseif (Test-Path (Join-Path $sysPy 'tcl')) {
    Copy-IfExists (Join-Path $sysPy 'tcl') (Join-Path $stagePython 'tcl') 'tcl\'
} else {
    Write-Warning "  No tcl/tk library folders found in system Python - tkinter may fail at runtime."
    $tkCopied = $false
}

if (-not $tkCopied) {
    Write-Warning "  TKINTER COPY INCOMPLETE - the GUI may not start on the target machine."
    if (-not $NoPrompt) {
        $ans = Read-Host "Continue build anyway? [y/N]"
        if ($ans -notmatch '^[Yy]') { throw "Aborted by user due to incomplete tkinter copy." }
    }
}

# ---------------------------------------------------------------------------
# Step 4: Copy source files + fresh defaults + ffmpeg
# ---------------------------------------------------------------------------
Write-Step 4 "Copy source files, default configs, ffmpeg"

$sourceFiles = @(
    'server.py',
    'gui.py',
    'tray.py',
    'Remote Voice.bat',
    'Remote Voice Tray.bat',
    'start.bat',
    'handy-prompt.txt'
)
foreach ($f in $sourceFiles) {
    $src = Join-Path $repoRoot $f
    if (-not (Test-Path -LiteralPath $src)) { throw "Required source file missing: $src" }
    Copy-Item -LiteralPath $src -Destination $stageDir -Force
    Write-Host "  copied: $f"
}
# Deliberately NOT copied: config.json, tray_config.json (personal), mac_* files,
# tests*.py, requirements*.txt, README.md, CLAUDE.md, __pycache__, .git.

foreach ($cfg in @('config.json', 'tray_config.json')) {
    $src = Join-Path $defaultsDir $cfg
    if (-not (Test-Path -LiteralPath $src)) { throw "Missing default config: $src" }
    Copy-Item -LiteralPath $src -Destination $stageDir -Force
    Write-Host "  copied default: $cfg"
}

New-Item -ItemType Directory -Force -Path (Join-Path $stageDir 'ffmpeg') | Out-Null
Copy-Item -LiteralPath $ffmpegExe -Destination (Join-Path $stageDir 'ffmpeg\ffmpeg.exe') -Force
Write-Host "  copied: ffmpeg\ffmpeg.exe"

# ---------------------------------------------------------------------------
# Step 5: Verify staged python imports
# ---------------------------------------------------------------------------
Write-Step 5 "Verify staged python imports"
& (Join-Path $stagePython 'python.exe') -c "import onnxruntime, tkinter, pystray, keyboard, sounddevice, PIL, pynput, fastapi; print('imports OK', onnxruntime.get_available_providers())" 2>&1 | ForEach-Object { Write-Host "    | $_" }
if ($LASTEXITCODE -ne 0) { throw "Staged python import verification FAILED (exit $LASTEXITCODE)." }

# ---------------------------------------------------------------------------
# Step 6: Locate Inno Setup compiler (ISCC.exe), install via winget if needed
# ---------------------------------------------------------------------------
Write-Step 6 "Locate Inno Setup compiler"
$isccPaths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $isccPaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "  ISCC.exe not found; attempting winget install JRSoftware.InnoSetup..."
    & winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements 2>&1 | ForEach-Object { Write-Host "    | $_" }
    # winget may require a new PATH; re-check both locations
    $iscc = $isccPaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $iscc) {
    throw "Inno Setup 6 (ISCC.exe) not found and winget install did not fix it. Install from https://jrsoftware.org/isdl.php and re-run."
}
Write-Host "  ISCC: $iscc"

# ---------------------------------------------------------------------------
# Step 7: Compile installer
# ---------------------------------------------------------------------------
Write-Step 7 "Compile installer (this compresses ~1GB of payload; be patient)"
New-Item -ItemType Directory -Force -Path $distributable | Out-Null
& $iscc "/DSTAGEDIR=$stageDir" "/DOUTPUTDIR=$distributable" (Join-Path $packagingDir 'installer.iss') 2>&1 | ForEach-Object { Write-Host "    | $_" }
if ($LASTEXITCODE -ne 0) { throw "ISCC compilation failed (exit $LASTEXITCODE)." }

# ---------------------------------------------------------------------------
# Step 8: Verify output
# ---------------------------------------------------------------------------
Write-Step 8 "Verify output"
$setupExe = Join-Path $distributable 'RemoteVoiceSetup.exe'
if (-not (Test-Path -LiteralPath $setupExe)) { throw "Expected output missing: $setupExe" }
$sizeGB = [math]::Round((Get-Item $setupExe).Length / 1GB, 2)
Write-Host ""
Write-Host "DONE: $setupExe ($sizeGB GB)" -ForegroundColor Green
