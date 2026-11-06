# Native Windows build adapter for cfdocument.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuiteAcquire 'cfdocument'
