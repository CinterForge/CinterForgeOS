#!/usr/bin/env bash
# Package an installed CFOS Release and its independently staged guest commands.
# Publication of these archives to a remote destination remains separately authorized.
cfos_publish() {
  [[ "$CFOS_CONFIGURATION" == Release ]] || { echo 'CFOS: Publish requires Release.' >&2; return 2; }
  local output="$CFOS_WORKSPACE/Builds/CreativeSuite/Zipped"
  if [[ "$CFOS_PLATFORM" == linux ]]; then
    local built="$CFOS_WORKSPACE/Builds/CreativeSuite/Built/cinterforge-os/linux/2026/Ubuntu/Ubuntu-24.04-amd64-rootfs.tar"
    local installed
    if (( ! CFOS_DRY_RUN )); then
      local steamapps
      steamapps="$(cfos_steamapps)" || return 1
      installed="$steamapps/common/CinterForge/Applications/Linux/CinterForgeOS/2026/Ubuntu/Distro/Ubuntu-24.04-amd64-rootfs.tar"
      [[ -s "$built" && -s "$installed" ]] || { echo 'CFOS: Linux Build and Stage must complete before Publish.' >&2; return 1; }
      cmp -s "$built" "$installed" || { echo 'CFOS: installed Linux archive does not match Build.' >&2; return 1; }
    fi
    # Only a successfully tested installed rootfs may become a Release archive.
    # Built remains the compiler output; Zipped holds installable release units.
    cfos_linux verify || return 1
    local archive="$output/CinterForgeOS-linux-amd64-2026.zip"
    if (( CFOS_DRY_RUN )); then
      echo "CFOS: would package validated Ubuntu Distro -> $archive"
    else
      mkdir -p "$output" || return 1
      python3 - "$built" "$archive" <<'PYZIP' || return 1
import hashlib, json, os, pathlib, sys, zipfile
source, target = map(pathlib.Path, sys.argv[1:])
member = 'Applications/Linux/CinterForgeOS/2026/Ubuntu/Distro/' + source.name
hasher = hashlib.sha256()
with source.open('rb') as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        hasher.update(block)
manifest = {
    'schema': 'cinterforge.release.package.v1',
    'product': 'cinterforge-os', 'platform': 'linux-amd64', 'version': '2026',
    'source': 'validated-installed-ubuntu-distro',
    'contents': [{'path': member, 'sha256': hasher.hexdigest(), 'bytes': source.stat().st_size}],
    'policy': 'first-party OS archive; proprietary publisher installers and user data excluded',
}
partial = target.with_name(target.name + '.partial')
try:
    with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
        bundle.write(source, member)
        bundle.writestr('release-manifest.json', json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    with zipfile.ZipFile(partial) as check:
        if check.testzip() is not None:
            raise RuntimeError('CFOS Linux release ZIP CRC check failed')
        if check.getinfo(member).file_size != source.stat().st_size:
            raise RuntimeError('CFOS Linux release ZIP size mismatch')
    os.replace(partial, target)
except BaseException:
    partial.unlink(missing_ok=True)
    raise
print(f'CFOS: packaged Ubuntu Release: {target} ({source.stat().st_size:,} source bytes)')
PYZIP
    fi
  else
    local app external="${CFOS_WORKSPACE}/Builds/CreativeSuite/Staged/mac/cinterforge-os/External/Current"
    local archive="$output/CinterForgeOS-mac-2026.zip"
    if (( CFOS_DRY_RUN )); then
      cfos_run ditto -c -k '<staged CFOS Release app and external dispatcher>' "$archive"
      echo "CFOS: would validate and update $output/CinterForgeOS-mac-latest.zip and its SHA-256 metadata"
    else
      app="$(cfos_installed_app Release)" || return 1
      cfos_validate_app "$app" || return 1
      [[ -s "$external/CinterDispatcher" && -d "$external/TestScripts" ]] || {
        echo 'CFOS: Stage must install the external guest dispatcher and scripts before Publish.' >&2
        return 1
      }
      mkdir -p "$output"
      local temporary
      temporary="$(mktemp -d "$output/CFOS-mac-release-XXXXXX")" || return 1
      # Distribution layout matches persistent Applications ownership; the
      # installer can place the app and guest scripts without a source checkout.
      mkdir -p "$temporary/Applications/macOS/CinterForgeOS/2026" \
               "$temporary/Applications/Test/Dispatcher" \
               "$temporary/Applications/Test/Scripts"
      if ! ditto "$app" "$temporary/Applications/macOS/CinterForgeOS/2026/CinterForge OS.app" \
          || ! cp "$external/CinterDispatcher" "$temporary/Applications/Test/Dispatcher/Cinter" \
          || ! ditto "$external/TestScripts" "$temporary/Applications/Test/Scripts"; then
        rm -rf -- "$temporary"
        echo 'CFOS: macOS package assembly failed.' >&2
        return 1
      fi
      # The manifest travels INSIDE the package, not as an untracked sibling.
      if ! python3 - "$temporary" <<'PYMANIFEST'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
files = []
for item in sorted(root.rglob('*')):
    if not item.is_file() or item.is_symlink():
        continue
    relative = item.relative_to(root).as_posix()
    digest = hashlib.sha256()
    with item.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    files.append({'path': relative, 'sha256': digest.hexdigest(), 'bytes': item.stat().st_size})
(root / 'release-manifest.json').write_text(json.dumps({
    'schema': 'cinterforge.release.package.v1',
    'product': 'cinterforge-os', 'platform': 'macos-arm64', 'version': '2026',
    'source': 'validated-staged-macos-app', 'contents': files,
    'policy': 'first-party release; third-party installers and user data excluded',
}, indent=2, sort_keys=True) + '\n')
PYMANIFEST
      then
        rm -rf -- "$temporary"
        echo 'CFOS: macOS release manifest failed.' >&2
        return 1
      fi
      # The latest alias may be hardlinked to the last committed versioned
      # ZIP. Never mutate that inode in place: write a fresh temporary ZIP,
      # validate it, and atomically replace the versioned file instead.
      local archive_partial="${archive}.partial"
      if [[ -e "$archive_partial" ]]; then
        rm -rf -- "$temporary"
        echo "CFOS: unreconciled macOS package transaction: $archive_partial" >&2
        return 1
      fi
      if ! ditto -c -k "$temporary" "$archive_partial"; then
        rm -f -- "$archive_partial"
        rm -rf -- "$temporary"
        echo 'CFOS: macOS release ZIP assembly failed.' >&2
        return 1
      fi
      if ! python3 - "$archive_partial" <<'PYZIPCHECK'
import json
from pathlib import Path
import sys
import zipfile

archive = Path(sys.argv[1])
with zipfile.ZipFile(archive) as bundle:
    if bundle.testzip() is not None:
        raise RuntimeError('CFOS macOS package ZIP CRC validation failed')
    names = bundle.namelist()
    manifests = [n for n in names if n == 'release-manifest.json' or n.endswith('/release-manifest.json')]
    if len(manifests) != 1:
        raise RuntimeError('CFOS macOS package has no unique release manifest')
    manifest = json.loads(bundle.read(manifests[0]))
    if (manifest.get('schema'), manifest.get('product'), manifest.get('platform')) != (
            'cinterforge.release.package.v1', 'cinterforge-os', 'macos-arm64'):
        raise RuntimeError('CFOS macOS package manifest identity mismatch')
    if not any(n.endswith('CinterForge OS.app/Contents/MacOS/cfos-vm') for n in names):
        raise RuntimeError('CFOS macOS package lacks executable')
PYZIPCHECK
      then
        rm -f -- "$archive_partial"
        rm -rf -- "$temporary"
        echo 'CFOS: macOS release ZIP validation failed.' >&2
        return 1
      fi
      if ! mv -f -- "$archive_partial" "$archive"; then
        rm -rf -- "$temporary"
        echo 'CFOS: unable to commit macOS release ZIP.' >&2
        return 1
      fi
      rm -rf -- "$temporary"
      # A fixed download URL must never point to a partial or invalid package.
      # Publish both the immutable version and the stable alias through the
      # classified Builds publisher; local Publish performs NO network upload.
      python3 - "$archive" "$output/CinterForgeOS-mac-latest.zip" <<'PYLATEST' || return 1
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

source, latest = map(Path, sys.argv[1:])
if not source.is_file() or source.stat().st_size == 0:
    raise RuntimeError(f'Missing validated macOS release archive: {source}')
with zipfile.ZipFile(source) as bundle:
    corrupt = bundle.testzip()
    if corrupt is not None:
        raise RuntimeError(f'Invalid macOS release ZIP member: {corrupt}')
    members = bundle.namelist()
    manifests = [name for name in members if name == 'release-manifest.json' or name.endswith('/release-manifest.json')]
    if len(manifests) != 1:
        raise RuntimeError('macOS release must contain exactly one release-manifest.json')
    manifest = json.loads(bundle.read(manifests[0]))
    if (manifest.get('schema'), manifest.get('product'), manifest.get('platform')) != (
            'cinterforge.release.package.v1', 'cinterforge-os', 'macos-arm64'):
        raise RuntimeError('macOS release manifest identity mismatch')
    if not any(name.endswith('CinterForge OS.app/Contents/MacOS/cfos-vm') for name in members):
        raise RuntimeError('macOS release ZIP is missing the native CFOS executable')

checksum = hashlib.sha256()
with source.open('rb') as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        checksum.update(block)
# Create a verified alias on the same filesystem without a second large
# local disk copy where hardlinks are supported. Never leave .partial as latest.
partial = latest.with_name(latest.name + '.partial')
if partial.exists():
    raise RuntimeError(f'Unreconciled macOS latest-release transaction: {partial}')
try:
    try:
        os.link(source, partial)
    except OSError:
        shutil.copy2(source, partial)
    if partial.stat().st_size != source.stat().st_size:
        raise RuntimeError('Latest macOS release archive size mismatch')
    os.replace(partial, latest)
finally:
    partial.unlink(missing_ok=True)
metadata = {
    'schema': 'cinterforge.release.alias.v1',
    'product': 'cinterforge-os',
    'platform': 'macos-arm64',
    'version': manifest.get('version'),
    'object_key': 'CreativeSuite/Zipped/' + latest.name,
    'versioned_object_key': 'CreativeSuite/Zipped/' + source.name,
    'sha256': checksum.hexdigest(),
    'bytes': source.stat().st_size,
}
metadata_path = latest.with_suffix('.json')
metadata_partial = metadata_path.with_name(metadata_path.name + '.partial')
if metadata_partial.exists():
    raise RuntimeError(f'Unreconciled release metadata transaction: {metadata_partial}')
try:
    metadata_partial.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
    os.replace(metadata_partial, metadata_path)
finally:
    metadata_partial.unlink(missing_ok=True)
print(f'CFOS: latest macOS Release: {latest} sha256={checksum.hexdigest()}')
PYLATEST
    fi
  fi
  echo "CFOS: Prepared local Release in $output; use Server's classified Builds publisher for Cloudflare (dev and explicitly approved production)."
}
