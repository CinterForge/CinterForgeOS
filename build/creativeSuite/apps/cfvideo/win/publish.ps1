# Native Windows publish adapter for cfvideo.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuitePublish 'cfvideo'
