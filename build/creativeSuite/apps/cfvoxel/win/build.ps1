# Native Windows build adapter for blockbench.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuiteAcquire 'blockbench'
