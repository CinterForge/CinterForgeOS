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

def dialog_script():
    # The installer can point to its own editable copy when using the fork.
    override = os.environ.get('CFOS_STEAM_DIALOG_SCRIPT')
    path = Path(override) if override else Path(__file__).resolve().with_name('steam_dialogs.applescript')
    if not path.is_file():
        raise RuntimeError(f'Missing customizable Steam dialog script: {path}')
    return path


def show_quit_dialog():
    subprocess.run(['/usr/bin/osascript', str(dialog_script()), 'quit-steam'], check=False)


def steam_users(root, install_prompt=False):
    """Resolve the active account, or ask rather than silently picking one."""
    userdata = root / 'userdata'
    users = sorted((p for p in userdata.iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name)) if userdata.is_dir() else []
    login = root / 'config/loginusers.vdf'
    account_info = {}
    recent = []
    if login.is_file():
        content = login.read_text(errors='replace')
        # SteamID64 keys identify the corresponding userdata AccountID32 folder.
        for steamid, body in re.findall(r'"(\d{15,20})"\s*\{([^{}]*)\}', content, re.S):
            account_id = str(int(steamid) & 0xffffffff)
            fields = dict(re.findall(r'"([^"\r\n]+)"\s*"([^"\r\n]*)"', body))
            account_info[account_id] = fields
            if fields.get('MostRecent') == '1':
                recent.append(userdata / account_id)
        recent = [u for u in recent if u in users]
        if len(recent) == 1 and not install_prompt:
            return recent
    if len(users) == 1 and not install_prompt:
        return users
    if not users:
        raise RuntimeError('No Steam account initialized. Sign in to Steam, then return to the installer.')
    # Display PersonaName and AccountName, retaining the numeric ID only for
    # disambiguation or when Steam has no cached name for that account.
    labels = []
    for user in users:
        fields = account_info.get(user.name, {})
        persona = fields.get('PersonaName', '').strip()
        account = fields.get('AccountName', '').strip()
        if persona and account and persona != account:
            label = f'{persona} ({account})'
        else:
            label = persona or account or f'Account {user.name}'
        labels.append(label)
    for i, label in enumerate(labels):
        if labels.count(label) > 1:
            labels[i] = f'{label} — {users[i].name}'
    print('CinterForge Steam: multiple accounts found; choose the account to register.')
    dialog = dialog_script()
    default_label = next((labels[users.index(u)] for u in recent if u in users), labels[0])
    action = 'install-account' if install_prompt else 'choose-account'
    selection = subprocess.run(['/usr/bin/osascript', str(dialog), action, default_label, *labels, str(dialog.parent)],
                               capture_output=True, text=True)
    if selection.returncode == 0 and selection.stdout.strip() in labels:
        return [users[labels.index(selection.stdout.strip())]]
    if selection.returncode == 0:
        raise RuntimeError('Installation cancelled' if install_prompt else 'Steam account selection cancelled')
    if install_prompt:
        # Preserve the actual osascript error. A UI crash is not a user cancellation.
        detail = (selection.stderr or selection.stdout or '').strip()
        if detail:
            raise RuntimeError(f'Steam installer UI failed (osascript exit {selection.returncode}): {detail}')
        raise RuntimeError(f'Steam installer UI returned exit {selection.returncode} without a selection')
    # Terminal fallback when AppleScript cannot show a dialog.
    try:
        with open('/dev/tty', 'r+') as tty:
            for n, label in enumerate(labels, 1):
                print(f'  {n}. {label}', file=tty)
            while True:
                tty.write('Account number (Enter to cancel): '); tty.flush()
                answer = tty.readline().strip()
                if not answer:
                    raise RuntimeError('Steam account selection cancelled')
                if answer.isdigit() and 1 <= int(answer) <= len(users):
                    return [users[int(answer) - 1]]
    except OSError as exc:
        raise RuntimeError('Cannot prompt for Steam account selection') from exc


def initialize_steam(root, steam_app):
    """Use Steam's own interactive login, never collect passwords or tokens."""
    if (root / 'userdata').is_dir() and any(p.is_dir() and p.name.isdigit() for p in (root / 'userdata').iterdir()):
        return
    print('CinterForge Steam: opening Steam for first-time authentication.')
    result = subprocess.run(['/usr/bin/open', '-a', steam_app], check=False)
    if result.returncode:
        raise RuntimeError(f'Could not launch Steam ({steam_app}); install/sign in to Steam first')
    print('Complete Steam login in the Steam window. Waiting up to 5 minutes for an account...')
    for _ in range(300):
        userdata = root / 'userdata'
        if userdata.is_dir() and any(p.is_dir() and p.name.isdigit() for p in userdata.iterdir()):
            return
        time.sleep(1)
    raise RuntimeError('Steam sign-in did not initialize an account within 5 minutes')

