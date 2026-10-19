#Requires -Version 5.1
param(
    [string]$PublicUrl = 'https://perdiem.inventorysolution.dev',
    [int]$Port = 8200,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$name = 'PerDiemSigning'
if ($Uninstall) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host 'Removed the signing startup task. Private data was retained.'
    exit
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'))) {
    throw 'Run start.ps1 first to install dependencies.'
}
$uri = [Uri]$PublicUrl
if ($uri.Scheme -ne 'https' -or $uri.AbsolutePath -ne '/' -or $uri.Query -or
    $uri.Fragment -or $uri.UserInfo -or $PublicUrl.Contains('"')) {
    throw 'PublicUrl must be a plain HTTPS origin.'
}
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$data = Join-Path $env:LOCALAPPDATA 'PerDiemSigning'
$log = Join-Path $data 'server.log'
$arguments = "-m per_diem_signing --public-url `"$PublicUrl`" --port $Port --data-dir `"$data`" --no-key-output --log-file `"$log`""
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Standalone per-diem signing server; starts at Windows sign-in and retries on failure.' -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host 'Per-diem signing starts now and automatically at Windows sign-in.'
Write-Host "Public address: $PublicUrl"
Write-Host 'Log: %LOCALAPPDATA%\PerDiemSigning\server.log (administrator key is not logged).'
