# Native Windows publish adapter for cfpaint.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
Invoke-SuitePublish 'cfpaint'