def steam_running():
    result = subprocess.run(['/usr/bin/pgrep', '-if', r'(^|/)steam_osx( |$)|Steam Helper'], capture_output=True)
    return result.returncode == 0

def register(path, app, icon, name, recovered_appid=None):
    raw = path.read_bytes() if path.exists() else b'\x00shortcuts\x00\x08\x08'
    obj, pos = parse_obj(raw)
    if pos != len(raw) or len(obj) != 1 or obj[0][0] != 0 or obj[0][1].lower() != 'shortcuts':
        raise ValueError(f'Unrecognized Steam shortcuts structure: {path}')
    entries = obj[0][2]
    exe = '"' + str(app / 'Contents/MacOS/cfos-vm') + '"'
    start = '"' + str(app / 'Contents/MacOS') + '"'
    matches = [(i, t, k, v) for i, (t, k, v) in enumerate(entries) if t == 0 and field(v, 'AppName') == name]
    if len(matches) > 1: raise RuntimeError('Duplicate CinterForge OS shortcuts found; refusing to guess which to update')
    appid = field(matches[0][3], 'appid') if matches else (recovered_appid if recovered_appid is not None else struct.unpack('<i', struct.pack('<I', (zlib.crc32((exe + name).encode()) | 0x80000000)))[0])
    # Keep an existing, working icon when updating an installed shortcut.
    if matches:
        existing_icon = field(matches[0][3], 'icon')
        if existing_icon and Path(existing_icon).is_file():
            icon = Path(existing_icon)
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
    print(f'CinterForge Steam: registered {name} (appid={appid & 0xffffffff}) in {path}')
    return appid & 0xffffffff


def game_id(appid):
    return ((appid & 0xffffffff) << 32) | 0x02000000


def recover_artwork_appid(user, source):
    """Recover the old Steam shortcut ID only when its installed logo matches.

    A 13-byte shortcuts.vdf is an empty shortcut database; generating a new ID
    in that case would orphan artwork Steam already associates with the game.
    """
    import hashlib
    grid = user / 'config/grid'
    logo = source / 'logo.png'
    if not grid.is_dir() or not logo.is_file():
        return None
    wanted = hashlib.sha256(logo.read_bytes()).digest()
    matches = []
    for candidate in grid.glob('*_logo.png'):
        stem = candidate.name[:-len('_logo.png')]
        if not stem.isdigit():
            continue
        number = int(stem)
        if number > 0xffffffff and (number & 0xffffffff) == 0x02000000:
            appid = number >> 32
        elif number <= 0xffffffff:
            appid = number
        else:
            continue
        if hashlib.sha256(candidate.read_bytes()).digest() == wanted:
            matches.append(appid)
    matches = sorted(set(matches))
    if len(matches) > 1:
        raise RuntimeError(f'Ambiguous matching Steam artwork IDs: {matches}; refusing to create a duplicate shortcut')
    return matches[0] if matches else None


