# Native Windows stage adapter for rapidraw.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuiteStage 'rapidraw'
