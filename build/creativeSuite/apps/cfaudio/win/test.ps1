param([ValidateSet('gui','headless')][string]$Mode = 'gui')
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot '../../../shared/cfaudio/test/test.ps1') -Mode $Mode
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
