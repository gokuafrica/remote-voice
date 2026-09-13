# Remote Voice (rework) - installer build script (PowerShell 7+)
#
# Produces distributable\RemoteVoiceSetup.exe: an Inno Setup installer that
# bundles EVERYTHING needed on a clean machine (build-time downloads only):
#   - the packaged Electron app   (packaged dir -> stage\_pkg)
#   - embedded Python 3.11 + all pip deps + CUDA 12 wheels
#     (onnxruntime-gpu==1.24.4, see engine\requirements.txt)
#   - the Parakeet TDT 0.6b v2 ONNX weights (~2.4 GB) as resources\hf_cache
#   - vc_redist.x64.exe (Microsoft-signed, verified)
#
# Usage (from repo root or packaging\):
#   pwsh packaging\build_installer.ps1 [-PythonVersion '3.11.9']
# Requires: pwsh 7+, node/npm (for electron packaging), internet on first run.
# Admin NOT required to build (the installer elevates at install time).

param(
    [string]$PythonVersion = '3.11.9'
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
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'engine\engine.py'))) {
    $repoRoot = (Get-Location).Path
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'engine\engine.py'))) {
        throw "Cannot locate repo root (engine\engine.py not found). Run from repo root or packaging\."
    }
}

$packagingDir  = Join-Path $repoRoot 'packaging'
$cacheDir      = Join-Path $packagingDir 'cache'
$stageDir      = Join-Path $packagingDir 'stage'
$stagePython   = Join-Path $stageDir 'python311'
$stagePkg      = Join-Path $stageDir '_pkg'
$stageHf       = Join-Path $stageDir 'hf_cache'
$distributable = Join-Path $repoRoot 'distributable'
$pythonZipName = "python-$PythonVersion-embed-amd64.zip"

# Old worktree's download cache — reuse artifacts when versions match.
$masterCache = Join-Path (Split-Path -Parent $repoRoot) 'master\packaging\cache'
if (-not (Test-Path -LiteralPath $masterCache)) { $masterCache = $null }

Write-Host "Repo root: $repoRoot"
Write-Host "Python:    $PythonVersion (embeddable amd64)"

# ---------------------------------------------------------------------------
# Step 1: Downloads (cached in packaging\cache)
# ---------------------------------------------------------------------------
Write-Step 1 "Ensure downloads (cached in packaging\cache)"
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null

$pythonZip = Join-Path $cacheDir $pythonZipName
$getPip    = Join-Path $cacheDir 'get-pip.py'
$vcRedist  = Join-Path $cacheDir 'vc_redist.x64.exe'

