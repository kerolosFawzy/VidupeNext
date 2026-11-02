$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
py -3 -m pip install --upgrade pip
py -3 -m pip install -r requirements.txt pyinstaller
$ffmpeg = (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue).Source
$ffprobe = (Get-Command ffprobe.exe -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg -or -not $ffprobe) { throw 'Install FFmpeg and ensure ffmpeg.exe and ffprobe.exe are on PATH before building.' }
py -3 -m PyInstaller --noconfirm --clean --onedir --windowed --name VidupeNext --collect-all cv2 --add-binary "$($ffmpeg);." --add-binary "$($ffprobe);." app.py
Write-Host "Built: $PSScriptRoot\dist\VidupeNext\VidupeNext.exe"
$inno = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "${env:ProgramFiles}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($inno) { & $inno 'installer\VidupeNext.iss'; Write-Host 'Installer created in installer-output' }
else { Write-Host 'Inno Setup 6 not found. EXE is ready, but installer was not built.' }
