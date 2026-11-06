# Native Windows build adapter for cfpaint.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuiteAcquire 'cfpaint'
