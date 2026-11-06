#!/usr/bin/env python3
"""Native CFOS model/runtime integration consumes Plugins-produced model assets.

Hugging Face catalog, downloads, and model packaging are Plugins-owned.
This native gate validates presence of Plugins products; inference acceleration,
installation, and host launch are integrated into the platform CFOS lifecycle.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
WORKSPACE=ROOT.parents[2]
GRAPH={phase:(('native-model-inputs',ROOT/'native_model_inputs.py'),) for phase in ('setup','build','stage','test')}

def plan(phase:str, builds:Path)->list:
    if phase not in GRAPH: raise ValueError(phase)
    return [{'name':name,'path':str(path.resolve()),'builds':str(builds)} for name,path in GRAPH[phase]]

def execute(phase:str,builds:Path,*,dry_run:bool=False):
    tasks=plan(phase,builds)
    if dry_run:return {'phase':phase,'tasks':tasks,'dry_run':True}
    from native_model_inputs import validate
    validation=validate(builds)
    return {'phase':phase,'owner':'CFOS-native-consumer','validation':validation,'ok':True}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=tuple(GRAPH))
    p.add_argument('--builds',type=Path,default=WORKSPACE/'Builds')
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    print(json.dumps(execute(a.phase,a.builds.resolve(),dry_run=a.dry_run),indent=2))
