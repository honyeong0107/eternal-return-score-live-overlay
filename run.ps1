param(
    [double]$Fps = 5.0,
    [int]$Monitor = 1
)

$workspaceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $workspaceDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw ".venv가 없습니다. 먼저 py -m venv .venv 를 실행하세요."
}

& $pythonExe -m score_overlay --fps $Fps --monitor $Monitor --open
