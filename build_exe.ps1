# Powered by Honyeong
$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildVenv = Join-Path $projectDir ".build-venv"
$pythonExe = Join-Path $buildVenv "Scripts\python.exe"
$releaseDir = Join-Path $projectDir "release"

Set-Location -LiteralPath $projectDir

if (-not (Test-Path -LiteralPath $pythonExe)) {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $launcher) {
        throw "EXE 빌드에는 Python 3가 필요합니다."
    }
    & $launcher.Source -3 -m venv $buildVenv
    if ($LASTEXITCODE -ne 0) {
        throw "빌드용 Python 환경을 만들지 못했습니다."
    }
}

& $pythonExe -m pip install --disable-pip-version-check --quiet `
    "pyinstaller>=6,<7" `
    "numpy>=2,<3" `
    "opencv-python-headless>=4.10,<5" `
    "mss>=10,<11" `
    "rapidocr-onnxruntime>=1.2,<2"
if ($LASTEXITCODE -ne 0) {
    throw "빌드 의존성을 설치하지 못했습니다."
}

& $pythonExe -m pip install --disable-pip-version-check --quiet --no-deps "windows-capture>=2,<3"
if ($LASTEXITCODE -ne 0) {
    throw "창 캡처 모듈을 설치하지 못했습니다."
}

& $pythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $releaseDir `
    --workpath (Join-Path $projectDir "build-exe") `
    (Join-Path $projectDir "EternalReturnScore.spec")
if ($LASTEXITCODE -ne 0) {
    throw "EXE 빌드에 실패했습니다."
}

$result = Join-Path $releaseDir "EternalReturnScore.exe"
Write-Host "빌드 완료: $result"
