#!/usr/bin/env python3
"""Inventory a real Debian AMD64 installation before/after each guest install.

Executed in the existing persistent Debian guest via Rosetta. All results live
in the host's visible CreativeSuite/Linux/Inspection/<app> directory. No fake
installation claims, no new VM, and no application-specific logic.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

ROOTS = ("/usr", "/opt", "/etc", "/var/lib", "/var/cache", "/var/spool", "/home", "/root", "/bin", "/sbin", "/lib", "/lib64")
SKIP = ("/var/lib/docker",)

def inventory():
    found = {}
    for root in ROOTS:
        if not os.path.lexists(root):
            continue
        todo = [root]
        while todo:
            current = todo.pop()
            if current in found or current in SKIP:
                continue
            try:
                info = os.lstat(current)
            except (OSError, PermissionError):
                continue
            kind = ('symlink' if stat.S_ISLNK(info.st_mode) else
                    'directory' if stat.S_ISDIR(info.st_mode) else
                    'file' if stat.S_ISREG(info.st_mode) else 'special')
            item = {'type':kind,'mode':oct(stat.S_IMODE(info.st_mode)),
                    'uid':info.st_uid,'gid':info.st_gid,'size':info.st_size}
            if kind == 'symlink':
                item['target'] = os.readlink(current)
            elif kind == 'file':
                digest = hashlib.sha256()
                try:
                    with open(current,'rb') as f:
                        for block in iter(lambda: f.read(1048576), b''):
                            digest.update(block)
                    item['sha256'] = digest.hexdigest()
                except OSError as exc:
                    item['readError'] = str(exc)
            elif kind == 'directory':
                try:
                    with os.scandir(current) as it:
                        todo.extend(e.path for e in it)
                except OSError as exc:
                    item['readError'] = str(exc)
            found[current] = item
    return found

def packages():
    result = subprocess.run(['dpkg-query','-W','-f=${binary:Package}\t${Version}\t${Architecture}\t${source:Package}\n'],
                            capture_output=True,text=True,check=True)
    return sorted(x for x in result.stdout.splitlines() if x)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=['before','after'],required=True)
    parser.add_argument('--app',required=True)
    args=parser.parse_args()
    if not args.app or '/' in args.app or args.app in ('.','..'):
        parser.error('unsafe application name')
    destination=Path('/mnt/cinter-apps/Inspection')/args.app.replace(' ','_')
    destination.mkdir(parents=True,exist_ok=True)
    data={'schemaVersion':1,'application':args.app,'phase':args.phase,
          'filesystem':inventory(),'packages':packages()}
    temporary=destination/(args.phase+'.json.tmp')
    temporary.write_text(json.dumps(data,sort_keys=True,separators=(',',':'))+'\n')
    temporary.replace(destination/(args.phase+'.json'))
    if args.phase == 'after':
        before=json.loads((destination/'before.json').read_text())
        a=before['filesystem']; b=data['filesystem']
        delta={'added':{k:b[k] for k in b.keys()-a.keys()},
               'deleted':{k:a[k] for k in a.keys()-b.keys()},
               'modified':{k:{'before':a[k],'after':b[k]} for k in a.keys()&b.keys() if a[k]!=b[k]}}
        before_pkg=set(before['packages']); after_pkg=set(data['packages'])
        result={'schemaVersion':1,'application':args.app,'status':'captured',
                'addedPackages':sorted(after_pkg-before_pkg),
                'removedPackages':sorted(before_pkg-after_pkg),
                'filesystemDelta':delta,
                'counts':{kind:len(items) for kind,items in delta.items()}}
        temporary=destination/'InstallationDelta.json.tmp'
        temporary.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
        temporary.replace(destination/'InstallationDelta.json')
        print('CINTER_INSTALL_INSPECTION_COMPLETE '+args.app+' '+str(result['counts']),flush=True)
    else:
        (destination/'InstallationDelta.json').unlink(missing_ok=True)
        print('CINTER_INSTALL_BASELINE_COMPLETE '+args.app+' files='+str(len(data['filesystem'])),flush=True)

if __name__=='__main__':
    main()
