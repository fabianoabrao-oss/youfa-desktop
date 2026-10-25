$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $Python) {
    throw 'Python 3 não encontrado no PATH.'
}

$Venv = Join-Path $Root '.venv'
if (-not (Test-Path -LiteralPath $Venv)) {
    & $Python -m venv $Venv
}
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependencias.' }
$Ffmpeg = & $VenvPython -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
if ($LASTEXITCODE -ne 0) { throw 'Falha ao localizar FFmpeg.' }
if (-not (Test-Path -LiteralPath $Ffmpeg)) { throw 'FFmpeg nao encontrado.' }
$Runtime = Join-Path $Root 'runtime'
New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
$BundledFfmpeg = Join-Path $Runtime 'ffmpeg.exe'
Copy-Item -LiteralPath $Ffmpeg -Destination $BundledFfmpeg -Force
$NodeSource = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
if (-not $NodeSource) { throw 'Node.js nao encontrado no PATH.' }
$BundledNode = Join-Path $Runtime 'node.exe'
Copy-Item -LiteralPath $NodeSource -Destination $BundledNode -Force
& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name 'YouFA' `
    --icon (Join-Path $Root 'assets\youfa.ico') `
    --collect-all yt_dlp `
    --collect-all yt_dlp_ejs `
    --add-binary "$BundledFfmpeg;." `
    --add-binary "$BundledNode;." `
    --add-data "$(Join-Path $Root 'assets\youfa.ico');." `
    --add-data "$(Join-Path $Root 'CREDITS.txt');." `
    (Join-Path $Root 'app.py')
if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar o executavel.' }
