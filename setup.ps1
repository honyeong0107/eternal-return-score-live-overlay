$ErrorActionPreference = "Stop"
$workspaceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $workspaceDir ".venv\Scripts\python.exe"
$requirementsPath = Join-Path $workspaceDir "requirements.txt"

Set-Location -LiteralPath $workspaceDir

if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Host "처음 실행 준비 중입니다. 잠시만 기다려 주세요..."
    $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonLauncher) {
        & py -3 -m venv .venv
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw "Python 3가 필요합니다. https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요."
        }
        & python -m venv .venv
    }
}

& $pythonExe -m pip install --disable-pip-version-check --quiet -r $requirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "필요한 구성 요소를 설치하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 실행하세요."
}

Write-Host "준비가 끝났습니다. 제어 화면을 여는 중입니다..."
& $pythonExe -m score_overlay --fps 5 --open
