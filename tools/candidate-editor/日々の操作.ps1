param(
    [ValidateSet('today','preview','apply')][string]$Action='today',
    [ValidateRange(1,100)][int]$Count=10,
    [switch]$NoOpen
)
$ErrorActionPreference='Stop'
$env:PYTHONIOENCODING='utf-8'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$OutputEncoding=[Console]::OutputEncoding
$projectRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runtimeRoot=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies'
$pythonExe=Join-Path $runtimeRoot 'python/python.exe'
$nodeExe=Join-Path $runtimeRoot 'node/bin/node.exe'
$daily=Join-Path $PSScriptRoot 'daily.py'
$outputRoot=Join-Path $projectRoot 'outputs/daily-dictionary'
$latest=Join-Path $outputRoot 'latest.json'
if ($Action -eq 'today') {
    # Reopen pending work rather than overwrite it or silently discard choices.
    $batch=''
    if (Test-Path -LiteralPath $latest) {
        $batch=(Get-Content -LiteralPath $latest -Raw -Encoding UTF8 | ConvertFrom-Json).batch
        if (Test-Path -LiteralPath (Join-Path $batch '完了.json')) { $batch='' }
    }
    if ($batch -and (Test-Path -LiteralPath (Join-Path $batch 'app_review.json'))) {
        throw 'この候補一覧はアプリへ引き継いでいます。プロジェクト直下の辞書追加アプリ.cmdを開いてください。'
    }
    if (-not $batch) {
        $prepared=& $pythonExe $daily prepare --count $Count
        if ($LASTEXITCODE -ne 0) { throw '今日の候補を作れませんでした。表示された理由を確認してください。' }
        $batch=($prepared | ConvertFrom-Json).batch
        $modulesPath=Join-Path $PSScriptRoot 'node_modules'
        if (-not (Test-Path -LiteralPath $modulesPath)) {
            New-Item -ItemType Junction -Path $modulesPath -Target (Join-Path $runtimeRoot 'node/node_modules') | Out-Null
        }
        & $nodeExe (Join-Path $PSScriptRoot 'build_daily.mjs') (Join-Path $batch 'daily_input.json')
        if ($LASTEXITCODE -ne 0) { throw '候補Excelの生成に失敗しました。元の台帳・辞書は変更されていません。' }
        & $pythonExe $daily protect (Join-Path $batch '今日の候補.xlsx')
        if ($LASTEXITCODE -ne 0) { throw '候補Excelの保護設定に失敗しました。' }
        & $pythonExe $daily activate $batch
        if ($LASTEXITCODE -ne 0) { throw '候補Excelの照合に失敗しました。' }
    }
    if (-not $NoOpen) { Invoke-Item -LiteralPath (Join-Path $batch '今日の候補.xlsx') }
    Write-Host "作業用Excel: $(Join-Path $batch '今日の候補.xlsx')"
} else {
    if (-not (Test-Path -LiteralPath $latest)) { throw '先に「01_今日の候補を開く」を実行してください。' }
    $batch=(Get-Content -LiteralPath $latest -Raw -Encoding UTF8 | ConvertFrom-Json).batch
    if (Test-Path -LiteralPath (Join-Path $batch 'app_review.json')) {
        throw 'この候補一覧はアプリへ引き継いでいます。プロジェクト直下の辞書追加アプリ.cmdを開いてください。'
    }
    if ($Action -eq 'preview') {
        & $pythonExe $daily preview (Join-Path $batch '今日の候補.xlsx')
        if ($LASTEXITCODE -ne 0) { throw '内容確認に修正が必要です。表示された理由を確認してください。' }
        if (-not $NoOpen) { Invoke-Item -LiteralPath (Join-Path $batch '辞書への追加内容.html') }
    } else {
        & $pythonExe $daily apply (Join-Path $batch '辞書反映計画.json')
        if ($LASTEXITCODE -ne 0) { throw '辞書への反映に失敗しました。表示された理由を確認してください。' }
        Write-Host 'ローカルの辞書とサイトへ反映しました。Web公開はCodexへ依頼してください。'
    }
}
