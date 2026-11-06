# Native Windows stage adapter for cfvector.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuiteStage 'cfvector'
