# Native Windows stage adapter for cfpaint.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuiteStage 'cfpaint'
