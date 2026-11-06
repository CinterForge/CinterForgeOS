"""macOS private dependency build orchestration.

Shared helpers live in creativeSuite/shared, one package level above mac.
Do not import .build_runtime: no shared/mac/build_runtime.py exists.
"""
from __future__ import annotations

from pathlib import Path

from dependencies import Dependency, build_dependencies


def build_private_dependencies(*, dependencies: tuple[Dependency, ...], prefix: Path,
                               source_cache: Path,
                               environment: dict[str, str] | None = None,
                               patch_catalog: Path | None = None) -> None:
    """Build the application's pinned sources into its private macOS prefix."""
    print(f"[shared][mac][dependencies] START {len(dependencies)} recipes -> {prefix}", flush=True)
    build_dependencies(dependencies, prefix=prefix, source_cache=source_cache,
                       environment=environment, patch_catalog=patch_catalog,
                       target_platform="mac")


# Generic Homebrew and pkg-config provisioning. Policy lives in recipes/app Setup.
import shutil as _shutil
import subprocess as _subprocess

def run(*args: str) -> None:
    print('[Creative Suite mac][dependencies] ' + ' '.join(args), flush=True)
    _subprocess.run(args, check=True)

_installed_brew_formulas: set[str] | None = None


def _brew_installed_formulas() -> set[str]:
    """Read Homebrew's installed formula inventory once per Setup process.

    Repeated ``brew list --versions <formula>`` starts a full Homebrew process
    for every dependency; Krita's dependency graph makes that costly even
    when every dependency is already installed.
    """
    global _installed_brew_formulas
    if _installed_brew_formulas is None:
        result = _subprocess.run(
            ['brew', 'list', '--formula', '--versions'],
            check=True,
            capture_output=True,
            text=True,
        )
        _installed_brew_formulas = {
            line.split()[0]
            for line in result.stdout.splitlines()
            if line.strip()
        }
    return _installed_brew_formulas


def provision_brew(formulas: tuple[str, ...], *, install_formula=None) -> None:
    if not _shutil.which('brew'):
        raise RuntimeError('Homebrew is required for macOS source dependencies')
    installed = _brew_installed_formulas()
    for formula in formulas:
        # A tapped formula may appear with its fully qualified tap name.
        if formula in installed or (
            '/' in formula and formula.rsplit('/', 1)[-1] in installed
        ):
            print(f'[Creative Suite mac][dependencies] Reuse {formula}', flush=True)
            continue
        if install_formula is not None:
            install_formula(formula)
        else:
            run('brew', 'install', formula)
        # A successful installation is now available to subsequent checks
        # within this Setup invocation; never cache a failed installation.
        installed.add(formula)

def provision_ecm() -> None:
    provision_brew(('extra-cmake-modules',))
    from pathlib import Path
    prefix = _subprocess.check_output(['brew', '--prefix', 'extra-cmake-modules'], text=True).strip()
    config = Path(prefix) / 'share/ECM/cmake/ECMConfig.cmake'
    if not config.is_file():
        raise RuntimeError(f'Installed extra-cmake-modules lacks {config}')
    print(f'[Creative Suite mac][dependencies] ECMConfig: {config}', flush=True)

def provision_pkg_config(formula: str, module: str, minimum: str | None = None) -> None:
    provision_brew((formula,))
    if not _shutil.which('pkg-config'):
        raise RuntimeError('pkg-config is required')
    check = ['pkg-config']
    if minimum:
        check.append(f'--atleast-version={minimum}')
    check.append(module)
    run(*check)
    run('pkg-config', '--modversion', module)
