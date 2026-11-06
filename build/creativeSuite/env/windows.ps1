# Shared native Windows Creative Suite operations. No Bash/WSL requirement.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Get-SuiteRoot {
  if ($env:CINTER_APPLICATIONS_ROOT) { return $env:CINTER_APPLICATIONS_ROOT }
  return (Join-Path (Split-Path (Resolve-Path (Join-Path $PSScriptRoot '../../../../../..')).Path -Parent) 'Applications')
}
function Get-ArtifactUrl([string]$App) {
  if (-not $env:CINTER_CLOUDFLARE_APPLICATIONS_URL) { throw 'CINTER_CLOUDFLARE_APPLICATIONS_URL is required (existing Cloudflare public Applications base URL).' }
  $base = $env:CINTER_CLOUDFLARE_APPLICATIONS_URL.TrimEnd('/')
  if ($base -notmatch '^https://') { throw 'Cloudflare artifact base URL must use HTTPS.' }
  return "$base/win/$App.zip"
}
function Invoke-SuiteAcquire([string]$App) {
  $root = Get-SuiteRoot
  $acquired = Join-Path $root "Acquired/win/$App"
  $parent = Split-Path $acquired -Parent
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
  $temp = Join-Path $parent (".$App-acquire-" + [guid]::NewGuid().ToString('N'))
  $archive = "$temp.zip"
  try {
    New-Item -ItemType Directory -Path $temp | Out-Null
    if ($env:CINTER_APP_ARCHIVE) {
      if (-not (Test-Path -LiteralPath $env:CINTER_APP_ARCHIVE -PathType Leaf)) { throw "Archive missing: $env:CINTER_APP_ARCHIVE" }
      Copy-Item -LiteralPath $env:CINTER_APP_ARCHIVE -Destination $archive
    } else {
      $url = Get-ArtifactUrl $App
      Write-Host "[Cinter Windows] Download $url"
      Invoke-WebRequest -Uri $url -OutFile $archive -MaximumRedirection 5
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
      if ($zip.Entries.Count -eq 0) { throw 'Downloaded archive is empty.' }
      $prefix = [IO.Path]::GetFullPath($temp + [IO.Path]::DirectorySeparatorChar)
      foreach ($entry in $zip.Entries) {
        $destination = [IO.Path]::GetFullPath((Join-Path $temp $entry.FullName))
        if (-not $destination.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe ZIP entry: $($entry.FullName)" }
      }
    } finally { $zip.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $temp)
    if (-not @(Get-ChildItem -LiteralPath $temp -Force).Count) { throw 'Acquired application has no files.' }
    $old = "$acquired.old"
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
    if (Test-Path -LiteralPath $acquired) { Move-Item -LiteralPath $acquired -Destination $old }
    try { Move-Item -LiteralPath $temp -Destination $acquired }
    catch { if (Test-Path -LiteralPath $old) { Move-Item -LiteralPath $old -Destination $acquired }; throw }
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
    Write-Host "[Cinter Windows] Acquired $App"
  } finally {
    if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
  }
}
function Invoke-SuiteStage([string]$App) {
  $root = Get-SuiteRoot
  $source = Join-Path $root "Acquired/win/$App"
  $target = Join-Path $root "Applications/win/$App"
  if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Not acquired: $source" }
  if (-not @(Get-ChildItem -LiteralPath $source -Force).Count) { throw "Empty acquisition: $source" }
  $parent = Split-Path $target -Parent
  New-Item -ItemType Directory -Path $parent -Force | Out-Null
  $temp = Join-Path $parent (".$App-stage-" + [guid]::NewGuid().ToString('N'))
  $old = "$target.old"
  try {
    Copy-Item -LiteralPath $source -Destination $temp -Recurse
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
    if (Test-Path -LiteralPath $target) { Move-Item -LiteralPath $target -Destination $old }
    try { Move-Item -LiteralPath $temp -Destination $target }
    catch { if (Test-Path -LiteralPath $old) { Move-Item -LiteralPath $old -Destination $target }; throw }
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
    Write-Host "[Cinter Windows] Staged $App at $target"
  } finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force } }
}
function Invoke-SuitePublish([string]$App) {
  # Publishing uses the same deterministic key as acquisition. No manifest.
  $root = Get-SuiteRoot
  $source = Join-Path $root "Applications/win/$App"
  if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Installed application missing: $source" }
  if (-not @(Get-ChildItem -LiteralPath $source -Force).Count) { throw "Installed application empty: $source" }
  if (-not $env:CINTER_CLOUDFLARE_UPLOAD_URL -or -not $env:CINTER_CLOUDFLARE_UPLOAD_TOKEN) {
    throw 'Publishing requires existing Cloudflare upload endpoint/token: CINTER_CLOUDFLARE_UPLOAD_URL and CINTER_CLOUDFLARE_UPLOAD_TOKEN.'
  }
  $url = "$($env:CINTER_CLOUDFLARE_UPLOAD_URL.TrimEnd('/'))/win/$App.zip"
  if ($url -notmatch '^https://') { throw 'Cloudflare upload URL must use HTTPS.' }
  $archive = Join-Path ([IO.Path]::GetTempPath()) ("cinter-$App-" + [guid]::NewGuid().ToString('N') + '.zip')
  try {
    Compress-Archive -Path (Join-Path $source '*') -DestinationPath $archive -CompressionLevel Optimal
    Invoke-WebRequest -Method Put -Uri $url -Headers @{ Authorization = "Bearer $env:CINTER_CLOUDFLARE_UPLOAD_TOKEN" } -InFile $archive -ContentType 'application/zip' | Out-Null
    Write-Host "[Cinter Windows] Published $App to $url"
  } finally { if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force } }
}
