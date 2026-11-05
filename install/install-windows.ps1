# Installe Meshpass sur Windows : Python, dépendances, lanceur du bureau.
$ErrorActionPreference = "Stop"

$Source = Split-Path -Parent $PSScriptRoot
$Dest = "C:\Apps\meshpass"
$Port = 8091
if ($env:MESH_PORT) { $Port = [int]$env:MESH_PORT }

function Find-Python {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "C:\Python314\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $exe = & py -3 -c "import sys; print(sys.executable)"
        if ($exe) { return $exe.Trim() }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }
    return $null
}

Write-Host "Installation Meshpass pour Windows"
$python = Find-Python
if (-not $python) {
    Write-Host "Python est absent. Installation avec winget..."
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    $python = Find-Python
}
if (-not $python) {
    throw "Python 3 est introuvable. Installez-le puis relancez ce script."
}
Write-Host "Python : $python"

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
if ((Resolve-Path $Source).Path -ne (Resolve-Path $Dest).Path) {
    Write-Host "Copie du projet vers $Dest"
    & robocopy $Source $Dest /E /NFL /NDL /NJH /NJS /XD data __pycache__ .venv .git | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "La copie du projet a échoué ($LASTEXITCODE)." }
    if (-not (Test-Path "$Dest\data") -and (Test-Path "$Source\data")) {
        & robocopy "$Source\data" "$Dest\data" /E /NFL /NDL /NJH /NJS | Out-Null
    }
}

$venvPython = Join-Path $Dest ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Création de l'environnement Python"
    & $python -m venv (Join-Path $Dest ".venv")
}
Write-Host "Installation des bibliothèques"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $Dest "requirements.txt")

$bat = @"
@echo off
title Passerelle Mesh
cd /d $Dest
echo.
echo  Passerelle Mesh
echo  Adresse : http://127.0.0.1:$Port
echo  Mot de passe initial : meshpass
echo  Fermez cette fenetre pour arreter.
echo.
set MESH_PORT=$Port
set MESH_OPEN_BROWSER=1
"$venvPython" -u run.py
echo.
pause
"@
$desktop = [Environment]::GetFolderPath("Desktop")
$batPath = Join-Path $desktop "Passerelle Mesh.bat"
Set-Content -Path $batPath -Value $bat -Encoding ASCII

$menuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Force -Path $menuDir | Out-Null
$lnkPath = Join-Path $menuDir "Passerelle Mesh.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $batPath
$shortcut.WorkingDirectory = $Dest
$shortcut.Description = "Lancer la passerelle Meshpass"
$shortcut.Save()
$deskLnk = Join-Path $desktop "Passerelle Mesh.lnk"
$shortcut2 = $shell.CreateShortcut($deskLnk)
$shortcut2.TargetPath = $batPath
$shortcut2.WorkingDirectory = $Dest
$shortcut2.Description = "Lancer la passerelle Meshpass"
$shortcut2.Save()

Write-Host ""
Write-Host "Meshpass est installé dans $Dest"
Write-Host "Page : http://127.0.0.1:$Port"
Write-Host "Mot de passe initial : meshpass"
Write-Host "Lanceur : $batPath"
