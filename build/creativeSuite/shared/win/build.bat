@echo off
setlocal EnableExtensions DisableDelayedExpansion

REM The script lives at creativeSuite/shared/win. Resolve the checkout and its
REM sibling Builds with the existing lightweight, centralized Python IO module.
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..\..\..\..\..") do set "ROOT_DIR=%%~fI"
set "PYTHONPATH=%ROOT_DIR%\CinterForgeServer"
for /f "usebackq delims=" %%I in (`python -c "from cf.io.CinterIO import cinterforgeBuildsRootPath; print(cinterforgeBuildsRootPath())"`) do set "BUILDS_ROOT=%%I"
if not defined BUILDS_ROOT (
    echo ERROR: Could not resolve the shared sibling Builds directory.
    exit /b 1
)

REM Build Hugo output once into the canonical, persistent distribution tree.
REM Do not delete the tree: it belongs to other producers and the publisher.
where hugo >nul 2>&1
if errorlevel 1 (
    echo ERROR: Hugo not found in PATH.
    exit /b 1
)
hugo --source "%ROOT_DIR%\CinterForgeWebApp" -e staging --destination "%BUILDS_ROOT%\websites\dev"
if errorlevel 1 (
    echo ERROR: Hugo build failed.
    exit /b 1
)
if not exist "%BUILDS_ROOT%\websites\dev\index.html" (
    echo ERROR: Hugo did not produce websites\dev\index.html.
    exit /b 1
)

if not exist "%BUILDS_ROOT%\Server" mkdir "%BUILDS_ROOT%\Server"
copy /Y "%ROOT_DIR%\CinterForgeServer\cf\csRuntime\CinterRuntime.py" "%BUILDS_ROOT%\Server\CinterRuntime.py" >nul
if errorlevel 1 (
    echo ERROR: Could not stage portable CinterRuntime.py.
    exit /b 1
)

echo Hugo and portable runtime staged into %BUILDS_ROOT%.
endlocal
exit /b 0
