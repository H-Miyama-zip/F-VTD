param(
    [ValidateSet('export','preview','apply')][string]$Action='preview',
    [string]$Workbook='',
    [switch]$NoOpen
)
$ErrorActionPreference='Stop'
$env:PYTHONIOENCODING='utf-8'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runtimeRoot = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies'
$pythonExe = Join-Path $runtimeRoot 'python/python.exe'
$nodeExe = Join-Path $runtimeRoot 'node/bin/node.exe'
$outputRoot = Join-Path $projectRoot 'outputs/01a11e9f-3575-7fc3-8eb3-55ed85c0a3c9'
$editorFile = Join-Path $PSScriptRoot 'editor.py'
$defaultBook = Join-Path $outputRoot '追加候補_作業台帳.xlsx'
if (-not $Workbook) { $Workbook=$defaultBook }
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'CodexのPython実行環境が見つかりません。' }
switch ($Action) {
    'export' {
        & $pythonExe $editorFile prepare
        if ($LASTEXITCODE -ne 0) { throw '書き出し準備に失敗しました。' }
        # Preserve prior Excel edits instead of overwriting them during refresh.
        if (Test-Path -LiteralPath $defaultBook) {
            $historyRoot = Join-Path $outputRoot '台帳履歴'
            New-Item -ItemType Directory -Force -Path $historyRoot | Out-Null
            $savedBook = Join-Path $historyRoot ('追加候補_作業台帳_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '.xlsx')
            Move-Item -LiteralPath $defaultBook -Destination $savedBook
            Write-Host "前の台帳を保存しました: $savedBook"
        }
        $modulesPath=Join-Path $PSScriptRoot 'node_modules'
        if (-not (Test-Path -LiteralPath $modulesPath)) {
            New-Item -ItemType Junction -Path $modulesPath -Target (Join-Path $runtimeRoot 'node/node_modules') | Out-Null
        }
        & $nodeExe (Join-Path $PSScriptRoot 'build.mjs') (Join-Path $outputRoot 'workbook_input.json')
        if ($LASTEXITCODE -ne 0) { throw 'Excel台帳の生成に失敗しました。前の台帳は台帳履歴に保存されています。' }
        & $pythonExe $editorFile protect $defaultBook
        if ($LASTEXITCODE -ne 0) { throw '保護設定に失敗しました。' }
        & $pythonExe $editorFile preview $defaultBook
        if ($LASTEXITCODE -ne 0) { throw '生成した台帳の照合に失敗しました。' }
        if (-not $NoOpen) { Invoke-Item -LiteralPath $defaultBook }
    }
    'preview' {
        & $pythonExe $editorFile preview $Workbook
        $resultCode=$LASTEXITCODE
        if (-not $NoOpen -and (Test-Path -LiteralPath (Join-Path $outputRoot '変更内容.html'))) { Invoke-Item -LiteralPath (Join-Path $outputRoot '変更内容.html') }
        if ($resultCode -ne 0) { throw '差分確認に修正が必要な項目があります。変更内容を確認してください。' }
    }
    'apply' {
        & $pythonExe $editorFile apply (Join-Path $outputRoot '反映計画.json')
        if ($LASTEXITCODE -ne 0) { throw 'DBへ反映できませんでした。表示された理由を確認してください。' }
    }
}
