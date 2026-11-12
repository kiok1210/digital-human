[CmdletBinding()]
param(
    [ValidateSet("auto", "cpu", "cuda")]
    [string]$Torch = "auto",
    [switch]$SkipModels,
    [ValidateSet("auto", "mamba", "system")]
    [string]$Engine = "auto"
)

# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
# as GBK, which corrupts non-ASCII text and can break parsing.

# "Continue" keeps native tools that log to stderr (git, pip, curl) from
# aborting the script on PowerShell 5.1. Failures are detected by exit code.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# Resolve the project root from this file's real location (scripts\setup.ps1),
# not from the working directory. setup.bat may run a temporary BOM copy.
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not (Test-Path (Join-Path $ProjectRoot "app.py"))) {
    throw "[FAIL] cannot locate the project root from $PSCommandPath. Run setup.bat from the project folder."
}
$ToolsDir = Join-Path $ProjectRoot ".tools"
$MambaRoot = Join-Path $ProjectRoot ".mamba"
$RuntimeDir = Join-Path $ProjectRoot ".runtime"
$Micromamba = Join-Path $ToolsDir "micromamba.exe"
$SadTalkerDir = Join-Path $ProjectRoot "vendor\SadTalker"
$CheckpointDir = Join-Path $SadTalkerDir "checkpoints"
$PiperDir = Join-Path $ProjectRoot "models\piper"
$VendorBin = Join-Path $ProjectRoot "vendor\bin"

# Mainland-friendly mirrors, tried in order. Override with -PipIndexUrl if needed.
$PipMirrors = @(
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://mirrors.cloud.tencent.com/pypi/simple/",
    "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "https://repo.huaweicloud.com/repository/pypi/simple/"
)
$GithubProxies = @(
    "https://ghfast.top/",
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
    ""
)

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "[FAIL] exit code $LASTEXITCODE : $Program $($Arguments -join ' ')"
    }
}

function Invoke-Step {
    param([string]$Program, [string[]]$Arguments, [string]$Label)
    Write-Host "  $Label" -ForegroundColor DarkGray
    # pip writes progress to stderr; PowerShell 5.1 turns that into a
    # NativeCommandError. Capture the merged stream quietly instead.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & $Program @Arguments 2>&1 | Out-String
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    foreach ($line in $output -split "`r?`n") {
        if ($line.Trim()) { Write-Host "    $line" -ForegroundColor DarkGray }
    }
    return $code
}

function Get-RemoteSize {
    param([string]$Url)
    $raw = & curl.exe -sSL -m 25 -o NUL -w "%{http_code} %{size_download}" $Url 2>$null
    if (-not $raw) { return -1 }
    $parts = "$raw".Trim() -split "\s+"
    if ($parts.Count -lt 2) { return -1 }
    if ($parts[0] -ne "200") { return -1 }
    return [int64]$parts[1]
}

function Download-File {
    param([string]$Url, [string]$Destination, [int64]$MinBytes = 1MB)
    if ((Test-Path $Destination) -and ((Get-Item $Destination).Length -gt $MinBytes)) {
        Write-Host "  exists: $(Split-Path -Leaf $Destination)" -ForegroundColor DarkGray
        return $true
    }
    $Part = "$Destination.part"
    Remove-Item -Force -ErrorAction SilentlyContinue $Part
    Write-Host "  download: $(Split-Path -Leaf $Destination)" -ForegroundColor Cyan
    & curl.exe -L --retry 3 --retry-delay 2 --connect-timeout 20 -o $Part $Url 2>&1 |
        ForEach-Object { if ("$_" -match "^\s*\d" -or $_ -match "curl:") { Write-Host "    $_" -ForegroundColor DarkGray } }
    if ((Test-Path $Part) -and ((Get-Item $Part).Length -gt $MinBytes)) {
        Move-Item -Force -LiteralPath $Part -Destination $Destination
        return $true
    }
    Remove-Item -Force -ErrorAction SilentlyContinue $Part
    return $false
}

function Download-WithMirrors {
    param([string]$Path, [string]$Destination, [int64]$MinBytes = 1MB)
    foreach ($prefix in $GithubProxies) {
        $url = "$prefix$Path"
        if (Download-File $url $Destination $MinBytes) { return $true }
        Write-Host "  mirror failed, trying next..." -ForegroundColor DarkYellow
    }
    return $false
}

Write-Host ""
Write-Host "=== Talking Portrait workflow setup ===" -ForegroundColor Cyan
Write-Host "project root: $ProjectRoot"
Write-Host "torch backend: $Torch ; engine: $Engine"
Write-Host ""

New-Item -ItemType Directory -Force -Path $ToolsDir, $MambaRoot, (Split-Path $SadTalkerDir), $PiperDir, $VendorBin | Out-Null

