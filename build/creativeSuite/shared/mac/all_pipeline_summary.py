#!/usr/bin/env python3
"""Report installed macOS bundle sizes after the aggregate lifecycle."""
import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SUITE))
from lifecycle import installation_directory, product_name  # noqa: E402


def bundle_bytes(bundle: Path) -> int:
    # Apparent disk size of files; hard links are counted only once.
    total = 0
    seen = set()
    for item in bundle.rglob('*'):
        if item.is_symlink() or not item.is_file():
            continue
        stat = item.stat()
        key = (stat.st_dev, stat.st_ino)
        if key not in seen:
            seen.add(key)
            total += stat.st_size
    return total


def main() -> None:
    rows = []
    for line in Path(sys.argv[1]).read_text().splitlines():
        app, status = line.split('\t', 1)
        name = product_name(app)
        short = name.removeprefix('CinterForge ').strip() or name
        size = '—'
        try:
            installed = installation_directory(app, 'mac')
            versions = [p for p in installed.iterdir() if p.is_dir()] if installed.is_dir() else []
            if versions:
                version = max(versions, key=lambda p: tuple(int(x) for x in p.name.split('.')) if all(x.isdigit() for x in p.name.split('.')) else (0,))
                bundles = list(version.glob('*.app'))
                if len(bundles) == 1:
                    size = f'{bundle_bytes(bundles[0]) / (1024 ** 3):.2f} GiB'
                elif len(bundles) > 1:
                    size = f'{sum(bundle_bytes(p) for p in bundles) / (1024 ** 3):.2f} GiB'
        except (OSError, RuntimeError) as exc:
            print(f'[Cinter all] Unable to inspect {app}: {exc}', file=sys.stderr)
        rows.append((short, size, status))
    widths = [max(len(label), *(len(row[i]) for row in rows)) for i, label in enumerate(('App', 'Bundle size', 'Result'))]
    border = '+' + '+'.join('-' * (w + 2) for w in widths) + '+'
    print('\n' + border)
    print('| ' + ' | '.join(label.ljust(widths[i]) for i, label in enumerate(('App', 'Bundle size', 'Result'))) + ' |')
    print(border)
    for row in rows:
        print('| ' + ' | '.join(value.ljust(widths[i]) for i, value in enumerate(row)) + ' |')
    print(border)
    print('Sizes: installed .app contents (logical file bytes); failed apps may show a prior installed version.')
    print('Successful GUI tests leave applications running; closing them is not part of this pipeline.')


if __name__ == '__main__':
    main()
