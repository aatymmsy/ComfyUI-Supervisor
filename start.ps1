param([int]$Port = 7860, [switch]$Demo, [switch]$Test)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
if (-not $Demo -and -not $Test) {
    $address = "http://127.0.0.1:$Port"
    try {
        $running = Invoke-RestMethod -Uri "$address/config" -TimeoutSec 2
        if ($running.components | Where-Object { $_.props.elem_id -eq 'result-gallery' }) {
            Write-Host "ComfyUI Supervisor is already running: $address"
            Start-Process $address
            exit 0
        }
    } catch {
        # A first launch has no server yet; continue with environment setup.
    }
}
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
$installStamp = Join-Path $projectRoot '.venv\.supervisor-install.sha256'
$projectHash = (Get-FileHash -LiteralPath (Join-Path $projectRoot 'pyproject.toml') -Algorithm SHA256).Hash
if (-not (Test-Path -LiteralPath $installStamp) -or (Get-Content -LiteralPath $installStamp -Raw).Trim() -ne $projectHash) {
    & $venvPython -m pip install -e "$projectRoot[test]"
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    Set-Content -LiteralPath $installStamp -Value $projectHash -Encoding ascii
}
if ($Test) {
    Push-Location $projectRoot
    try { & $venvPython -m pytest -q } finally { Pop-Location }
} elseif ($Demo) {
    & $venvPython -m supervisor demo --root $projectRoot
} else {
    & $venvPython -m supervisor serve --root $projectRoot --port $Port
}
if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE." }