# ---------------------------------------------------------------- runtime ---
$Python = $null
$FfprobePath = $null

if ($Engine -in @("auto", "mamba")) {
    $env:MAMBA_ROOT_PREFIX = $MambaRoot
    if (-not (Test-Path $Micromamba)) {
        Write-Host "[1/6] Fetching micromamba (bundled environment manager)" -ForegroundColor Yellow
        $Archive = Join-Path $ToolsDir "micromamba.tar.bz2"
        $ExtractDir = Join-Path $ToolsDir "micromamba-extract"
        $ok = Download-WithMirrors "mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64" $Archive 3MB
        if (-not $ok) { $ok = Download-File "https://micro.mamba.pm/api/micromamba/win-64/latest" $Archive 3MB }
        if ($ok) {
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $ExtractDir
            New-Item -ItemType Directory -Force -Path $ExtractDir | Out-Null
            Invoke-Checked "tar.exe" @("-xjf", $Archive, "-C", $ExtractDir)
            $FoundMamba = Get-ChildItem -Path $ExtractDir -Filter "micromamba.exe" -Recurse | Select-Object -First 1
            if ($FoundMamba) { Copy-Item -LiteralPath $FoundMamba.FullName -Destination $Micromamba }
        }
        if (-not (Test-Path $Micromamba)) {
            if ($Engine -eq "mamba") { throw "[FAIL] micromamba is unavailable. Check your network or rerun with -Engine system." }
            Write-Host "  micromamba unavailable, falling back to system Python" -ForegroundColor DarkYellow
        }
    }

    if (Test-Path $Micromamba) {
        if (-not (Test-Path (Join-Path $RuntimeDir "python.exe"))) {
            Write-Host "[2/6] Creating Python 3.10 + FFmpeg environment via micromamba" -ForegroundColor Yellow
            Invoke-Checked $Micromamba @("create", "-y", "-p", $RuntimeDir, "-c", "conda-forge", "python=3.10", "ffmpeg")
        }
        else {
            Write-Host "[2/6] Python runtime already present" -ForegroundColor DarkGray
        }
        $Python = Join-Path $RuntimeDir "python.exe"
        $candidateProbe = Join-Path $RuntimeDir "Library\bin\ffprobe.exe"
        if (Test-Path $candidateProbe) { $FfprobePath = $candidateProbe }
    }
}

# System-Python fallback path: no conda, ffmpeg comes from the pip wheel.
if (-not $Python -or -not (Test-Path $Python)) {
    Write-Host "[1/6] Locating a system Python (3.9 - 3.12)" -ForegroundColor Yellow
    $found = $null
    foreach ($candidate in @("python", "python3", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $args = if ($candidate -eq "py") { @("-3", "-c", "import sys;print(sys.executable)") } else { @("-c", "import sys;print(sys.executable)") }
        $exe = & $cmd.Source @args 2>$null | Select-Object -Last 1
        if ($exe -and (Test-Path $exe)) { $found = $exe; break }
    }
    if (-not $found) { throw "[FAIL] no usable system Python found. Install Python 3.10 from python.org and rerun." }
    Write-Host "  base Python: $found" -ForegroundColor DarkGray
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    $VenvDir = Join-Path $RuntimeDir "venv"
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        Invoke-Checked $found @("-m", "venv", $VenvDir)
    }
    if (-not (Test-Path $VenvPython)) { throw "[FAIL] could not create the project virtual environment." }
    $Python = $VenvPython
    $marker = Join-Path $RuntimeDir "system-python.txt"
    Set-Content -Path $marker -Value $Python -Encoding ASCII

    Write-Host "[2/6] Installing FFmpeg via the imageio-ffmpeg wheel" -ForegroundColor Yellow
    $ffOk = $false
    foreach ($mirror in $PipMirrors) {
        Write-Host "  pip index: $mirror" -ForegroundColor DarkGray
        $code = Invoke-Step $Python @("-m", "pip", "install", "-q", "--no-deps", "-i", $mirror, "--timeout", "60", "imageio-ffmpeg") "imageio-ffmpeg"
        if ($code -eq 0) { $ffOk = $true; break }
    }
    if (-not $ffOk) { throw "[FAIL] could not install imageio-ffmpeg from any mirror." }
    $FfBundle = & $Python "-c" "import imageio_ffmpeg,os;print(imageio_ffmpeg.get_ffmpeg_exe())"
    $FfBundle = "$FfBundle".Trim()
    if (-not (Test-Path $FfBundle)) { throw "[FAIL] imageio-ffmpeg did not provide a ffmpeg binary." }
    $FfDest = Join-Path $VendorBin "ffmpeg.exe"
    Copy-Item -Force -LiteralPath $FfBundle -Destination $FfDest
    Write-Host "  ffmpeg -> $FfDest" -ForegroundColor DarkGray
    $FfprobePath = $null
}