function Get-Download([string]$Url, [string]$Dest, [string]$Label) {
    if (Test-Path -LiteralPath $Dest) {
        Write-Host "  [cached] $Label"
        return
    }
    # Reuse the master worktree's cache if it has the same artifact.
    if ($masterCache) {
        $shared = Join-Path $masterCache (Split-Path -Leaf $Dest)
        if ((Test-Path -LiteralPath $shared) -and ((Get-Item $shared).Length -gt 0)) {
            Write-Host "  [reused from master cache] $Label"
            Copy-Item -LiteralPath $shared -Destination $Dest
            return
        }
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
Get-Download "https://aka.ms/vc14/vc_redist.x64.exe" $vcRedist "Microsoft Visual C++ x64 runtime"

# vc_redist must be valid and Microsoft-signed (it runs silently in Setup).
$vcSignature = Get-AuthenticodeSignature -LiteralPath $vcRedist
if ($vcSignature.Status -ne 'Valid' -or
    $vcSignature.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation') {
    throw "Visual C++ redistributable signature is not valid and Microsoft-signed: $vcRedist"
}
Write-Host "  vc_redist signature OK ($($vcSignature.SignerCertificate.Subject))"

# ---------------------------------------------------------------------------
# Step 2: Stage embedded Python 3.11 + patch ._pth
# ---------------------------------------------------------------------------
Write-Step 2 "Stage embedded Python + patch python311._pth"
if (-not (Test-Path -LiteralPath (Join-Path $stagePython 'python.exe'))) {
    if (Test-Path -LiteralPath $stagePython) { Remove-Item -LiteralPath $stagePython -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $stagePython | Out-Null
    Expand-Archive -LiteralPath $pythonZip -DestinationPath $stagePython -Force
} else {
    Write-Host "  [cached] staged python311 already expanded"
}

$pthPath = Join-Path $stagePython 'python311._pth'
$pth = Get-Content -LiteralPath $pthPath -Raw
$pth = $pth -replace '(?m)^#\s*import\s+site\s*$', 'import site'
# Embedded python has no site-packages on sys.path by default. Add the stdlib
# dirs, pip's site-packages, and the app-code parent (engine/ lives one level
# above python311 in the packaged resources).
if ($pth -notmatch '(?m)^Lib\s*$')    { $pth = $pth.TrimEnd() + "`r`nLib`r`n" }
if ($pth -notmatch '(?m)^DLLs\s*$')   { $pth = $pth.TrimEnd() + "`r`nDLLs`r`n" }
if ($pth -notmatch '(?m)^Lib\\site-packages\s*$') { $pth = $pth.TrimEnd() + "`r`nLib\site-packages`r`n" }
if ($pth -notmatch '(?m)^\.\.\s*$')   { $pth = $pth.TrimEnd() + "`r`n..`r`n" }
Set-Content -LiteralPath $pthPath -Value $pth -NoNewline
Write-Host "  python311._pth:"
Get-Content -LiteralPath $pthPath | ForEach-Object { Write-Host "    | $_" }

# ---------------------------------------------------------------------------
# Step 3: pip install engine deps + nvidia cu12 wheels (background, polled)
# ---------------------------------------------------------------------------
Write-Step 3 "pip install engine deps + nvidia cu12 wheels"
if (-not (Test-Path -LiteralPath (Join-Path $stagePython 'Lib\site-packages\onnx_asr'))) {
    $pyExe = Join-Path $stagePython 'python.exe'
    $pipLog = Join-Path $stageDir 'pip-install.log'
    if (Test-Path -LiteralPath (Join-Path $stagePython 'Scripts\pip.exe')) {
        Write-Host "  [cached] pip already bootstrapped"
    } else {
        Write-Host "  installing pip into embedded python..."
        & $pyExe $getPip --no-warn-script-location 2>&1 | Select-Object -Last 2 | ForEach-Object { Write-Host "    | $_" }
        if ($LASTEXITCODE -ne 0) { throw "get-pip.py failed (exit $LASTEXITCODE)." }
    }
    $pkgs = @(
        '-m', 'pip', 'install', '--no-warn-script-location',
        '-r', (Join-Path $repoRoot 'engine\requirements.txt'),
        'nvidia-cublas-cu12', 'nvidia-cuda-runtime-cu12', 'nvidia-cuda-nvrtc-cu12',
        'nvidia-cudnn-cu12', 'nvidia-cufft-cu12', 'nvidia-cusparse-cu12',
        'nvidia-cusolver-cu12', 'nvidia-curand-cu12', 'nvidia-nvjitlink-cu12'
    )
    Write-Host "  starting pip install in background (log: $pipLog)..."
    # Long downloads: run detached, poll with short commands.
    Start-Process -FilePath $pyExe -ArgumentList $pkgs -WindowStyle Hidden `
        -RedirectStandardOutput $pipLog -RedirectStandardError "$pipLog.err"
    $pipStart = Get-Date
    while ($true) {
        Start-Sleep -Seconds 10
        $procs = Get-Process -Name python -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $pyExe }
        $done = -not $procs
        if ($done -and (Test-Path -LiteralPath $pipLog)) {
            # give the log a moment to flush
            Start-Sleep -Seconds 2
            break
        }
        $tail = Get-Content -LiteralPath $pipLog -Tail 1 -ErrorAction SilentlyContinue
        if ($tail) { Write-Host "    | $tail" }
        if ($done) { break }
        # hard safety: 30 min
        if (((Get-Date) - $pipStart).TotalMinutes -gt 30) { throw "pip install timed out after 30 minutes." }
    }
    Get-Content -LiteralPath "$pipLog.err" -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    | $_" }
    if (-not (Test-Path -LiteralPath (Join-Path $stagePython 'Lib\site-packages\onnx_asr'))) {
        throw "pip install failed — onnx_asr not present in staged site-packages (see $pipLog)."
    }
    Write-Host "  pip install complete."
} else {
    Write-Host "  [cached] engine deps already installed"
}

# ---------------------------------------------------------------------------
# Step 4: Stage HF model cache (copy from local HF cache; download if absent)
# ---------------------------------------------------------------------------
Write-Step 4 "Stage Parakeet model weights (hf_cache)"
New-Item -ItemType Directory -Force -Path $stageHf | Out-Null
$repoDirName = 'models--istupakov--parakeet-tdt-0.6b-v2-onnx'
$localHfRepo = Join-Path $env:USERPROFILE ".cache\huggingface\hub\$repoDirName"
$stagedRepo  = Join-Path $stageHf "hub\$repoDirName"
if (Test-Path -LiteralPath (Join-Path $stagedRepo 'refs\main')) {
    Write-Host "  [cached] staged hf_cache already contains the model"
} elseif (Test-Path -LiteralPath (Join-Path $localHfRepo 'refs\main')) {
    Write-Host "  [copy] $localHfRepo -> stage (2.4 GB, takes a minute)..."
    New-Item -ItemType Directory -Force -Path (Join-Path $stageHf 'hub') | Out-Null
    # /SL: copy symlinks as links when possible; snapshot files here are real
    # files so this degrades to a plain copy gracefully.
    robocopy $localHfRepo $stagedRepo /E /COPY:DAT /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy of HF model cache failed (exit $LASTEXITCODE)." }
    Write-Host "  copied."
} else {
    Write-Host "  model not cached locally — downloading via staged python (2.4 GB)..."
    $pyExe = Join-Path $stagePython 'python.exe'
    $dlLog = Join-Path $stageDir 'hf-download.log'
    $dlScript = Join-Path $stageDir 'hf_download.py'
    @'
import os, sys
os.environ["HF_HOME"] = sys.argv[1]
import onnx_asr
m = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", providers=["CPUExecutionProvider"])
print("DOWNLOAD-OK")
'@ | Set-Content -LiteralPath $dlScript
    Start-Process -FilePath $pyExe -ArgumentList "`"$dlScript`" `"$stageHf`"" -WindowStyle Hidden `
        -RedirectStandardOutput $dlLog -RedirectStandardError "$dlLog.err"
    while ($true) {
        Start-Sleep -Seconds 15
        $running = Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $pyExe }
        if (-not $running) { break }
        $sz = 0
        if (Test-Path -LiteralPath $stagedRepo) {
            $sz = (Get-ChildItem -LiteralPath $stagedRepo -Recurse -File -ErrorAction SilentlyContinue |
                Measure-Object Length -Sum).Sum
        }
        Write-Host ("    | downloaded {0:N0} MB" -f ($sz / 1MB))
    }
    Get-Content -LiteralPath $dlLog -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    | $_" }
    Get-Content -LiteralPath "$dlLog.err" -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    | $_" }
    if (-not (Test-Path -LiteralPath (Join-Path $stagedRepo 'refs\main'))) {
        throw "HF model download failed — refs\main missing (see $dlLog.err)."
    }
}
$hfSize = (Get-ChildItem -LiteralPath $stageHf -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("  hf_cache size: {0:N1} GB" -f ($hfSize / 1GB))

# ---------------------------------------------------------------------------
# Step 5: Staged-python verification gate
# ---------------------------------------------------------------------------
Write-Step 5 "Verify staged python imports (onnxruntime + providers)"
$pyExe = Join-Path $stagePython 'python.exe'
$gateOut = & $pyExe -c "import onnxruntime, onnx_asr, httpx, word2number; print('gate OK', onnxruntime.__version__, onnxruntime.get_available_providers())" 2>&1
$gateOut | ForEach-Object { Write-Host "    | $_" }
if ($LASTEXITCODE -ne 0) { throw "Staged-python import verification FAILED (exit $LASTEXITCODE)." }
if (($gateOut -join "`n") -notmatch 'CUDAExecutionProvider') {
    throw "Staged python does not advertise CUDAExecutionProvider — CUDA DLL layout broken."
}

# ---------------------------------------------------------------------------
# Step 6: Package the Electron app into stage\_pkg (dist\ untouched)
# ---------------------------------------------------------------------------
Write-Step 6 "Package Electron app into stage\_pkg"
if (Test-Path -LiteralPath (Join-Path $stagePkg 'Remote Voice-win32-x64\Remote Voice.exe')) {
    Write-Host "  [cached] packaged app already present"
} else {
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'app\node_modules\electron'))) {
        Write-Host "  npm install in app\..."
        & npm.cmd install --prefix (Join-Path $repoRoot 'app') 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Host "    | $_" }
        if ($LASTEXITCODE -ne 0) { throw "npm install failed." }
    }
    & node.exe (Join-Path $packagingDir 'package_app.js') $stagePkg 2>&1 | ForEach-Object { Write-Host "    | $_" }
    if ($LASTEXITCODE -ne 0) { throw "Electron packaging failed." }
}
if (-not (Test-Path -LiteralPath (Join-Path $stagePkg 'Remote Voice-win32-x64\Remote Voice.exe'))) {
    throw "Packaged app missing after packaging step."
}

