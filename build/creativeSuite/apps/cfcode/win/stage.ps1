# Native Windows stage adapter for code-oss.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuiteStage 'code-oss'
