# Native Windows build adapter for cfvideo.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuiteAcquire 'cfvideo'
