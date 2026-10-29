#!/usr/bin/env python3
"""macOS Steam non-Steam shortcut registration for the CinterForge fork.

Steam's shortcuts.vdf is undocumented. Refuse unknown types rather than corrupt data.
Run only while Steam is stopped; preserve an atomic backup.
"""
import argparse
import time
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

STEAM = Path.home() / 'Library/Application Support/Steam'

def string(buf, pos):
    end = buf.find(b'\0', pos)
    if end < 0: raise ValueError('unterminated VDF string')
    return buf[pos:end].decode('utf-8', 'surrogateescape'), end + 1

def parse_obj(buf, pos=0):
    result = []
    while pos < len(buf):
        typ = buf[pos]; pos += 1
        if typ == 8: return result, pos
        key, pos = string(buf, pos)
        if typ == 0: value, pos = parse_obj(buf, pos)
        elif typ == 1: value, pos = string(buf, pos)
        elif typ == 2:
            if pos + 4 > len(buf): raise ValueError('truncated VDF int')
            value = struct.unpack_from('<i', buf, pos)[0]; pos += 4
        elif typ == 7:
            if pos + 8 > len(buf): raise ValueError('truncated VDF uint64')
            value = struct.unpack_from('<Q', buf, pos)[0]; pos += 8
        else: raise ValueError(f'unsupported VDF type {typ} at {pos - 1}')
        result.append((typ, key, value))
    raise ValueError('missing VDF object terminator')

def emit_obj(entries):
    out = bytearray()
    for typ, key, value in entries:
        out.append(typ); out.extend(key.encode('utf-8', 'surrogateescape') + b'\0')
        if typ == 0: out.extend(emit_obj(value))
        elif typ == 1: out.extend(value.encode('utf-8', 'surrogateescape') + b'\0')
        elif typ == 2: out.extend(struct.pack('<i', value))
        elif typ == 7: out.extend(struct.pack('<Q', value))
        else: raise ValueError(f'unsupported type {typ}')
    out.append(8)
    return bytes(out)

def field(entries, name, default=''):
    return next((v for _, k, v in entries if k.lower() == name.lower()), default)

def steam_users(root):
    userdata = root / 'userdata'
    if not userdata.is_dir(): raise RuntimeError(f'No Steam userdata at {userdata}; sign in to Steam first')
    users = [p for p in userdata.iterdir() if p.is_dir() and p.name.isdigit()]
    login = root / 'config/loginusers.vdf'
    if login.is_file():
        content = login.read_text(errors='replace')
        recent = re.findall(r'"(\d{5,})"\s*\{[^{}]*"MostRecent"\s*"1"', content, re.S | re.I)
        for user in recent:
            selected = userdata / user
            if selected in users: return [selected]
    if len(users) == 1: return users
    raise RuntimeError('Steam user is ambiguous; pass --steam-user <numeric Steam account ID>')

def steam_running():
    result = subprocess.run(['/usr/bin/pgrep', '-if', r'(^|/)steam_osx( |$)|Steam Helper'], capture_output=True)
    return result.returncode == 0

def register(path, app, icon, name):
    raw = path.read_bytes() if path.exists() else b'\x00shortcuts\x00\x08\x08'
    obj, pos = parse_obj(raw)
    if pos != len(raw) or len(obj) != 1 or obj[0][0] != 0 or obj[0][1].lower() != 'shortcuts':
        raise ValueError(f'Unrecognized Steam shortcuts structure: {path}')
    entries = obj[0][2]
    exe = '"' + str(app / 'Contents/MacOS/cfos-vm') + '"'
    start = '"' + str(app / 'Contents/MacOS') + '"'
    matches = [(i, t, k, v) for i, (t, k, v) in enumerate(entries) if t == 0 and field(v, 'AppName') == name]
    if len(matches) > 1: raise RuntimeError('Duplicate CinterForge OS shortcuts found; refusing to guess which to update')
    appid = field(matches[0][3], 'appid') if matches else struct.unpack('<i', struct.pack('<I', (zlib.crc32((exe + name).encode()) | 0x80000000)))[0]
    values = [(2, 'appid', appid), (1, 'AppName', name), (1, 'Exe', exe), (1, 'StartDir', start),
              (1, 'icon', str(icon)), (1, 'ShortcutPath', ''), (1, 'LaunchOptions', ''),
              (2, 'IsHidden', 0), (2, 'AllowDesktopConfig', 1), (2, 'AllowOverlay', 1),
              (2, 'OpenVR', 0), (2, 'Devkit', 0), (1, 'DevkitGameID', ''), (2, 'DevkitOverrideAppID', 0),
              (2, 'LastPlayTime', 0), (0, 'tags', [])]
    if matches:
        i, _, key, old = matches[0]
        replacements = {k.lower(): (t, k, v) for t, k, v in values}
        updated = [replacements.pop(k.lower(), (t, k, v)) for t, k, v in old]
        updated.extend(replacements.values())
        entries[i] = (0, key, updated)
    else:
        nextid = max((int(k) for t, k, _ in entries if t == 0 and k.isdigit()), default=-1) + 1
        entries.append((0, str(nextid), values))
    encoded = emit_obj(obj)
    parsed, end = parse_obj(encoded)
    if end != len(encoded) or parsed != obj: raise ValueError('VDF roundtrip validation failed')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists(): shutil.copy2(path, path.with_suffix('.vdf.cinterforge-backup'))
    fd, tmp = tempfile.mkstemp(prefix='shortcuts-', suffix='.vdf', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(encoded); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    print(f'CinterForge Steam: registered {name} in {path}')

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--app', required=True, type=Path)
    p.add_argument('--icon', type=Path)
    p.add_argument('--steam-root', type=Path, default=STEAM)
    p.add_argument('--steam-user')
    p.add_argument('--restart-steam', action='store_true')
    args = p.parse_args()
    app = args.app.resolve()
    if not (app / 'Contents/MacOS/cfos-vm').is_file(): p.error(f'Not a staged CinterForge OS app: {app}')
    was_running = steam_running()
    if was_running:
        if not args.restart_steam: p.error('Steam is running. Quit Steam completely, then rerun Stage; no shortcut was changed.')
        subprocess.run(['/usr/bin/osascript', '-e', 'tell application "Steam" to quit'], check=False)
        for _ in range(60):
            if not steam_running(): break
            time.sleep(1)
        if steam_running(): p.error('Steam did not quit within 60 seconds; shortcuts left unchanged')
    root = args.steam_root.expanduser().resolve()
    if args.steam_user:
        if not args.steam_user.isdigit(): p.error('--steam-user must be numeric')
        users = [root / 'userdata' / args.steam_user]
        if not users[0].is_dir(): p.error(f'Steam user not found: {users[0]}')
    else: users = steam_users(root)
    icon = args.icon or app / 'Contents/Resources/CinterForge.icns'
    if not icon.exists(): icon = app / 'Contents/Resources/AppIcon.icns'
    for user in users: register(user / 'config/shortcuts.vdf', app, icon, 'CinterForge OS')
    if was_running: subprocess.run(['/usr/bin/open', '-a', 'Steam'], check=False)

if __name__ == '__main__':
    try: main()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'CinterForge Steam: {exc}', file=sys.stderr)
        sys.exit(1)
