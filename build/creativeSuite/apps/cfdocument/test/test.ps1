# Installed Windows cfdocument smoke-test contract. Never report success without a real test.
param([ValidateSet('gui','headless')][string]$Mode = 'gui')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
$installed = Join-Path (Get-SuiteRoot) 'Applications/win/cfdocument'
if (-not (Test-Path -LiteralPath $installed -PathType Container)) { throw 'Application not installed: ' + $installed }
throw 'Windows functional smoke test not implemented for cfdocument mode=' + $Mode
