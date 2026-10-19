#Requires -Version 5.1
param(
    [string]$Template = '',
    [string]$PublicUrl = '',
    [int]$Port = 8200
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -m venv .venv
    } else {
        $launcher = Get-Command python -ErrorAction Stop
        & $launcher.Source -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create a Python virtual environment. Install Python 3.11+.' }
}
$ready = Join-Path $PSScriptRoot '.venv\per-diem-ready.txt'
if (-not (Test-Path -LiteralPath $ready)) {
    & $python -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Correct the error and run pip install -e . in this virtual environment.' }
    Set-Content -LiteralPath $ready -Value 'Dependencies installed' -Encoding ASCII
}
$arguments = @('-m', 'per_diem_signing', '--port', "$Port")
if ($Template) { $arguments += @('--template', $Template) }
if ($PublicUrl) { $arguments += @('--public-url', $PublicUrl) }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'The signing server stopped with an error. See the message above.' }
