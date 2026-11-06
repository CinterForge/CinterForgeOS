# Native Windows stage adapter for cfimage.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuiteStage 'cfimage'
