param([switch]$NoOpen)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runtimeRoot=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies'
$pythonExe=Join-Path $runtimeRoot 'python/python.exe'
$outputRoot=Join-Path $projectRoot 'outputs/daily-dictionary'
$descriptor=Join-Path $outputRoot 'app_server.json'
$workspaceHasher=[System.Security.Cryptography.SHA256]::Create()
$rootFingerprint=([BitConverter]::ToString($workspaceHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot)))).Replace('-','').ToLowerInvariant()
$workspaceHasher.Dispose()
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
function Get-AppUrl {
    if (-not (Test-Path -LiteralPath $descriptor)) { return $null }
    try {
        $details=Get-Content -LiteralPath $descriptor -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($details.url -notmatch '^http://127\.0\.0\.1:[0-9]+$') { return $null }
        $health=Invoke-RestMethod -Uri ($details.url+'/api/health') -TimeoutSec 1
        if ($health.app -eq 'f-vtd-daily' -and $health.instance -eq $details.instance -and $health.root_id -eq $details.root_id -and $health.root_id -eq $rootFingerprint) {
            if ($health.storage_mode -eq 'candidate_db') { return $details.url }
            # Restart an older dictionary-writing server without deleting saved work.
            $session=Invoke-RestMethod -Uri ($details.url+'/api/session') -TimeoutSec 2
            Invoke-RestMethod -Uri ($details.url+'/api/shutdown') -Method Post -ContentType 'application/json' -Headers @{'X-App-Token'=$session.token} -Body '{}' -TimeoutSec 3 | Out-Null
            Start-Sleep -Milliseconds 600
        }
    } catch { return $null }
    return $null
}
$appUrl=Get-AppUrl
if (-not $appUrl) {
    $env:PYTHONIOENCODING='utf-8'
    $serverFile=Join-Path $PSScriptRoot 'app_server.py'
    Start-Process -FilePath $pythonExe -ArgumentList ('"'+$serverFile+'"') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $outputRoot 'app.log') -RedirectStandardError (Join-Path $outputRoot 'app-errors.log') | Out-Null
    for ($attempt=0; $attempt -lt 30 -and -not $appUrl; $attempt++) {
        Start-Sleep -Milliseconds 200
        $appUrl=Get-AppUrl
    }
}
if (-not $appUrl) { throw 'アプリを起動できませんでした。outputs/daily-dictionary/app-errors.logを確認してください。' }
if (-not $NoOpen) { Start-Process $appUrl }
Write-Output $appUrl
