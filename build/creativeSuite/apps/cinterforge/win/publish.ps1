# Native Windows publish adapter for cinterforge.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuitePublish 'cinterforge'
