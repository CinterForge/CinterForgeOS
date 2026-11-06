# Native Windows installed-product smoke (when invoked on Windows directly).
param([ValidateSet('gui','headless')][string]$Mode = 'gui')
$ErrorActionPreference = 'Stop'
$steam = Join-Path $env:ProgramFiles '(x86)\Steam\steamapps\common\CinterForge\Applications\Windows\CinterForge 3D'
if (-not (Test-Path -LiteralPath $steam -PathType Container)) { throw "CinterForge 3D is not installed: $steam" }
$version = $env:CINTER_APP_VERSION
if (-not $version) { $version = (Get-ChildItem -LiteralPath $steam -Directory | Sort-Object Name -Descending | Select-Object -First 1).Name }
$installed = Join-Path $steam $version
$executables = @(Get-ChildItem -LiteralPath $installed -Filter cf3d.exe -File -Recurse)
if ($executables.Count -ne 1) { throw "Expected one installed cf3d.exe in $installed, found $($executables.Count)" }
$results = Join-Path $installed 'Tests\Results'
New-Item -Path $results -ItemType Directory -Force | Out-Null
$blend = Join-Path $results 'BlenderSmoke.blend'
$glb = Join-Path $results 'BlenderSmoke.glb'
Remove-Item -LiteralPath $blend, $glb -ErrorAction SilentlyContinue
$expression = "import bpy; bpy.ops.wm.read_factory_settings(use_empty=True); bpy.ops.mesh.primitive_cube_add(); bpy.ops.wm.save_as_mainfile(filepath=r'$blend'); bpy.ops.export_scene.gltf(filepath=r'$glb', export_format='GLB')"
& $executables[0].FullName --background --factory-startup --python-expr $expression *> (Join-Path $results 'SmokeTest.log')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $blend) -or -not (Test-Path -LiteralPath $glb)) { throw 'Installed Blender document/export smoke failed' }
if ($Mode -eq 'gui') {
    $process = Start-Process -FilePath $executables[0].FullName -ArgumentList @('--factory-startup') -PassThru
    try {
        Start-Sleep -Seconds 6
        $process.Refresh()
        if ($process.HasExited) { throw "Installed Blender GUI exited: $($process.ExitCode)" }
    } finally {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    }
}
Write-Host "CinterForge 3D installed Windows $Mode smoke passed: $blend $glb"
