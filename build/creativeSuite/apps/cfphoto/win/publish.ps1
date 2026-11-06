# Native Windows publish adapter for rapidraw.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuitePublish 'rapidraw'
