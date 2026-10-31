# Gera o Dock.exe e o instalador (Dock-Setup.exe) NO SEU PC, sem GitHub. Rode:  .\build.ps1
# Precisa de: Python 3.10+  e  Inno Setup 6 (https://jrsoftware.org/isdl.php)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$linha = Select-String -Path dock.py -Pattern '^VERSAO = "(.+?)"' | Select-Object -First 1
$versao = $linha.Matches[0].Groups[1].Value
Write-Host "Versao: $versao"

python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm --clean --windowed --name Dock --icon icon.ico dock.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou" }

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 nao encontrado. Instale em https://jrsoftware.org/isdl.php e rode de novo." }

& $iscc "/DAppVersion=$versao" installer\Dock.iss
if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar o instalador" }

Write-Host ""
Write-Host "Pronto: $PSScriptRoot\installer\Output\Dock-Setup.exe"
