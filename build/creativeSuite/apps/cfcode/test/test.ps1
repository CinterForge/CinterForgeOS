# Installed Windows code-oss smoke-test contract. Never report success without a real test.
param([ValidateSet('gui','headless')][string]$Mode = 'gui')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../../env/windows.ps1')
$installed = Join-Path (Get-SuiteRoot) 'Applications/win/code-oss'
if (-not (Test-Path -LiteralPath $installed -PathType Container)) { throw 'Application not installed: ' + $installed }
throw 'Windows functional smoke test not implemented for code-oss mode=' + $Mode
