#!/usr/bin/env python3
"""Embed checked-in Creative Suite guest scripts into a LinuxKit configuration.

Source linuxkit.yml is never modified. The rendered file is a generated build
input and should be written beside the consuming LinuxKit build output.
"""
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parent
SUITE = ROOT.parents[1] / 'guest/linuxkit'
SCRIPT_ROOT = Path('usr/local/lib/cinter-creative-suite')


def render(output: Path) -> Path:
    source = yaml.safe_load((ROOT / 'linuxkit.yml').read_text())
    if not isinstance(source, dict) or not isinstance(source.get('files'), list):
        raise ValueError('linuxkit.yml must contain a files list')
    scripts = (sorted((SUITE / 'env').glob('*.sh'))
               + sorted((SUITE / 'apps').glob('*/*/*.sh')))
    if not scripts:
        raise ValueError('No Creative Suite scripts found')
    for script in scripts:
        if script.suffix == '.sh':
            subprocess.run(['sh', '-n', str(script)], check=True)
    existing = {entry['path'] for entry in source['files']}
    for script in scripts:
        relative = (script.relative_to(SUITE) if script.is_relative_to(SUITE)
                    else script.relative_to(ROOT.parent))
        path = (SCRIPT_ROOT / relative).as_posix()
        if path in existing:
            raise ValueError(f'Duplicate LinuxKit file path: {path}')
        source['files'].append({'path': path, 'mode': '0755', 'contents': script.read_text()})
        existing.add(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(source, sort_keys=False, width=120, allow_unicode=True))
    print(f'CINTER_CREATIVE_SUITE_LINUXKIT_RENDERED path={output} scripts={len(scripts)}')
    return output


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: render_linuxkit.py OUTPUT_YML')
    render(Path(sys.argv[1]).resolve())
