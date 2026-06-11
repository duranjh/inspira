param(
    [string]$AppPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'app'),
    [switch]$SkipRustupSync
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Require-File {
    param(
        [string]$Path,
        [string]$Message
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw $Message
    }
}

function Import-BatchEnvironment {
    param(
        [string]$BatchPath,
        [string[]]$Arguments = @()
    )

    $argumentText = ($Arguments -join ' ')
    $command = if ([string]::IsNullOrWhiteSpace($argumentText)) {
        "`"$BatchPath`" && set"
    }
    else {
        "`"$BatchPath`" $argumentText && set"
    }

    $output = & cmd.exe /d /s /c $command
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to import environment from $BatchPath"
    }

    foreach ($line in $output) {
        if ($line -match '^(.*?)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
        }
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
$cargoExe = Join-Path $cargoBin 'cargo.exe'
$rustupExe = Join-Path $cargoBin 'rustup.exe'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'

Require-File -Path $AppPath -Message "Planning Studio app path not found: $AppPath"
Require-File -Path $cargoExe -Message "cargo.exe not found at $cargoExe. Install Rust with rustup first."
Require-File -Path $rustupExe -Message "rustup.exe not found at $rustupExe. Install Rust with rustup first."
Require-File -Path $vswhere -Message "vswhere.exe not found. Install Visual Studio Build Tools first."

if ($env:Path -notlike "*$cargoBin*") {
    $env:Path = "$cargoBin;$env:Path"
}

if (-not $SkipRustupSync) {
    Write-Step 'Ensuring Rust MSVC toolchain'
    & $rustupExe default stable-msvc
    & $rustupExe target add x86_64-pc-windows-msvc
}

Write-Step 'Locating Visual Studio C++ toolchain'
$vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ([string]::IsNullOrWhiteSpace($vsPath)) {
    throw 'Visual Studio Build Tools with the VC++ workload were not found.'
}

$vsDevCmd = Join-Path $vsPath 'Common7\Tools\VsDevCmd.bat'
Require-File -Path $vsDevCmd -Message "Visual Studio developer command script not found: $vsDevCmd"

Write-Step 'Loading Visual Studio developer shell'
Import-BatchEnvironment -BatchPath $vsDevCmd -Arguments @('-arch=amd64', '-host_arch=amd64')

$linkPath = & where.exe link 2>&1
if ($LASTEXITCODE -ne 0 -or -not $linkPath) {
    throw 'link.exe is still unavailable after loading the Visual Studio developer shell.'
}

Write-Step 'Building Planning Studio desktop bundles'
Push-Location $AppPath
try {
    & npm run tauri:build
}
finally {
    Pop-Location
}
