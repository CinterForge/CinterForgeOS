# Native Windows build adapter for cinterforge.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuiteAcquire 'cinterforge'
