<#
.SYNOPSIS
    Compile Dosoft puis l'installe comme application Windows
    (C:\Program Files\Dosoft + raccourcis Menu Demarrer et Bureau).

.DESCRIPTION
    1. Compile dist\Dosoft.exe avec PyInstaller (.venv cree si absent).
    2. Se relance en administrateur (une invite UAC) pour copier l'exe et
       ses ressources dans Program Files et creer les raccourcis.
    Le settings.json deja installe est conserve d'une version a l'autre.

.EXAMPLE
    .\deploy.ps1              # compile + installe
    .\deploy.ps1 -Run         # compile + installe + lance l'application
    .\deploy.ps1 -SkipBuild   # installe le dernier build sans recompiler
    .\deploy.ps1 -Uninstall   # retire l'application et ses raccourcis
#>
param(
    [switch]$SkipBuild,
    [switch]$Run,
    [switch]$Uninstall,
    [switch]$NoDesktopShortcut,
    # Parametres internes, utilises par la relance en administrateur
    [string]$ElevatedAction,
    [string]$StartMenuDir,
    [string]$DesktopDir,
    [string]$LogFile
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$AppName = 'Dosoft'
$InstallDir = Join-Path $env:ProgramFiles $AppName
# Ressources lues relativement au dossier de travail par l'application
$Assets = @('skin', 'sounds', 'logo.ico')

function Stop-Dosoft {
    Get-Process -Name $AppName -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 800
}

function Get-ShortcutDirs {
    @($StartMenuDir, $DesktopDir) | Where-Object { $_ }
}

function Install-Dosoft {
    $exe = Join-Path $Root "dist\$AppName.exe"
    if (-not (Test-Path $exe)) { throw "Build introuvable : $exe" }

    Stop-Dosoft
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item $exe $InstallDir -Force
    foreach ($asset in $Assets) {
        $dest = Join-Path $InstallDir $asset
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        Copy-Item (Join-Path $Root $asset) $InstallDir -Recurse -Force
    }

    # Premiere installation : reprend les reglages faits en testant depuis dist\
    $settings = Join-Path $InstallDir 'settings.json'
    $distSettings = Join-Path $Root 'dist\settings.json'
    if (-not (Test-Path $settings) -and (Test-Path $distSettings)) {
        Copy-Item $distSettings $settings
    }

    $shell = New-Object -ComObject WScript.Shell
    foreach ($dir in Get-ShortcutDirs) {
        $lnk = $shell.CreateShortcut((Join-Path $dir "$AppName.lnk"))
        $lnk.TargetPath = Join-Path $InstallDir "$AppName.exe"
        $lnk.WorkingDirectory = $InstallDir
        $lnk.IconLocation = Join-Path $InstallDir 'logo.ico'
        $lnk.Description = 'Gestionnaire multi-compte Dofus'
        $lnk.Save()
    }
    Write-Host "Installe dans $InstallDir"

    if ($Run) {
        Start-Process -FilePath (Join-Path $InstallDir "$AppName.exe") -WorkingDirectory $InstallDir
    }
}

function Uninstall-Dosoft {
    Stop-Dosoft
    if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
    foreach ($dir in Get-ShortcutDirs) {
        $lnk = Join-Path $dir "$AppName.lnk"
        if (Test-Path $lnk) { Remove-Item $lnk -Force }
    }
    Write-Host "Dosoft desinstalle"
}

# --- Partie executee en administrateur ---
if ($ElevatedAction) {
    $code = 0
    try {
        $output = & {
            if ($ElevatedAction -eq 'install') { Install-Dosoft } else { Uninstall-Dosoft }
        } *>&1
    } catch {
        $output = "ERREUR : $_"
        $code = 1
    }
    if ($LogFile) { $output | Out-File -FilePath $LogFile -Encoding utf8 }
    exit $code
}

# --- Partie executee en utilisateur normal ---
function Get-VenvPython {
    $venvPy = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path $venvPy) { return $venvPy }

    Write-Host 'Creation de .venv...'
    $basePy = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (-not (Test-Path $basePy)) { throw 'Python 3.12 introuvable (winget install Python.Python.3.12)' }
    & $basePy -m venv (Join-Path $Root '.venv')
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r (Join-Path $Root 'requirements.txt') pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'Installation des dependances echouee' }
    return $venvPy
}

if (-not $Uninstall -and -not $SkipBuild) {
    $py = Get-VenvPython
    Write-Host 'Compilation PyInstaller...'
    Push-Location $Root
    try {
        $ErrorActionPreference = 'Continue'   # PyInstaller ecrit ses logs sur stderr
        & $py -m PyInstaller --noconfirm --log-level WARN Dosoft.spec
        $buildCode = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
    } finally { Pop-Location }
    if ($buildCode -ne 0) { throw "Compilation echouee (code $buildCode)" }
    # Permet aussi de lancer dist\Dosoft.exe directement pour tester
    foreach ($asset in $Assets) {
        Copy-Item (Join-Path $Root $asset) (Join-Path $Root 'dist') -Recurse -Force
    }
}

$log = Join-Path $env:TEMP 'dosoft_deploy.log'
if (Test-Path $log) { Remove-Item $log -Force }
$action = if ($Uninstall) { 'uninstall' } else { 'install' }
$argList = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', "`"$PSCommandPath`"",
    '-ElevatedAction', $action,
    '-StartMenuDir', "`"$([Environment]::GetFolderPath('Programs'))`"",
    '-LogFile', "`"$log`""
)
if (-not $NoDesktopShortcut) {
    $argList += @('-DesktopDir', "`"$([Environment]::GetFolderPath('Desktop'))`"")
}
if ($Run) { $argList += '-Run' }

Write-Host 'Installation (invite UAC)...'
# WaitForExit plutot que -Wait : -Wait suit aussi les processus enfants (dont Dosoft lance par -Run)
$proc = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -PassThru -ArgumentList $argList
$proc.WaitForExit()
if (Test-Path $log) { Get-Content $log }
if ($proc.ExitCode -ne 0) { throw "Installation echouee (code $($proc.ExitCode))" }
