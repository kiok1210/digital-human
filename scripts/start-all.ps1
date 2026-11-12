[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$RequireFish,
    [switch]$CheckOnly,
    [int]$FishTimeoutSeconds = 1200
)

# Keep this file ASCII-only for Windows PowerShell 5.1 compatibility.
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    foreach ($raw in Get-Content -LiteralPath $Path) {
        $line = "$raw".Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { continue }
        $pair = $line.Split("=", 2)
        $name = $pair[0].Trim()
        $value = $pair[1].Trim().Trim('"').Trim("'")
        if ($name -and -not [Environment]::GetEnvironmentVariable($name, "Process")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

function Test-HttpOk {
    param([string]$Url, [int]$TimeoutSeconds = 2)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    }
    catch { return $false }
}

function Get-FishHealthUrl {
    param([string]$TtsUrl)
    $uri = [Uri]$TtsUrl
    $path = $uri.AbsolutePath
    $slash = $path.LastIndexOf("/")
    if ($slash -lt 0) { $healthPath = "/v1/health" }
    else { $healthPath = $path.Substring(0, $slash) + "/health" }
    return "$($uri.Scheme)://$($uri.Authority)$healthPath"
}

function Get-AppPython {
    $candidates = @(
        (Join-Path $ProjectRoot ".runtime\python.exe"),
        (Join-Path $ProjectRoot ".runtime\venv\Scripts\python.exe")
    )
    $marker = Join-Path $ProjectRoot ".runtime\system-python.txt"
    if (Test-Path -LiteralPath $marker) {
        $candidates += (Get-Content -Raw -LiteralPath $marker).Trim()
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Start-NativeFish {
    param([string]$FishDir)
    $pythonCandidates = @()
    if ($env:FISH_PYTHON) { $pythonCandidates += $env:FISH_PYTHON }
    $pythonCandidates += @(
        (Join-Path $ProjectRoot ".fish-runtime\python.exe"),
        (Join-Path $FishDir ".venv\Scripts\python.exe"),
        (Join-Path $FishDir "venv\Scripts\python.exe")
    )
    $fishPython = $pythonCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $fishPython) { return $false }

    $checkpointCandidates = @()
    if ($env:FISH_CHECKPOINT_DIR) { $checkpointCandidates += $env:FISH_CHECKPOINT_DIR }
    $checkpointCandidates += @(
        (Join-Path $FishDir "checkpoints\fish-speech-1.5"),
        (Join-Path $FishDir "checkpoints\s2-pro")
    )
    $checkpoint = $checkpointCandidates | Where-Object {
        $_ -and (Test-Path -LiteralPath $_) -and (
            (Test-Path -LiteralPath (Join-Path $_ "codec.pth")) -or
            (Test-Path -LiteralPath (Join-Path $_ "firefly-gan-vq-fsq-8x1024-21hz-generator.pth"))
        )
    } | Select-Object -First 1
    if (-not $checkpoint) { return $false }

    $s2Codec = Join-Path $checkpoint "codec.pth"
    $legacyCodec = Join-Path $checkpoint "firefly-gan-vq-fsq-8x1024-21hz-generator.pth"
    $codec = if (Test-Path -LiteralPath $s2Codec) { $s2Codec } else { $legacyCodec }
    if (-not (Test-Path -LiteralPath (Join-Path $FishDir "tools\api_server.py"))) {
        return $false
    }

    $device = if ($env:FISH_DEVICE) { $env:FISH_DEVICE } else { "cpu" }
    $fishRuntime = Split-Path -Parent $fishPython
    $fishPathParts = @(
        (Join-Path $fishRuntime "Library\bin"),
        (Join-Path $fishRuntime "Scripts")
    ) | Where-Object { Test-Path -LiteralPath $_ }
    if ($fishPathParts) { $env:PATH = (($fishPathParts -join ";") + ";" + $env:PATH) }

    $arguments = @(
        "tools/api_server.py",
        "--llama-checkpoint-path", $checkpoint,
        "--decoder-checkpoint-path", $codec,
        "--device", $device,
        "--listen", "127.0.0.1:8080"
    )
    if ($env:FISH_LOCAL_API_KEY) { $arguments += @("--api-key", $env:FISH_LOCAL_API_KEY) }
    if ($device -ne "cpu" -and $env:FISH_USE_HALF -ne "0") { $arguments += "--half" }

    Start-Process -FilePath $fishPython -ArgumentList $arguments -WorkingDirectory $FishDir `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDir "fish-speech.out.log") `
        -RedirectStandardError (Join-Path $LogDir "fish-speech.err.log") | Out-Null
    return $true
}

function Start-WslFish {
    if (-not $env:FISH_WSL_PATH) { return $false }
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { return $false }
    $distroArgs = @()
    if ($env:FISH_WSL_DISTRO) { $distroArgs = @("-d", $env:FISH_WSL_DISTRO) }
    $checkpoint = if ($env:FISH_WSL_CHECKPOINT_DIR) { $env:FISH_WSL_CHECKPOINT_DIR } else { "$($env:FISH_WSL_PATH.TrimEnd('/'))/checkpoints/s2-pro" }
    $apiKeyArg = if ($env:FISH_LOCAL_API_KEY) { " --api-key '$($env:FISH_LOCAL_API_KEY)'" } else { "" }
    $halfArg = if ($env:FISH_USE_HALF -eq "0") { "" } else { " --half" }
    $command = "cd '$($env:FISH_WSL_PATH)' && exec python tools/api_server.py --llama-checkpoint-path '$checkpoint' --decoder-checkpoint-path '$checkpoint/codec.pth' --listen 0.0.0.0:8080$halfArg$apiKeyArg"
    $arguments = $distroArgs + @("--", "bash", "-lc", $command)
    Start-Process -FilePath "wsl.exe" -ArgumentList $arguments -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "fish-speech.out.log") `
        -RedirectStandardError (Join-Path $LogDir "fish-speech.err.log") | Out-Null
    return $true
}

Import-DotEnv (Join-Path $ProjectRoot ".env")
$fishUrl = if ($env:FISH_LOCAL_URL) { $env:FISH_LOCAL_URL } else { "http://127.0.0.1:8080/v1/tts" }
$fishHealth = Get-FishHealthUrl $fishUrl
$appHealth = "http://127.0.0.1:7860/api/ping"
$nativeDir = if ($env:FISH_SPEECH_DIR) { $env:FISH_SPEECH_DIR } else { Join-Path $ProjectRoot "vendor\fish-speech" }

Write-Host "Fish Speech health: $fishHealth" -ForegroundColor DarkGray
Write-Host "Studio health:      $appHealth" -ForegroundColor DarkGray
if ($CheckOnly) {
    Write-Host "Fish ready:  $(Test-HttpOk $fishHealth)"
    Write-Host "Studio ready: $(Test-HttpOk $appHealth)"
    Write-Host "Native Fish directory: $nativeDir"
    exit 0
}

$fishReady = Test-HttpOk $fishHealth
if ($fishReady) {
    Write-Host "[1/2] Fish Speech is already running." -ForegroundColor Green
}
else {
    Write-Host "[1/2] Starting Fish Speech..." -ForegroundColor Yellow
    Write-Host "First CPU startup loads the local model and can take 10-15 minutes. Keep this window open." -ForegroundColor DarkGray
    $started = $false
    if (Test-Path -LiteralPath $nativeDir) { $started = Start-NativeFish $nativeDir }
    if (-not $started) { $started = Start-WslFish }

    if ($started) {
        $deadline = (Get-Date).AddSeconds([Math]::Max(30, $FishTimeoutSeconds))
        do {
            Start-Sleep -Seconds 2
            $fishReady = Test-HttpOk $fishHealth
            if ($fishReady) { break }
            Write-Host "." -NoNewline
        } while ((Get-Date) -lt $deadline)
        Write-Host ""
    }

    if ($fishReady) {
        Write-Host "Fish Speech is ready." -ForegroundColor Green
    }
    else {
        Write-Warning "Fish Speech was not started. Configure FISH_SPEECH_DIR/FISH_PYTHON or FISH_WSL_PATH in .env. The studio will use Piper."
        if ($RequireFish) { throw "Fish Speech is required but unavailable." }
    }
}

if ($fishReady -and -not $env:DOUYIN_TTS_ENGINE) { $env:DOUYIN_TTS_ENGINE = "fish_local" }

if (Test-HttpOk $appHealth) {
    Write-Host "[2/2] Talking-portrait studio is already running." -ForegroundColor Green
}
else {
    Write-Host "[2/2] Starting talking-portrait studio..." -ForegroundColor Yellow
    $appPython = Get-AppPython
    if (-not $appPython) { throw "Project Python runtime is missing. Run setup.bat first." }
    Start-Process -FilePath $appPython -ArgumentList @("app.py", "--no-browser", "--port", "7860") `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "studio.out.log") `
        -RedirectStandardError (Join-Path $LogDir "studio.err.log") | Out-Null
    $deadline = (Get-Date).AddSeconds(60)
    do {
        Start-Sleep -Milliseconds 500
        if (Test-HttpOk $appHealth) { break }
    } while ((Get-Date) -lt $deadline)
    if (-not (Test-HttpOk $appHealth)) { throw "Studio failed to start. Check logs/studio.err.log." }
    Write-Host "Studio is ready." -ForegroundColor Green
}

Write-Host "Open http://127.0.0.1:7860" -ForegroundColor Cyan
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:7860" }