# ------------------------------------------------------------- sadtalker ---
if (-not (Test-Path (Join-Path $SadTalkerDir "inference.py"))) {
    Write-Host "[3/6] Fetching SadTalker source" -ForegroundColor Yellow
    $cloned = $false
    if (Get-Command "git.exe" -ErrorAction SilentlyContinue) {
        foreach ($prefix in $GithubProxies) {
            $url = "${prefix}https://github.com/OpenTalker/SadTalker.git"
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $SadTalkerDir
            Write-Host "  git clone via '$prefix'" -ForegroundColor DarkGray
            $cloneOut = & git.exe clone --depth 1 $url $SadTalkerDir 2>&1 | Out-String
            foreach ($line in $cloneOut -split "`r?`n") {
                if ($line.Trim()) { Write-Host "    $line" -ForegroundColor DarkGray }
            }
            if (Test-Path (Join-Path $SadTalkerDir "inference.py")) { $cloned = $true; break }
        }
    }
    if (-not $cloned) {
        Write-Host "  git clone unavailable, downloading zip archive" -ForegroundColor DarkYellow
        $zip = Join-Path $ToolsDir "SadTalker.zip"
        if (-not (Download-WithMirrors "https://github.com/OpenTalker/SadTalker/archive/refs/heads/main.zip" $zip 256KB)) {
            throw "[FAIL] could not download SadTalker. Check your network / proxy settings."
        }
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $SadTalkerDir
        Expand-Archive -Path $zip -DestinationPath $ToolsDir -Force
        $extracted = Get-ChildItem -Path $ToolsDir -Directory -Filter "SadTalker-*" | Select-Object -First 1
        if (-not $extracted) { throw "[FAIL] SadTalker archive did not contain the expected folder." }
        Move-Item -LiteralPath $extracted.FullName -Destination $SadTalkerDir
    }
}
else {
    Write-Host "[3/6] SadTalker source already present" -ForegroundColor DarkGray
}

# ----------------------------------------------------------------- torch ---
if ($Torch -eq "auto") {
    $Torch = "cpu"
    if (Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue) {
        $GpuMemoryText = & nvidia-smi.exe --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1
        $GpuMemory = 0
        if ([int]::TryParse(($GpuMemoryText -replace "[^0-9]", ""), [ref]$GpuMemory) -and $GpuMemory -ge 4096) {
            $Torch = "cuda"
        }
    }
}

$Py3_12 = $false
try {
    $PyVersion = (& $Python "-c" "import sys;print('%d.%d'%sys.version_info[:2])") | Select-Object -Last 1
    $Py3_12 = ("$PyVersion".Trim() -eq "3.12")
} catch { }

Write-Host "[4/6] Installing Python dependencies (torch: $Torch)" -ForegroundColor Yellow
if ($Py3_12) {
    Write-Host "  python 3.12 detected: installing Pytorch 2.2+ and skipping 2023-era pins" -ForegroundColor DarkYellow
}

$PipIndex = @()
foreach ($mirror in $PipMirrors) { $PipIndex += @("-i", $mirror); break }

$code = Invoke-Step $Python (@("-m", "pip", "install", "--upgrade", "pip", "wheel") + $PipIndex) "upgrading pip"
$torchPackages = if ($Py3_12) {
    @("torch", "torchvision", "torchaudio")
} else {
    @("torch==2.0.1", "torchvision==0.15.2", "torchaudio==2.0.2")
}
$torchIndex = if ($Torch -eq "cuda") { "https://download.pytorch.org/whl/cu118" } else { "https://download.pytorch.org/whl/cpu" }
$torchArgs = @("-m", "pip", "install") + $torchPackages + @("--index-url", $torchIndex)
$code = Invoke-Step $Python $torchArgs "installing pytorch"
if ($code -ne 0) {
    Write-Host "  official torch index failed, retrying via mirror" -ForegroundColor DarkYellow
    $code = Invoke-Step $Python (@("-m", "pip", "install", "torch", "torchvision", "torchaudio") + $PipIndex) "installing pytorch (mirror)"
    if ($code -ne 0) { throw "[FAIL] pytorch installation failed." }
}

$reqArgs = @("-m", "pip", "install", "-r", (Join-Path $ProjectRoot "requirements-sadtalker.txt")) + $PipIndex
$code = Invoke-Step $Python $reqArgs "installing sadtalker requirements"
if ($code -ne 0 -and $Py3_12) {
    Write-Host "  strict pins failed on 3.12, installing relaxed set instead" -ForegroundColor DarkYellow
    $relaxed = @(
        "numpy", "face-alignment", "imageio", "imageio-ffmpeg", "librosa", "numba",
        "resampy", "pydub", "scipy", "kornia", "tqdm", "yacs", "PyYAML", "joblib",
        "scikit-image", "facexlib", "gfpgan", "av", "safetensors", "piper-tts", "msgpack"
    )
    $code = Invoke-Step $Python (@("-m", "pip", "install") + $relaxed + $PipIndex) "installing relaxed requirements"
    if ($code -ne 0) { throw "[FAIL] dependency installation failed." }
}