# ---------------------------------------------------------------------------
# Step 7: Assemble final staging tree (what the installer installs)
# ---------------------------------------------------------------------------
Write-Step 7 "Assemble staging tree"
$appStage = Join-Path $stageDir 'app'
if (Test-Path -LiteralPath $appStage) { Remove-Item -LiteralPath $appStage -Recurse -Force }
Copy-Item -LiteralPath (Join-Path $stagePkg 'Remote Voice-win32-x64') -Destination $appStage -Recurse
# vc_redist rides along for the installer's [Run] step (deleteafterinstall).
Copy-Item -LiteralPath $vcRedist -Destination (Join-Path $stageDir 'vc_redist.x64.exe') -Force

# resources: python311\, engine\ (fresh copy — strip __pycache__), hf_cache\
$resources = Join-Path $appStage 'resources'
New-Item -ItemType Directory -Force -Path $resources | Out-Null
Copy-Item -LiteralPath $stagePython -Destination (Join-Path $resources 'python311') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repoRoot 'engine\engine.py') -Destination (Join-Path $resources 'engine\engine.py') -Force
Copy-Item -LiteralPath (Join-Path $stageHf) -Destination (Join-Path $resources 'hf_cache') -Recurse -Force
Write-Host "  staged: app payload + resources\python311 + resources\engine + resources\hf_cache"

