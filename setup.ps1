$ErrorActionPreference = "Stop"
$workspaceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $workspaceDir ".venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
$requirementsPath = Join-Path $workspaceDir "requirements.txt"
$pythonDownloadUrl = "https://www.python.org/downloads/windows/"

Set-Location -LiteralPath $workspaceDir

function Test-CompatiblePython {
    param(
        [string]$Program,
        [string[]]$Arguments = @()
    )

    try {
        & $Program @Arguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonProgram = $null
    $pythonArguments = @()
    $pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pythonLauncher -and (Test-CompatiblePython -Program $pythonLauncher.Source -Arguments @("-3"))) {
        $pythonProgram = $pythonLauncher.Source
        $pythonArguments = @("-3")
    }
    else {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        $isStoreAlias = $pythonCommand -and $pythonCommand.Source -like "*\WindowsApps\python.exe"
        if ($pythonCommand -and -not $isStoreAlias -and (Test-CompatiblePython -Program $pythonCommand.Source)) {
            $pythonProgram = $pythonCommand.Source
        }
    }

    if (-not $pythonProgram) {
        Write-Host ""
        Write-Host "Python 3.10 이상이 필요합니다." -ForegroundColor Yellow
        Write-Host "지금 Python 공식 다운로드 페이지를 엽니다."
        Write-Host ""
        Write-Host "1. Python 3 설치 파일을 내려받아 실행해 주세요."
        Write-Host "2. 설치 첫 화면의 'Add python.exe to PATH'를 꼭 체크해 주세요."
        Write-Host "3. 설치가 끝나면 이 창을 닫고 start.bat을 다시 실행해 주세요."
        Write-Host ""
        Write-Host $pythonDownloadUrl -ForegroundColor Cyan
        try {
            Start-Process $pythonDownloadUrl
        }
        catch {
            Write-Host "브라우저가 열리지 않으면 위 주소를 직접 열어 주세요."
        }
        exit 2
    }

    Write-Host "처음 실행 준비 중입니다. 필요한 구성 요소를 설치합니다..."
    & $pythonProgram @pythonArguments -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Python 가상 환경을 만들지 못했습니다."
    }
}

& $pythonExe -m pip install --disable-pip-version-check --quiet -r $requirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "필요한 구성 요소를 설치하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 실행하세요."
}

Write-Host "준비가 끝났습니다. 제어 화면을 여는 중입니다..."
& $pythonExe -m score_overlay --fps 5 --open