# ---------------------------------------------------------------- models ---
if (-not $SkipModels) {
    Write-Host "[5/6] Downloading checkpoints and the Piper Chinese voice" -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $CheckpointDir | Out-Null
    $assets = @(
        @{ Name = "mapping_00109-model.pth.tar"; Size = 2MB },
        @{ Name = "mapping_00229-model.pth.tar"; Size = 2MB },
        @{ Name = "SadTalker_V0.0.2_256.safetensors"; Size = 20MB }
    )
    foreach ($asset in $assets) {
        $target = Join-Path $CheckpointDir $asset.Name
        $remote = "OpenTalker/SadTalker/releases/download/v0.0.2-rc/$($asset.Name)"
        if (-not (Download-WithMirrors $remote $target $asset.Size)) {
            throw "[FAIL] could not download $($asset.Name). Re-run setup later or copy it into checkpoints manually."
        }
    }
    $code = Invoke-Step $Python @("-m", "piper.download_voices", "--download-dir", $PiperDir, "zh_CN-huayan-medium") "downloading piper voice"
    if ($code -ne 0) {
        Write-Host "  piper.download_voices failed, trying direct HuggingFace download" -ForegroundColor DarkYellow
        $voiceBase = "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium"
        foreach ($file in @("zh_CN-huayan-medium.onnx", "zh_CN-huayan-medium.onnx.json")) {
            if (-not (Download-File "$voiceBase/$file" (Join-Path $PiperDir $file) 1KB)) {
                throw "[FAIL] could not download $file."
            }
        }
    }
    # Optional second voice for the UI picker (small footprint).
    $lightVoice = "zh_CN-huayan-x_low"
    $lightOnnx = Join-Path $PiperDir "$lightVoice.onnx"
    if (-not (Test-Path $lightOnnx)) {
        $code = Invoke-Step $Python @("-m", "piper.download_voices", "--download-dir", $PiperDir, $lightVoice) "downloading piper light voice"
        if ($code -ne 0) {
            Write-Host "  light voice download failed, trying direct HuggingFace download" -ForegroundColor DarkYellow
            $voiceBase = "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/x_low"
            foreach ($file in @("$lightVoice.onnx", "$lightVoice.onnx.json")) {
                if (-not (Download-File "$voiceBase/$file" (Join-Path $PiperDir $file) 1KB)) {
                    Write-Host "  optional voice $file skipped (medium voice is enough to run)" -ForegroundColor DarkYellow
                }
            }
        }
    }
    # SadTalker also needs its auxiliary face/eye models on first run.
    Write-Host "  note: SadTalker will download auxiliary models on the first generation" -ForegroundColor DarkGray
}
else {
    Write-Host "[5/6] Skipped model download (-SkipModels)" -ForegroundColor DarkGray
}

# ----------------------------------------------------------------- verify ---
Write-Host "[6/6] Verifying the runtime" -ForegroundColor Yellow
$code = Invoke-Step $Python @("-c", "import torch;print('torch', torch.__version__, 'cuda', torch.cuda.is_available())") "torch"
$code = Invoke-Step $Python @("-c", "import piper;print('piper ok')") "piper"

$ff = Join-Path $RuntimeDir "Library\bin\ffmpeg.exe"
if (-not (Test-Path $ff)) { $ff = Join-Path $VendorBin "ffmpeg.exe" }
if (-not (Test-Path $ff)) { throw "[FAIL] no ffmpeg binary found." }
$filters = & $ff -hide_banner -filters 2>&1 | Out-String
foreach ($needle in @("xfade", "acrossfade", "amix", "\bass\s")) {
    if ($filters -notmatch $needle) { throw "[FAIL] ffmpeg is missing the '$needle' filter." }
}
Write-Host "  ffmpeg: $ff" -ForegroundColor DarkGray

$probeConfigured = $false
if ($FfprobePath -and (Test-Path $FfprobePath)) { $probeConfigured = $true }
if ($probeConfigured) {
    Set-Content -Path (Join-Path $RuntimeDir "ffprobe.txt") -Value $FfprobePath -Encoding ASCII
    Write-Host "  ffprobe: $FfprobePath" -ForegroundColor DarkGray
}
else {
    Write-Host "  ffprobe: not bundled, app.py will fall back to ffmpeg" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Setup finished. Run start.bat to launch the workflow." -ForegroundColor Green
Write-Host "torch: $Torch ; runtime: $Python" -ForegroundColor Green
