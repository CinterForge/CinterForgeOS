[CmdletBinding()]
param(
  [string] $Archive = ""
)

# Build a relocatable Windows IF runtime bundle from already-installed developer
# tools. This is a maintainer packaging command; ordinary setup consumes the

$ErrorActionPreference = "Stop"
$ScriptDir = (Resolve-Path $PSScriptRoot).Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..\..\..")).Path
$RuntimeRoot = if ($env:CINTERFORGE_IF_RUNTIME_ROOT) { $env:CINTERFORGE_IF_RUNTIME_ROOT } else { Join-Path $RepoRoot "Builds\InteractiveFiction\Tools" }
$PlatformKey = "windows-x86_64"
if (-not $Archive) { $Archive = Join-Path $RepoRoot "Builds\InteractiveFiction\ToolBundles\CinterForge-InteractiveFictionRuntime-v2-$PlatformKey.tar.gz" }

function Resolve-HostExecutable {
  param([string] $Name, [string] $EnvironmentName = "")
  if ($EnvironmentName) {
    $Candidate = [Environment]::GetEnvironmentVariable($EnvironmentName)
    if ($Candidate -and (Test-Path -LiteralPath $Candidate -PathType Leaf) -and -not $Candidate.StartsWith($RuntimeRoot, [StringComparison]::OrdinalIgnoreCase)) { return (Resolve-Path $Candidate).Path }
  }
  $Command = Get-Command $Name -ErrorAction SilentlyContinue
  if ($Command -and -not $Command.Source.StartsWith($RuntimeRoot, [StringComparison]::OrdinalIgnoreCase)) { return $Command.Source }
  return $null
}

function Test-WindowsExecutableFile {
  param([string] $Executable)

  if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
  # Packaging validation must be location-independent.  The legacy IFArchive
  # from a staging/temp directory even when the EXE bytes are unchanged.  Prove
  # that this is a Windows PE executable here; IFPlayer/update-data performs the
  # real functional launch test against an actual story after installation.
  try {
    $Stream = [System.IO.File]::OpenRead($Executable)
    try {
      if ($Stream.Length -lt 64) { return $false }
      $Reader = New-Object System.IO.BinaryReader($Stream)
      if ($Reader.ReadUInt16() -ne 0x5A4D) { return $false } # MZ
      $Stream.Seek(0x3C, [System.IO.SeekOrigin]::Begin) | Out-Null
      $PeOffset = $Reader.ReadInt32()
      if ($PeOffset -lt 0 -or ($PeOffset + 4) -gt $Stream.Length) { return $false }
      $Stream.Seek($PeOffset, [System.IO.SeekOrigin]::Begin) | Out-Null
      return ($Reader.ReadUInt32() -eq 0x00004550) # PE\0\0
    } finally { $Stream.Dispose() }
  } catch { return $false }
}

}

$Dot = Resolve-HostExecutable "dot.exe" "CINTERFORGE_GRAPHVIZ_DOT"
if (-not $Dot) {
  $SearchRoots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
  $DotFile = Get-ChildItem -Path $SearchRoots -Filter "dot.exe" -File -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match "Graphviz.*\\bin\\dot.exe$" } | Select-Object -First 1
  if ($DotFile) { $Dot = $DotFile.FullName }
}
if (-not $Dot) { throw "[if-runtime-bundle] Graphviz dot.exe not found. Install Graphviz on the maintainer machine before packaging." }
$GraphvizBin = Split-Path -Parent $Dot
$GraphvizRoot = Split-Path -Parent $GraphvizBin
$Neato = Join-Path $GraphvizBin "neato.exe"
if (-not (Test-Path -LiteralPath $Neato -PathType Leaf)) { throw "[if-runtime-bundle] Graphviz neato.exe not found beside dot.exe: $GraphvizBin" }

