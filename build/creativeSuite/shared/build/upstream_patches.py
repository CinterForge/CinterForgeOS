"""Reusable, auditable upstream dependency-patch snapshots and application.

Snapshots live in Builds/CreativeSuite/Built/<app>/<platform>/<version>/Dependencies/Patches.
Remote HTML is *data*, never a script. Dependency sources are patched only when
explicitly built, never in the maintained application checkout.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


class _PatchPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.section = ''
        self.dependency = None
        self.items = []
        self.depth = 0
        self.tag = None
        self.text = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('h1', 'h2', 'pre', 'code', 'a'):
            self.depth += 1
            self.tag = tag
            self.text.append((tag, [], attrs.get('href')))

    def handle_data(self, data):
        for _, parts, _ in self.text:
            parts.append(data)

    def handle_endtag(self, tag):
        if not self.text or self.text[-1][0] != tag:
            return
        _, parts, href = self.text.pop()
        value = ''.join(parts).strip()
        if tag == 'h1':
            if 'Inline sed' in value:
                self.section = 'inline'
            elif 'Actual patches' in value:
                self.section = 'patch'
        elif tag == 'h2':
            self.dependency = value
        elif tag in ('pre', 'code') and value and self.section and self.dependency:
            # The published page uses <pre><code> in some versions.
            if not any(entry['command'] == value and entry['dependency'] == self.dependency
                       for entry in self.items):
                self.items.append({'dependency': self.dependency, 'kind': self.section, 'command': value})
        elif tag == 'a' and href and self.section == 'patch':
            self.links.append((self.dependency, href))


def parse_patch_page(html: bytes, url: str) -> list[dict[str, str]]:
    """Parse the build-stack report, including repeated patches per library.

    The upstream report labels download anchors simply "Patch"; their hrefs
    are authoritative.  A dependency may have multiple entries (cairo has
    two), so a dependency-only lookup is ambiguous.  Match the exact filename
    from each patch command against its download link, and reject ambiguity.
    """
    parser = _PatchPage()
    parser.feed(html.decode('utf-8'))
    commands = [v for v in parser.items
                if v['command'].lstrip().startswith(('sed ', 'find ', 'patch '))]
    for entry in commands:
        if entry['kind'] != 'patch':
            continue
        name = re.search(r'/([A-Za-z0-9._+-]+\.(?:patch|diff))\b', entry['command'])
        if not name:
            raise ValueError(f'Unsupported upstream patch command: {entry}')
        filename = name.group(1)
        matches = [urljoin(url, href) for dep, href in parser.links
                   if dep == entry['dependency'] and
                   urlsplit(urljoin(url, href)).path.endswith('/' + filename)]
        if len(matches) != 1:
            raise ValueError(f'Cannot identify unique upstream patch {filename}: {matches}')
        patch_url = matches[0]
        if (urlsplit(patch_url).hostname != urlsplit(url).hostname or
                urlsplit(patch_url).scheme != 'https'):
            raise ValueError(f'Unexpected upstream patch host: {patch_url}')
        entry['url'] = patch_url
        entry['filename'] = filename
    if not commands or not any(i['kind'] == 'patch' for i in commands):
        raise ValueError('Published patch page contains no recognized instructions')
    return commands


def _download(url: str, *, limit: int = 4_000_000) -> bytes:
    if urlsplit(url).scheme != 'https':
        raise ValueError('Only HTTPS upstream patch sources are accepted')
    request = Request(url, headers={'User-Agent': 'CinterForge-CreativeSuite/1.0'})
    with urlopen(request, timeout=25) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'Upstream file exceeds size limit: {url}')
    return data


def sync_patch_catalog(*, url: str, destination: Path) -> Path:
    """Refresh a complete catalog atomically, without hidden caches or fallback.

    The returned JSON records the exact patch-content hashes. Each build checks
    the current published instructions and never executes remote shell code.
    """
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    html = _download(url)
    commands = parse_patch_page(html, url)
    assets = {}
    for instruction in commands:
        if instruction['kind'] != 'patch':
            continue
        name = instruction['filename']
        data = _download(instruction['url'])
        if name in assets and assets[name] != data:
            raise ValueError(f'Conflicting upstream patch files: {name}')
        assets[name] = data
        instruction['sha256'] = hashlib.sha256(data).hexdigest()
    snapshot = {'source': url, 'page_sha256': hashlib.sha256(html).hexdigest(),
                'instructions': commands}
    # Don't overwrite a successful previous snapshot until all downloads validate.
    for name, data in assets.items():
        _atomic_write(destination / name, data)
    _atomic_write(destination / 'patch-info.html', html)
    _atomic_write(destination / 'patch-info.json', (json.dumps(snapshot, indent=2) + '\n').encode())
    print(f'[patches] Current upstream instructions: {len(commands)}; files: {len(assets)}; {destination}', flush=True)
    return destination / 'patch-info.json'


def _atomic_write(destination: Path, content: bytes) -> None:
    fd, temp = tempfile.mkstemp(prefix='patch-download-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _relative(base: Path, filename: str) -> Path:
    if filename.startswith(('/', '-')) or '$' in filename or '\x00' in filename:
        raise ValueError(f'Unsafe dependency patch target: {filename}')
    target = (base / filename).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError(f'Dependency patch target escapes source: {filename}')
    return target


def _sed_instruction(command: str, source: Path, prefix: Path) -> None:
    # The published sed commands use BRE, which Python regex does not emulate.
    # Invoke sed directly with an argv vector, never through a shell.
    argv = shlex.split(command)
    if argv[:2] != ['sed', '-i.bak'] or len(argv) != 4:
        raise ValueError(f'Unsupported published sed syntax: {command}')
    script, filename = argv[2:]
    if not (script.startswith(('s', '/')) and script.count('\n') == 0):
        raise ValueError(f'Unsupported published sed expression: {script}')
    if filename.startswith('$PREFIX/'):
        target = _relative(prefix, filename[len('$PREFIX/'):])
    else:
        target = _relative(source, filename)
    if not target.is_file():
        raise FileNotFoundError(f'Published patch target missing: {target}')
    subprocess.run(['sed', '-i.bak', script, str(target)], check=True)
    target.with_name(target.name + '.bak').unlink(missing_ok=True)


def selected_dependency_patches(*, catalog: Path, dependency: str,
                                platform: str) -> list[dict[str, str]]:
    """Select the upstream instructions for a dependency and target platform.

    This is also used by the build cache fingerprint: a change to an unrelated
    dependency or a patch for another platform must not invalidate this build.
    """
    if platform not in ("mac", "linux", "win"):
        raise ValueError(f"Unsupported patch target platform: {platform}")
    snapshot = json.loads(Path(catalog).read_text(encoding="utf-8"))
    instructions = [item for item in snapshot["instructions"]
                    if item["dependency"].casefold() == dependency.casefold()]
    selected = []
    for item in instructions:
        cmd = item["command"]
        if platform != "mac" and any(x in cmd for x in
               ("darwin.jam", "quartz-", "arm64-clang", "-force_cpusubtype_ALL")):
            continue
        if platform == "mac" and any(x in cmd for x in
               ("coreconf/Linux.mk", "m4-glibc.patch")):
            continue
        if platform == "win" and any(x in cmd for x in
               ("coreconf/Linux.mk", "m4-glibc.patch")):
            continue
        selected.append(item)
    return selected


def apply_dependency_patches(*, catalog: Path, dependency: str, source: Path,
                             platform: str, prefix: Path, phase: str = "source") -> str:
    """Apply published transformations to one disposable dependency source.

    The caller must supply fresh unmodified dependency source on a changed
    fingerprint. Returns fingerprint for incremental cache invalidation.
    """
    if phase not in ("source", "installed"):
        raise ValueError(f"Unsupported dependency patch phase: {phase}")
    catalog = Path(catalog).resolve()
    source = Path(source).resolve()
    prefix = Path(prefix).resolve()
    selected = selected_dependency_patches(catalog=catalog, dependency=dependency,
                                           platform=platform)
    fingerprint = hashlib.sha256(json.dumps({"platform": platform, "phase": phase, "instructions": selected}, sort_keys=True).encode()).hexdigest()
    for item in selected:
        cmd = item['command']
        # build-stack includes edits to $PREFIX/lib/pkgconfig/*.pc that exist
        # only after install. Do not attempt them against source checkouts.
        installed_edit = item["kind"] == "inline" and "$PREFIX/" in cmd
        if (phase == "installed") != installed_edit:
            continue
        if item['kind'] == 'patch':
            argv = shlex.split(cmd.replace('<', ' < '))
            if len(argv) != 4 or argv[0] != 'patch' or not re.fullmatch(r'-p[0-9]', argv[1]) or argv[2] != '<':
                raise ValueError(f'Unsupported upstream patch instruction: {cmd}')
            patchfile = catalog.parent / item['filename']
            content = patchfile.read_bytes()
            if hashlib.sha256(content).hexdigest() != item['sha256']:
                raise RuntimeError(f'Upstream patch integrity mismatch: {patchfile}')
            # Patch headers are untrusted: reject absolute and escaping paths
            # before letting the native patch utility see the payload.
            strip = int(argv[1][2:])
            for line in content.decode('utf-8', errors='replace').splitlines():
                if line.startswith(('--- ', '+++ ')):
                    name = line[4:].split('\t', 1)[0].split(' ', 1)[0]
                    if name == '/dev/null':
                        continue
                    parts = Path(name).parts[strip:]
                    if not parts or any(part in ('..', '.', '') for part in parts):
                        raise ValueError(f'Unsafe upstream patch path: {name}')
                    _relative(source, '/'.join(parts))
            with patchfile.open('rb') as patch_stream:
                subprocess.run(['patch', '--batch', '--forward', argv[1], '--dry-run'],
                               cwd=source, stdin=patch_stream, check=True)
            with patchfile.open('rb') as patch_stream:
                subprocess.run(['patch', '--batch', '--forward', argv[1]],
                               cwd=source, stdin=patch_stream, check=True)
        elif cmd.lstrip().startswith('sed '):
            _sed_instruction(cmd.strip(), source, prefix)
        elif cmd.lstrip().startswith('find '):
            # Never execute arbitrary recursively downloaded shell expressions.
            match = re.fullmatch(r"find \. -type f -exec (sed -i\.bak '[^']+' \{\} \\;)", cmd.strip())
            if not match:
                raise ValueError(f'Unsupported published recursive edit: {cmd}')
            sed_command = match.group(1).replace(' {} \\;', '')
            for file in sorted(source.rglob('*')):
                if file.is_file() and not file.is_symlink() and '.git' not in file.parts:
                    rel = file.relative_to(source).as_posix()
                    _sed_instruction(sed_command + ' ' + shlex.quote(rel), source, prefix)
        else:
            raise ValueError(f'Unsupported upstream instruction: {cmd}')
    return fingerprint
