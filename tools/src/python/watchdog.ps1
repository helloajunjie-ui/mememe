# watchdog: check launcher every minute, restart if dead (unless stop.flag)
$root = 'F:\me\self-agent'
$py = Join-Path $root '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $root 'launcher.py'
$stopFlag = Join-Path $root 'data\stop.flag'
$logFile = Join-Path $root 'logs\watchdog.log'

$now = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

if (Test-Path $stopFlag) {
    Add-Content -Path $logFile -Value "[$now] stop.flag exists, user stopped, skip." -Encoding utf8
    exit 0
}

$launcherProc = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" | Where-Object { $_.CommandLine -like '*launcher.py*' }

if ($launcherProc) {
    exit 0
}

Add-Content -Path $logFile -Value "[$now] launcher not running, restarting." -Encoding utf8
Start-Process -FilePath $py -ArgumentList $launcher -WorkingDirectory $root -WindowStyle Hidden