$StagingParent = Join-Path $env:TEMP ("cinterforge-if-runtime-build-" + [Guid]::NewGuid().ToString("N"))
$StagingRoot = Join-Path $StagingParent "InteractiveFiction"
$Bin = Join-Path $StagingRoot "bin"
$GraphvizStageRoot = Join-Path $StagingRoot "graphviz"
$GraphvizStageBin = Join-Path $GraphvizStageRoot "bin"
$Licenses = Join-Path $StagingRoot "licenses"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
New-Item -ItemType Directory -Force -Path $GraphvizStageRoot | Out-Null
New-Item -ItemType Directory -Force -Path $Licenses | Out-Null
try {
  # Preserve the complete Graphviz portable distribution layout, not merely its
  # bin directory. Graphviz may resolve plugins/configuration from sibling lib,
  # share, and etc directories even when dot.exe itself lives in bin.
  Copy-Item -Path (Join-Path $GraphvizRoot "*") -Destination $GraphvizStageRoot -Recurse -Force
  if (-not (Test-Path -LiteralPath $GraphvizStageBin -PathType Container)) {
    throw "[if-runtime-bundle] Graphviz distribution did not stage graphviz\bin from: $GraphvizRoot"
  }
  # Windows DLL search directory and can make this old binary load an unrelated
  # complete Graphviz distribution lives under graphviz/.
  }

  # Preserve upstream notices when they are present in the portable inputs.
  foreach ($CandidateName in @("LICENSE", "LICENSE.txt", "COPYING", "COPYING.txt")) {
    $Candidate = Join-Path $GraphvizRoot $CandidateName
    if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
      Copy-Item -LiteralPath $Candidate -Destination (Join-Path $Licenses "graphviz.txt") -Force
      break
    }
  }
  $Manifest = [ordered]@{
    schema = "cinterforge.if.runtime-tools.v1"
    platform = $PlatformKey
    layout = "windows-graphviz-bin-v1"
    tools = [ordered]@{
      dot = "graphviz/bin/dot.exe"
      neato = "graphviz/bin/neato.exe"
    }
  }
  $ManifestJson = $Manifest | ConvertTo-Json -Depth 5
  [System.IO.File]::WriteAllText((Join-Path $StagingRoot "manifest.json"), $ManifestJson + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))

  $OldPath = $env:Path
  try {
    $env:Path = "$Bin;$GraphvizStageBin;$OldPath"
    $DotInput = Join-Path $StagingParent "dot.gv"
    $DotOutput = Join-Path $StagingParent "dot.txt"
    $NeatoInput = Join-Path $StagingParent "neato.gv"
    $NeatoOutput = Join-Path $StagingParent "neato.txt"
    Set-Content -LiteralPath $DotInput -Encoding ASCII -Value "digraph G { a -> b }"
    Set-Content -LiteralPath $NeatoInput -Encoding ASCII -Value "graph G { a -- b }"
    & (Join-Path $GraphvizStageBin "dot.exe") -Tplain $DotInput -o $DotOutput *> $null
    if ($LASTEXITCODE -ne 0) { throw "[if-runtime-bundle] staged dot.exe failed -Tplain validation." }
    & (Join-Path $GraphvizStageBin "neato.exe") -Tplain $NeatoInput -o $NeatoOutput *> $null
    if ($LASTEXITCODE -ne 0) { throw "[if-runtime-bundle] staged neato.exe failed -Tplain validation." }
  } finally { $env:Path = $OldPath }

  Remove-Item -LiteralPath $RuntimeRoot -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $RuntimeRoot) | Out-Null
  Copy-Item -LiteralPath $StagingRoot -Destination $RuntimeRoot -Recurse -Force

  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Archive) | Out-Null
  Remove-Item -LiteralPath $Archive -Force -ErrorAction SilentlyContinue
  $Tar = Get-Command tar.exe -ErrorAction SilentlyContinue
  if (-not $Tar) { throw "[if-runtime-bundle] tar.exe is required to create the .tar.gz archive on Windows." }
  Push-Location $StagingParent
  try {
    & $Tar.Source -czf $Archive "InteractiveFiction"
    if ($LASTEXITCODE -ne 0) { throw "[if-runtime-bundle] tar.exe failed with exit code $LASTEXITCODE." }
  } finally { Pop-Location }

  Write-Host "[if-runtime-bundle] Runtime root: $RuntimeRoot"
  Write-Host "[if-runtime-bundle] Archive: $Archive"
  Write-Host "[if-runtime-bundle] Platform: $PlatformKey"
} finally {
  Remove-Item -LiteralPath $StagingParent -Recurse -Force -ErrorAction SilentlyContinue
}
