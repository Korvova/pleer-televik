# Сборка: exe (PyInstaller) + установщик (Inno Setup).  pwsh installer\build.ps1 [-Version 1.0.0]
param([string]$Version = "1.0.0")
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$root = Split-Path $here -Parent
Set-Location $here

python make_icon.py icon.ico

# чистое окружение: в exe попадает только то, что нужно программе
$venv = "$here\.venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) { python -m venv $venv }
$py = "$venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip pyinstaller websockets

& $py -m PyInstaller --noconfirm --clean --onedir --console --name PleerPlixus `
  --icon "$here\icon.ico" `
  --add-data "$root\player.html;." `
  --distpath "$here\dist" --workpath "$here\build" --specpath "$here\build" `
  "$root\plixus_recorder.py"
if ($LASTEXITCODE) { throw "PyInstaller завершился с ошибкой" }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Не найден Inno Setup 6 (winget install JRSoftware.InnoSetup)" }
& $iscc "/DAppVersion=$Version" "$here\PleerPlixus.iss"
if ($LASTEXITCODE) { throw "Inno Setup завершился с ошибкой" }
Get-Item "$here\Output\PleerPlixus-Setup-$Version.exe" | Select-Object FullName, Length