# ---------------------------------------------------------------------------
# Step 8: Locate ISCC and compile the installer
# ---------------------------------------------------------------------------
Write-Step 8 "Compile installer (Inno Setup)"
$isccPaths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $isccPaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "  ISCC.exe not found; attempting winget install JRSoftware.InnoSetup..."
    & winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements 2>&1 |
        ForEach-Object { Write-Host "    | $_" }
    $iscc = $isccPaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $iscc) {
    throw "Inno Setup 6 (ISCC.exe) not found. Install from https://jrsoftware.org/isdl.php and re-run."
}
Write-Host "  ISCC: $iscc"

New-Item -ItemType Directory -Force -Path $distributable | Out-Null
& $iscc "/DSTAGEDIR=$stageDir" "/DOUTPUTDIR=$distributable" (Join-Path $packagingDir 'installer.iss') 2>&1 |
    ForEach-Object { Write-Host "    | $_" }
if ($LASTEXITCODE -ne 0) { throw "ISCC compilation failed (exit $LASTEXITCODE)." }

# ---------------------------------------------------------------------------
# Step 9: Verify output
# ---------------------------------------------------------------------------
Write-Step 9 "Verify output"
$setupExe = Join-Path $distributable 'RemoteVoiceSetup.exe'
if (-not (Test-Path -LiteralPath $setupExe)) { throw "Expected output missing: $setupExe" }
$sizeGB = [math]::Round((Get-Item $setupExe).Length / 1GB, 2)

# Stage size summary
function Get-StageSize([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return 'n/a' }
    $s = (Get-ChildItem -LiteralPath $p -Recurse -File | Measure-Object Length -Sum).Sum
    return "{0:N2} GB" -f ($s / 1GB)
}
Write-Host ""
Write-Host "Stage sizes:"
Write-Host ("  python311 (staged): {0}" -f (Get-StageSize $stagePython))
Write-Host ("  hf_cache:           {0}" -f (Get-StageSize $stageHf))
Write-Host ("  app payload:        {0}" -f (Get-StageSize $appStage))
Write-Host ""
Write-Host "DONE: $setupExe ($sizeGB GB)" -ForegroundColor Green
