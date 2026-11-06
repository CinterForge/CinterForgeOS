# Installed Windows cfvideo smoke-test contract. Never report success without a real test.
param([ValidateSet('gui','headless')][string]$Mode = 'gui')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
$installed = Join-Path (Get-SuiteRoot) 'Applications/win/cfvideo'
if (-not (Test-Path -LiteralPath $installed -PathType Container)) { throw 'Application not installed: ' + $installed }
throw 'Windows functional smoke test not implemented for cfvideo mode=' + $Mode
