Param(
  [Parameter(Mandatory=$true)][string]$AppDir
)

# ============================================================
# creativeSuite/shared/win/stage.ps1
# PowerShell 5.1 compatible. MSBuild-friendly: exits 0 after logging.
#
# Production worker Python remains:
#   <AppDir>\python\app\cinterenv\Scripts\pythonw.exe
#
# Site-packages remain in the production cinterenv:
#   <AppDir>\python\app\cinterenv\Lib\site-packages
#
# This script does not delete/re-copy cinterenv or site-packages every build.
# Delete <AppDir>\python or <AppDir>\python\app\cinterenv to force a full refresh.
# ============================================================

$ErrorActionPreference = "Continue"
try { Set-StrictMode -Off } catch {}

function Normalize-Path([string]$p) { return $p.Trim().Trim('"').Trim("'").TrimEnd('\').TrimEnd('/') }
function Info([string]$msg) { Write-Host $msg }
function Warn([string]$msg) { Write-Warning $msg }

function Robocopy-Safe([string]$src, [string]$dst) {
  if (!(Test-Path $src)) { Warn "Source not found (skip): $src"; return }
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
  Info "ROBOCOPY: $src -> $dst"
  & robocopy "$src" "$dst" /E /R:1 /W:1 /XD __pycache__ | Out-Null
  $rc = $LASTEXITCODE
  Info "robocopy rc=$rc"
  if ($rc -ge 8) { Warn "robocopy FAILED ($rc): $src -> $dst" }
  $global:LASTEXITCODE = 0
}

function Copy-FileIfExists([string]$src, [string]$dst) {
  if (Test-Path $src) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    Copy-Item -Force $src $dst
    return $true
  }
  return $false
}

function Ensure-Python310DllInAppRoot([string]$AppDir, [string]$PyRoot, [string]$PySrc) {
  # CinterForge.exe loads python310.dll from the EXE folder on Windows.
  # Keep the existing copy in <AppDir>\python, but also force an explicit
  # duplicate at <AppDir>\python310.dll even when the Python root copy was
  # skipped as already staged.
  $candidates = @(
    (Join-Path $PyRoot "python310.dll"),
    (Join-Path $PyRoot "DLLs\python310.dll")
  )

  if ($PySrc) {
    $candidates += @(
      (Join-Path $PySrc "python310.dll"),
      (Join-Path $PySrc "DLLs\python310.dll"),
      (Join-Path $PySrc "Scripts\python310.dll")
    )
  }

  $PyEnvRoot = Join-Path $env:USERPROFILE ".pyenv\pyenv-win\versions\3.10.0"
  $candidates += @(
    (Join-Path $PyEnvRoot "python310.dll"),
    (Join-Path $PyEnvRoot "DLLs\python310.dll")
  )

  $src = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  if ($src) {
    Copy-Item -Force $src (Join-Path $AppDir "python310.dll")
    Copy-Item -Force $src (Join-Path $PyRoot "python310.dll")
    Info "Ensured python310.dll -> EXE folder root + python root"
  } else {
    Warn "python310.dll not found; CinterForge may fail to load embedded Python."
  }
}


function Find-RepoRoot([string]$start) {
  $p = (Resolve-Path $start).Path
  while ($true) {
    if (Test-Path (Join-Path $p ".git")) { return $p }
    $parent = Split-Path -Parent $p
    if ($parent -eq $p) { throw "Could not find repo root (.git) starting from $start" }
    $p = $parent
  }
}
function Rewrite-CinterEnvPyVenvCfg([string]$CinterEnvDir, [string]$PyRoot) {
  $cfg = Join-Path $CinterEnvDir "pyvenv.cfg"
  if (!(Test-Path $CinterEnvDir)) { Warn "cinterenv missing, cannot rewrite pyvenv.cfg: $CinterEnvDir"; return }

  $pyExe = Join-Path $PyRoot "python.exe"
#<AppDir>\python\<AppDir>\python\app\cinterenv\Scripts\pythonw.exepythonw.exe
$content = @"
home = cinterenv\Scripts
include-system-site-packages = false
version = 3.10.0
"@
  Set-Content -Path $cfg -Value $content -Encoding ASCII
  Info "Rewrote pyvenv.cfg: $cfg"
}

function Remove-WrongRootSitePackages([string]$PyRoot) {
  # Enforce the single-venv architecture:
  #   python\Lib is stdlib only
  #   third-party packages live only in python\app\cinterenv\Lib\site-packages
  $badSite = Join-Path $PyRoot "Lib\site-packages"
  if (Test-Path $badSite) {
    Info "Remove wrong-root site-packages: $badSite"
    Remove-Item -Recurse -Force $badSite
  }
}

try {
  $AppDir = Normalize-Path $AppDir
  if (!(Test-Path $AppDir)) { Warn "AppDir not found: $AppDir (skipping staging)"; exit 0 }

  $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  if ($PSScriptRoot) { $ScriptDir = $PSScriptRoot }
  $RepoRoot  = Find-RepoRoot $ScriptDir

  Info "============================================================"
  Info "Staging (Win)  AppDir=$AppDir"
  Info "ScriptDir=$ScriptDir"
  Info "RepoRoot=$RepoRoot"
  Info "PS=$($PSVersionTable.PSVersion)"
  Info "============================================================"

  $BatFile = Join-Path $ScriptDir "build.bat"

  if (Test-Path $BatFile) {
    Info "Running: $BatFile"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $BatFile
    $psi.WorkingDirectory = $ScriptDir
    $psi.UseShellExecute = $false

    # Minimal clean environment to avoid Windows command-line/env limits
    $psi.EnvironmentVariables.Clear()

    $psi.EnvironmentVariables["SystemRoot"] = $env:SystemRoot
    $psi.EnvironmentVariables["WINDIR"]     = $env:WINDIR
    $psi.EnvironmentVariables["TEMP"]       = $env:TEMP
    $psi.EnvironmentVariables["TMP"]        = $env:TMP
    $UserProfile = $env:USERPROFILE
    
    $psi.EnvironmentVariables["PATH"] =
      "$UserProfile\scoop\shims;" +
      "$UserProfile\AppData\Roaming\npm;" +
      "C:\Program Files\nodejs;" +
      "C:\Windows\System32;" +
      "C:\Windows;" +
      "C:\Windows\System32\WindowsPowerShell\v1.0;" +
      "C:\Program Files\Git\cmd;" +
      "C:\Program Files\Git\bin;" +
      "$UserProfile\AppData\Local\Microsoft\WinGet\Links"

    $psi.EnvironmentVariables["HUGO_SASS_PATH"] = "$UserProfile\scoop\shims\sass.cmd"
    
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.WaitForExit()

    if ($proc.ExitCode -ne 0) {
      Warn "creativeSuite/shared/win/build.bat failed with exit code $($proc.ExitCode)"
    }
  }
  else {
    Warn "creativeSuite/shared/win/build.bat missing: $BatFile"
  }

  $BuildsRoot = Join-Path $RepoRoot "Builds"

  # Asset/card generation is owned by the in-app automation flow. App staging
  # must not call generate_build_assets.py because that can overwrite rendered
  # card artwork before it is embedded into final Pack/DLC payload cards.
  Info "Build asset generation skipped during app staging"
  Info "Asset/card generation is app-owned. Run the in-app Update DLC Artwork automation to render cards and finalize DLC staging."
  if ($env:CINTERFORGE_STAGE_GENERATE_ASSETS -eq "1" -or $env:CINTERFORGE_BUILD_GENERATE_ASSETS -eq "1") {
    Warn "CINTERFORGE_STAGE_GENERATE_ASSETS/CINTERFORGE_BUILD_GENERATE_ASSETS ignored; app staging never regenerates cards."
  }

  $resources = @(
    @{ Src = Join-Path $BuildsRoot "websites";   Dst = Join-Path $AppDir "websites";   Label="websites";   AlwaysFresh=$true },
    # Pack thumbnails are a build-time snapshot used by Collection before a Pack
    # is installed. Keep this AlwaysFresh so clean and incremental builds both
    # reflect the current setup/automation output exactly.
    @{ Src = Join-Path $BuildsRoot "Installers"; Dst = Join-Path $AppDir "Installers"; Label="Installers"; AlwaysFresh=$false }
  )

  $BundledPacks = Join-Path $AppDir "CinterForge\DLC\Packs"
  if (Test-Path $BundledPacks) {
    Info "Remove obsolete bundled Packs: $BundledPacks"
    Remove-Item -Recurse -Force $BundledPacks
  }
  # Runtime DLC/PlugIns is install-state derived. Pack thumbnails are lightweight
  # catalog art and are the only DLC catalog artifacts bundled with the app.
  # Launch-time runtime code mirrors installed Pack payload cards from
  # DLC\Models into DLC\PlugIns\Packs and extracts embedded Templates/* assets
  # into DLC\PlugIns\Templates. Never populate PlugIns or Templates during
  # setup/build/staging.
  $StaleBundledPlugIns = Join-Path $AppDir "CinterForge\DLC\PlugIns"
  if (Test-Path $StaleBundledPlugIns) {
    Info "Remove stale build-staged DLC/PlugIns content: $StaleBundledPlugIns"
    Remove-Item -Recurse -Force $StaleBundledPlugIns
  }
  Info "DLC/PlugIns staging skipped; launch derives PlugIns from installed DLC/Models Pack payload cards."

  foreach ($r in $resources) {
    if (!(Test-Path $r.Src)) { Warn "Resource source missing (skip): $($r.Src)"; continue }
    if ($r.AlwaysFresh -and (Test-Path $r.Dst)) {
      Info "Refresh $($r.Label): remove stale destination $($r.Dst)"
      Remove-Item -Recurse -Force $r.Dst
    } elseif (Test-Path $r.Dst) {
      Info " $($r.Label) already present (skip): $($r.Dst)"
      continue
    }
    Info " Copy $($r.Label)"
    Robocopy-Safe $r.Src $r.Dst
  }

  $PyRoot = Join-Path $AppDir "python"
  if (!(Test-Path $PyRoot)) { New-Item -ItemType Directory -Force -Path $PyRoot | Out-Null }

  $PySrc = $null

  # Search databases are on-demand data in Builds\Databases and are never embedded in the app bundle.

  $PrebuiltPy = Join-Path $BuildsRoot "python"
  $pythonLooksStaged = (Test-Path (Join-Path $PyRoot "Lib")) -and ((Test-Path (Join-Path $PyRoot "python310.dll")) -or (Test-Path (Join-Path $AppDir "python310.dll")))

  if ($pythonLooksStaged) {
    Info "Embedded Python already present (skip root copy): $PyRoot"
    if (Test-Path $PrebuiltPy) { $PySrc = $PrebuiltPy }
  } elseif (Test-Path $PrebuiltPy) {
    Info "Using prebuilt embedded python: $PrebuiltPy"
    Robocopy-Safe $PrebuiltPy $PyRoot
    $PySrc = $PrebuiltPy
  } else {
    $PyEnvRoot = Join-Path $env:USERPROFILE ".pyenv\pyenv-win\versions\3.10.0"
    if (Test-Path $PyEnvRoot) {
      Info "Using pyenv-win only as build source: $PyEnvRoot"
      Robocopy-Safe (Join-Path $PyEnvRoot "Lib")  (Join-Path $PyRoot "Lib")
      if (Test-Path (Join-Path $PyEnvRoot "DLLs")) { Robocopy-Safe (Join-Path $PyEnvRoot "DLLs") (Join-Path $PyRoot "DLLs") }
      Get-ChildItem -Path $PyEnvRoot -Filter "*.dll" -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -Force $_.FullName (Join-Path $PyRoot $_.Name)
      }
      Copy-FileIfExists (Join-Path $PyEnvRoot "python.exe")  (Join-Path $PyRoot "python.exe")  | Out-Null
      Copy-FileIfExists (Join-Path $PyEnvRoot "pythonw.exe") (Join-Path $PyRoot "pythonw.exe") | Out-Null
      $PySrc = $PyEnvRoot
    } else {
      Warn "No python source found: $PrebuiltPy or $PyEnvRoot"
    }
  }

  # Enforce after staging/skipping the embedded Python root because Builds\python
  # may already contain a stale Lib\site-packages. The only third-party package
  # root is python\app\cinterenv\Lib\site-packages.
  Remove-WrongRootSitePackages $PyRoot

  # Required by the Windows app loader: keep an extra python310.dll next to CinterForge.exe.
  Ensure-Python310DllInAppRoot $AppDir $PyRoot $PySrc

  if ($PySrc) {
    $isDebug = (Test-Path (Join-Path $AppDir "CinterForge.pdb")) -or ($AppDir -match "\\Debug($|\\)")
    if ($isDebug) { $pyDllName = "python310_d.dll" } else { $pyDllName = "python310.dll" }

    $dllCandidates = @(
      (Join-Path $PyRoot $pyDllName),
      (Join-Path $PyRoot ("DLLs\{0}" -f $pyDllName)),
      (Join-Path $PySrc  $pyDllName),
      (Join-Path $PySrc  ("DLLs\{0}" -f $pyDllName)),
      (Join-Path $PySrc  ("Scripts\{0}" -f $pyDllName))
    )
    $pyDll = $dllCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($pyDll) {
      Copy-Item -Force $pyDll (Join-Path $AppDir $pyDllName)
      Copy-Item -Force $pyDll (Join-Path $PyRoot $pyDllName)
      Info "Staged $pyDllName -> EXE folder + python root"
    } else { Warn "$pyDllName not found in staged python." }

    $zlibCandidates = @(
      (Join-Path $PyRoot "zlib.dll"),
      (Join-Path $PyRoot "DLLs\zlib.dll"),
      (Join-Path $PySrc  "zlib.dll"),
      (Join-Path $PySrc  "DLLs\zlib.dll")
    )
    $zlib = $zlibCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($zlib) { Copy-Item -Force $zlib (Join-Path $AppDir "zlib.dll"); Info "Staged zlib.dll -> EXE folder" }
  }

  # App python allow-list refreshes every build.
  $AppPySrcRoot = Join-Path $RepoRoot "CinterForgeServer"
  $AppPyDstRoot = Join-Path $AppDir  "python\app"
  $srcCinterDir = Join-Path $AppPySrcRoot "cinter"
  $dstCinterDir = Join-Path $AppPyDstRoot "cinter"
  $srcCore = Join-Path $AppPySrcRoot "CinterCore.py"
  $dstCore = Join-Path $AppPyDstRoot "CinterCore.py"

  New-Item -ItemType Directory -Force -Path $AppPyDstRoot | Out-Null
  if (Test-Path $dstCinterDir) { Remove-Item -Recurse -Force $dstCinterDir }
  if (Test-Path $dstCore) { Remove-Item $dstCore }

  Info "Copy app python package: $srcCinterDir -> $dstCinterDir"
  if (!(Test-Path $srcCinterDir)) { throw "Required cinter source missing: $srcCinterDir" }
  # /IS and /IT force refresh even when a developer edits a Python file
  # without changing its size or its rounded Windows timestamp.
  New-Item -ItemType Directory -Force -Path $dstCinterDir | Out-Null
  & robocopy "$srcCinterDir" "$dstCinterDir" /E /IS /IT /R:1 /W:1 /XD __pycache__ /XF *.pyc | Out-Null
  $copyResult = $LASTEXITCODE
  $global:LASTEXITCODE = 0
  if ($copyResult -ge 8) { throw "App Python staging failed (robocopy $copyResult): $srcCinterDir" }
  # Stage the authoritative IFPlayer web-runtime settings for embedded Python.
  $ifSettingsSrc = Join-Path $RepoRoot "CinterForgeWebApp\static\Code\IFPlayer\Runtime\Engine\settings\ifplayer_settings.json"
  $ifSettingsDst = Join-Path $dstCinterDir "Runtime\Engine\Settings\ifplayer_settings.json"
  if (!(Test-Path $ifSettingsSrc)) { throw "Required IFPlayer settings missing: $ifSettingsSrc" }
  if (!(Copy-FileIfExists $ifSettingsSrc $ifSettingsDst)) { throw "Failed to stage IFPlayer settings: $ifSettingsDst" }
  Info "Staged IFPlayer settings: $ifSettingsDst"

  if (!(Copy-FileIfExists $srcCore $dstCore)) { throw "Required CinterCore.py missing at: $srcCore" }
  Info "Copied CinterCore.py -> $dstCore"

  # Fail the build if the embedded application code differs from its source.
  # This protects both Debug and Release from silently running stale Python.
  $pythonSources = @(Get-Item $srcCore) + @(Get-ChildItem $srcCinterDir -Recurse -File | Where-Object {
    $_.Extension -ne ".pyc" -and $_.FullName -notmatch '[\\/]__pycache__[\\/]'
  })
  foreach ($sourceFile in $pythonSources) {
    if ($sourceFile.FullName -eq $srcCore) {
      $destinationFile = $dstCore
    } else {
      $relative = $sourceFile.FullName.Substring($srcCinterDir.Length).TrimStart('\', '/')
      $destinationFile = Join-Path $dstCinterDir $relative
    }
    if (!(Test-Path $destinationFile)) { throw "Embedded Python missing: $destinationFile" }
    $sourceHash = (Get-FileHash -Algorithm SHA256 $sourceFile.FullName).Hash
    $destinationHash = (Get-FileHash -Algorithm SHA256 $destinationFile).Hash
    if ($sourceHash -ne $destinationHash) { throw "Embedded Python stale: $destinationFile" }
  }
  Info "Verified $($pythonSources.Count) application Python files byte-for-byte"

  # Production worker venv lives here. Do not delete/re-copy every build.
  $srcCinterEnvDir = Join-Path $RepoRoot "Builds\cinterenv"
  $dstCinterEnvDir = Join-Path $AppPyDstRoot "cinterenv"
  $dstCinterEnvPy = Join-Path $dstCinterEnvDir "Scripts\pythonw.exe"
  $dstCinterEnvSite = Join-Path $dstCinterEnvDir "Lib\site-packages"

  $srcRequirementsMarker = Join-Path $srcCinterEnvDir ".cinterforge_requirements.sha256"
  $dstRequirementsMarker = Join-Path $dstCinterEnvDir ".cinterforge_requirements.sha256"
  $cinterEnvPresent = (Test-Path $dstCinterEnvPy) -and (Test-Path $dstCinterEnvSite)
  $requirementsCurrent = $false

  if ($cinterEnvPresent -and (Test-Path $srcRequirementsMarker) -and (Test-Path $dstRequirementsMarker)) {
    $srcRequirementsHash = (Get-Content -Raw $srcRequirementsMarker).Trim()
    $dstRequirementsHash = (Get-Content -Raw $dstRequirementsMarker).Trim()
    $requirementsCurrent = ($srcRequirementsHash -ne "") -and ($srcRequirementsHash -eq $dstRequirementsHash)
  }

  if ($cinterEnvPresent -and $requirementsCurrent) {
    Info "production cinterenv dependencies are current (skip): $dstCinterEnvDir"
  } elseif (Test-Path $srcCinterEnvDir) {
    if ($cinterEnvPresent) {
      Info "Refresh production worker cinterenv because dependencies changed: $srcCinterEnvDir -> $dstCinterEnvDir"
    } else {
      Info "Copy production worker cinterenv: $srcCinterEnvDir -> $dstCinterEnvDir"
    }
    Robocopy-Safe $srcCinterEnvDir $dstCinterEnvDir
  } else {
    Warn "App cinterenv missing: $srcCinterEnvDir"
  }
  Rewrite-CinterEnvPyVenvCfg $dstCinterEnvDir $PyRoot

  # Keep site-packages in app\cinterenv. The bridge adds this path explicitly.
  
  $torchDLL = Join-Path $AppDir "torch.dll"
  if ((Test-Path $torchDLL)) {
    Warn "torch installed: $torchDLL"
  } else {
    Info "production cinterenv site-packages present: $dstCinterEnvSite"

    $TorchLib = Join-Path $dstCinterEnvSite "torch\lib"
    if (Test-Path $TorchLib) {
      Info "Copy torch native DLLs next to EXE"
      Get-ChildItem -Path $TorchLib -Filter "*.dll" -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -Force $_.FullName (Join-Path $AppDir $_.Name)
      }
    }
  }

  $StageMarker = Join-Path $PyRoot ".staged.ok"
  New-Item -ItemType File -Force -Path $StageMarker | Out-Null
  
  Info "Done staging."
}
catch {
  Warn ("Staging error: " + $_.ToString())
}

exit 0
