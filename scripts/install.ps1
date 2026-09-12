# ==============================================================================
#  AI Friend — Automated Windows PowerShell Installer
#  Usage:
#    irm https://raw.githubusercontent.com/PALabs-v1/AI_friend/main/scripts/install.ps1 | iex
# ==============================================================================

[CmdletBinding()]
param (
    [string]$TargetDir = "$HOME\AI_friend",
    [string]$Model = "llama3.2:3b",
    [switch]$Dev,
    [switch]$NoStart,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Banner {
    Write-Host @"
      _    ___   _____ ____  ___ _____ _   _ ____
     / \  |_ _| |  ___|  _ \|_ _| ____| \ | |  _ \
    / _ \  | |  | |_  | |_) || ||  _| |  \| | | | |
   / ___ \ | |  |  _| |  _ < | || |___| |\  | |_| |
  /_/   \_\___| |_|   |_| \_\___|_____|_| \_|____/
"@ -ForegroundColor Cyan
    Write-Host "  An embodied, local-first lifelong companion on your own hardware.`n" -ForegroundColor DarkGray
}

Write-Banner

Write-Host "==> Platform: Windows (PowerShell / Docker Desktop)" -ForegroundColor Cyan

# 1. Prerequisite Validation
Write-Host "==> Checking prerequisites..." -ForegroundColor Cyan

# Check Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warning "Git is not installed. Attempting to install via winget..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Git.Git -e --source winget
    } else {
        Write-Error "Please install Git from https://git-scm.com/download/win and re-run."
        return
    }
}
Write-Host "[OK] Git is available" -ForegroundColor Green

# Check Python 3.11+
$pythonCmd = $null
foreach ($cmd in @("python3.13", "python3.12", "python3.11", "python", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $pythonCmd = $cmd
        break
    }
}

if (-not $pythonCmd) {
    Write-Warning "Python 3.11+ not found. Attempting to install via winget..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Python.Python.3.12 -e --source winget
        $pythonCmd = "python"
    } else {
        Write-Error "Please install Python 3.11+ from https://python.org and add to PATH."
        return
    }
}
Write-Host "[OK] Python is available ($pythonCmd)" -ForegroundColor Green

# Check Docker
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Warning "Docker Desktop is required to run the 9-agent signal mesh."
    Write-Host "Please install Docker Desktop: https://www.docker.com/products/docker-desktop" -ForegroundColor Yellow
}

# Check Ollama
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Warning "Ollama is recommended for local LLM inference."
    Write-Host "Download Ollama for Windows: https://ollama.com/download/windows" -ForegroundColor Yellow
}

if ($DryRun) {
    Write-Host "`n[OK] Dry run complete! System meets Windows prerequisites." -ForegroundColor Green
    return
}

# 2. Download Runtime Bundle vs Full Monorepo
New-Item -ItemType Directory -Force -Path (Split-Path $TargetDir) | Out-Null

if ($Dev) {
    Write-Host "==> Downloading full developer repository into $TargetDir..." -ForegroundColor Cyan
    if (Test-Path "$TargetDir\.git") {
        Set-Location $TargetDir
        git pull --ff-only
    } else {
        git clone "https://github.com/PALabs-v1/AI_friend.git" $TargetDir
        Set-Location $TargetDir
    }
} else {
    Write-Host "==> Downloading lightweight runtime bundle (~4.3 MB)..." -ForegroundColor Cyan
    $zipUrl = "https://raw.githubusercontent.com/PALabs-v1/AI_friend/main/dist/ai-friend-runtime.zip"
    $tmpZip = Join-Path $env:TEMP "ai-friend-runtime.zip"
    $tmpExtract = Join-Path $env:TEMP "ai-friend-runtime-extract"

    $downloaded = $false
    try {
        Invoke-WebRequest -Uri $zipUrl -OutFile $tmpZip -UseBasicParsing
        $downloaded = $true
    } catch {
        Write-Warning "Runtime bundle download failed ($($_.Exception.Message)); falling back to shallow source checkout."
    }

    if ($downloaded) {
        if (Test-Path $tmpExtract) { Remove-Item $tmpExtract -Recurse -Force }
        Expand-Archive -Path $tmpZip -DestinationPath $tmpExtract -Force
        New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
        Copy-Item -Path (Join-Path $tmpExtract "ai-friend-runtime\*") -Destination $TargetDir -Recurse -Force
        Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
        Remove-Item $tmpExtract -Recurse -Force -ErrorAction SilentlyContinue
        Set-Location $TargetDir
    } else {
        Write-Host "==> Fetching shallow runtime checkout..." -ForegroundColor Cyan
        git clone --depth 1 "https://github.com/PALabs-v1/AI_friend.git" $TargetDir
        Set-Location $TargetDir
    }
}