def install_artwork(user, appid, source):
    """Restore the existing hero artwork for both Steam shortcut ID conventions.

    Preserve the working sidebar icon and shortcut identity.
    """
    if not source.is_dir():
        raise RuntimeError(f'Missing Steam artwork directory: {source}')
    grid = user / 'config/grid'
    grid.mkdir(parents=True, exist_ok=True)
    gid = game_id(appid)
    assets = {
        'hero.png': f'{gid}_hero.png',
        'logo.png': f'{gid}_logo.png',
        'portrait.png': f'{gid}p.png',
        'wide.png': f'{gid}.png',
        'icon.png': f'{appid}_icon.png',
    }
    for filename in assets:
        if not (source / filename).is_file():
            raise RuntimeError(f'Missing Steam artwork: {source / filename}')
    # Steam versions differ in which non-Steam ID they use for library art.
    # Install identical assets for both, without changing the shortcut itself.
    for filename, target in assets.items():
        destinations = [target]
        if filename != 'icon.png':
            suffix = target[len(str(gid)):]
            destinations.append(f'{appid}{suffix}')
        for destination_name in dict.fromkeys(destinations):
            destination = grid / destination_name
            fd, temporary = tempfile.mkstemp(prefix='.cfos-art-', dir=grid)
            try:
                with os.fdopen(fd, 'wb') as output, (source / filename).open('rb') as input_file:
                    shutil.copyfileobj(input_file, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    print(f'CinterForge Steam: installed hero/logo/capsules for gameid={gid} and icon for appid={appid} in {grid}', flush=True)
    return grid / f'{appid}_icon.png'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--app', type=Path)
    p.add_argument('--preflight', action='store_true', help='Authenticate/select account before Setup and Build')
    p.add_argument('--install-dialog', action='store_true', help='Show the combined Install/Cancel account picker during preflight')
    p.add_argument('--icon', type=Path)
    p.add_argument('--steam-root', type=Path, default=STEAM)
    p.add_argument('--steam-user')
    p.add_argument('--restart-steam', action='store_true')
    p.add_argument('--steam-app', default='Steam', help='Steam application name or absolute .app path')
    args = p.parse_args()
    if not args.preflight and not args.app: p.error('--app is required for registration')
    app = args.app.resolve() if args.app else None
    if app and not (app / 'Contents/MacOS/cfos-vm').is_file(): p.error(f'Not a staged CinterForge OS app: {app}')
    root = args.steam_root.expanduser().resolve()
    if not args.steam_user:
        initialize_steam(root, args.steam_app)
    was_running = steam_running()
    if was_running:
        print('CinterForge Steam: Steam is running. Please quit Steam completely to continue installation.', flush=True)
        # Never terminate Steam ourselves. Give the user control over when to quit.
        show_quit_dialog()
        elapsed = 0
        while steam_running():
            time.sleep(2)
            elapsed += 2
            if elapsed % 30 == 0:
                print('CinterForge Steam: waiting for you to quit Steam...', flush=True)
        print('CinterForge Steam: Steam has exited; continuing registration.', flush=True)
    if args.steam_user:
        if not args.steam_user.isdigit(): p.error('--steam-user must be numeric')
        users = [root / 'userdata' / args.steam_user]
        if not users[0].is_dir(): p.error(f'Steam user not found: {users[0]}')
    else: users = steam_users(root, install_prompt=args.install_dialog)
    if args.preflight:
        if len(users) != 1: raise RuntimeError('Steam preflight requires exactly one selected account')
        print(f'CFOS_STEAM_ACCOUNT_ID={users[0].name}', flush=True)
        return
    source = Path(os.environ.get('CFOS_STEAM_ARTWORK_DIR') or
                  Path(__file__).resolve().parents[2] / 'Install CinterForgeOS.app/Contents/Resources/UI/SteamArtwork')
    for user in users:
        shortcut = user / 'config/shortcuts.vdf'
        raw = shortcut.read_bytes() if shortcut.is_file() else b'\x00shortcuts\x00\x08\x08'
        obj, pos = parse_obj(raw)
        if pos != len(raw) or len(obj) != 1 or obj[0][0] != 0 or obj[0][1].lower() != 'shortcuts':
            raise RuntimeError(f'Invalid shortcuts.vdf: {shortcut}')
        existing = [v for t, k, v in obj[0][2] if t == 0 and field(v, 'AppName') == 'CinterForge OS']
        if len(existing) > 1:
            raise RuntimeError('Duplicate CinterForge OS shortcuts; refusing to guess')
        if existing:
            appid = int(field(existing[0], 'appid')) & 0xffffffff
        else:
            appid = recover_artwork_appid(user, source)
            if appid is None:
                exe = '"' + str(app / 'Contents/MacOS/cfos-vm') + '"'
                appid = zlib.crc32((exe + 'CinterForge OS').encode()) | 0x80000000
        icon = install_artwork(user, appid, source)
        registered = register(shortcut, app, icon, 'CinterForge OS', recovered_appid=struct.unpack('<i', struct.pack('<I', appid))[0])
        if registered != appid:
            raise RuntimeError(f'Steam shortcut ID mismatch: expected {appid}, got {registered}')
        check_obj, _ = parse_obj(shortcut.read_bytes())
        check = [v for t, k, v in check_obj[0][2] if t == 0 and field(v, 'AppName') == 'CinterForge OS']
        if len(check) != 1 or field(check[0], 'icon') != str(icon) or not icon.is_file():
            raise RuntimeError('Steam shortcut/icon post-install verification failed')
    if was_running and args.restart_steam: subprocess.run(['/usr/bin/open', '-a', args.steam_app], check=False)

if __name__ == '__main__':
    try: main()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'CinterForge Steam: {exc}', file=sys.stderr)
        sys.exit(1)
