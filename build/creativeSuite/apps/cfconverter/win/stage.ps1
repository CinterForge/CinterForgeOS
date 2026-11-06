$ErrorActionPreference = 'Stop'
$root = if ($env:CINTER_WORKSPACE_ROOT) { $env:CINTER_WORKSPACE_ROOT } else { (Resolve-Path (Join-Path $PSScriptRoot '../../../../../../..')).Path }
$repo = Join-Path $root 'CinterForgeConverter'
$builds = if ($env:CINTER_BUILDS_ROOT) { $env:CINTER_BUILDS_ROOT } else { Join-Path $root 'Builds' }
& (Join-Path $repo 'CC.ps1') stage win --builds $builds
if ($LASTEXITCODE -ne 0) { throw 'cfconverter native Windows stage failed' }