# 3. Environment Setup — interactive wizard when possible, matching install.sh
if (-not (Test-Path ".env")) {
    $wizardPath = "scripts\bootstrap\env_wizard.py"
    if ([Environment]::UserInteractive -and (Test-Path $wizardPath)) {
        Write-Host "==> Launching interactive environment setup wizard..." -ForegroundColor Cyan
        & $pythonCmd $wizardPath
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path ".env")) {
            Write-Warning "Setup wizard did not complete; falling back to .env.example."
            Copy-Item ".env.example" ".env" -Force
        }
    } elseif (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
    } else {
        New-Item -ItemType File -Path ".env" -Force | Out-Null
    }
    Write-Host "[OK] Configured .env" -ForegroundColor Green
}

# Set active chat model in .env if explicitly passed
if ($Model -and $Model -ne "llama3.2:3b") {
    $envContent = Get-Content ".env" -Raw
    if ($envContent -match "(?m)^LLM_CHAT_MODEL=.*$") {
        $envContent = $envContent -replace "(?m)^LLM_CHAT_MODEL=.*$", "LLM_CHAT_MODEL=$Model"
    } else {
        $envContent += "`nLLM_CHAT_MODEL=$Model`n"
    }
    Set-Content ".env" -Value $envContent -NoNewline
}

# 4. Python Virtual Environment
if (-not (Test-Path ".venv")) {
    Write-Host "==> Creating Python virtual environment..." -ForegroundColor Cyan
    & $pythonCmd -m venv .venv
}

Write-Host "==> Installing Python dependencies..." -ForegroundColor Cyan
& ".\.venv\Scripts\pip.exe" install --upgrade pip | Out-Null
if (Test-Path "backend\pyproject.toml") {
    & ".\.venv\Scripts\pip.exe" install -e backend | Out-Null
}

# 5. Provision default voice
if (Test-Path "backend\scripts\bootstrap\ensure_default_voice_sample.py") {
    & ".\.venv\Scripts\python.exe" backend\scripts\bootstrap\ensure_default_voice_sample.py
}

# 6. Install Global `friend` CLI ------------------------------------------------
Write-Host "==> Installing global 'friend' CLI command..." -ForegroundColor Cyan

$binDir = "$HOME\.local\bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

$friendCmd = @"
@echo off
setlocal
set "REPO_ROOT=$TargetDir"
set "PYEXE=%REPO_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=$pythonCmd"
"%PYEXE%" "%REPO_ROOT%\scripts\friend_cli.py" %*
"@
Set-Content -Path (Join-Path $binDir "friend.cmd") -Value $friendCmd -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$binDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
    $env:Path = "$env:Path;$binDir"
    Write-Host "[OK] Added $binDir to your user PATH (new terminals will pick it up)" -ForegroundColor Green
}
Write-Host "[OK] Global command installed: $binDir\friend.cmd" -ForegroundColor Green

# 7. Summary & Launcher
Write-Host @"

═════════════════════════════════════════════════════════════════
             AI FRIEND INSTALLATION COMPLETE!
═════════════════════════════════════════════════════════════════
Location:  $TargetDir
Commands:
  friend start                Start the 9-agent cognitive mesh
  friend model list           Browse, pull, and switch LLM models
  friend talk                 Open terminal conversation REPL
  friend status                Inspect live agent health and RAM
  .\start.bat                 Start the complete system directly
─────────────────────────────────────────────────────────────────
"@ -ForegroundColor Green

if (-not $NoStart) {
    $startNow = Read-Host "Would you like to launch AI Friend now? (Y/n)"
    if ($startNow -eq "" -or $startNow -match "^[Yy]") {
        & ".\start.ps1"
    }
}
