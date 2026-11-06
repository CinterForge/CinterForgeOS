# Native Windows publish adapter for cfdocument.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuitePublish 'cfdocument'
