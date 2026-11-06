"""Versioned, evidence-preserving Creative Suite setup/build recipe format.

Recipes describe evidence and unresolved work, never assume that a nightly
build-stack inventory is a list of mandatory packages to install.
"""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
from urllib.parse import urlparse
import re
import shlex

RECIPE_SCHEMA = 'cinterforge.creative-suite.recipe.v1'
ROOT = Path(__file__).resolve().parent / 'apps'
ARCH = {'mac': 'arm64', 'linux': 'x86_64', 'win': 'x86_64'}
HOST = {'mac': 'macOS', 'linux': 'Linux x86-64', 'win': 'Linux x86-64'}


def recipe_path(name: str, target: str) -> Path:
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    if not slug or target not in ARCH:
        raise ValueError(f'Unsupported recipe target: {name!r}/{target!r}')
    return ROOT / slug / target / 'recipe.json'


def make_recipe(row: dict, target: str, discovery: dict, setup: dict | None = None) -> dict:
    name = row['Name']
    upstream = (row.get('Git Repo URL') or '').strip()
    repo_name = urlparse(upstream).path.rstrip('/').split('/')[-1].removesuffix('.git')
    # Maintained forks have stable local names independent of upstream URLs.
    maintained_forks = {'cf3d': 'CinterForge3D', 'cfaudio': 'CinterForgeAudio',
                        'cfdocument': 'CinterForgeDocument', 'cfpaint': 'CinterForgePaint',
                        'cfphoto': 'CinterForgePhoto', 'cfvector': 'CinterForgeVector',
                        'cfvideo': 'CinterForgeVideo'}
    checkout_name = maintained_forks.get(re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-'), repo_name)
    command = row.get(f'{target} Setup Command', '').strip()
    packages = [x.strip() for x in row.get(f'{target} Setup Packages', '').split(';') if x.strip()]
    requirements = []
    for r in discovery.get('requirements', []):
        requirements.append({'name': r['name'], 'category': r['type'], 'observed_version': r['version'],
                             'required_version': None, 'version_policy': 'observed_upstream_not_pinned',
                             'source': r.get('source_url'), 'evidence': r.get('evidence')})
    constraints = [{'name': c['name'], 'operator': c['operator'], 'version': c['version'],
                    'source': c.get('source_url'), 'evidence': c.get('evidence')}
                   for c in discovery.get('constraints', [])]
    patches = [{'component': p.get('component'), 'action': p.get('action'), 'source': p.get('source_url'),
                'execution': 'evidence_only_not_authorized'} for p in discovery.get('patches', [])]
    install_command = shlex.split(command) if command else []
    configuration = setup.get('configure') if setup else None
    configured = bool(setup and setup.get('configured', setup.get('ready')))
    # Configuration is a prerequisite, never proof that compilation works.
    ready = configured and bool(setup and setup.get('build_succeeded'))
    issues = list(discovery.get('errors', []))
    if not command:
        issues.append('No configure command in Creative Suite CSV')
    if not ready:
        issues.append('Build has not completed successfully since the latest build attempt')
    return {
        'schema': RECIPE_SCHEMA,
        'identity': {'application': name, 'platform': target, 'target_arch': ARCH[target],
                     'build_host': HOST[target], 'acquisition': discovery.get('acquisition', 'source')},
        'source': {'upstream_url': upstream, 'checkout_name': checkout_name or None,
                   'revision': None, 'submodules': 'upstream_pinned_gitlinks'},
        'toolchain': {'components': [r for r in requirements if r['category'] in ('tool', 'compiler')],
                      'detected': (setup or {}).get('detected_compilers', {}),
                      'cross_compiler': 'x86_64-w64-mingw32-gcc' if target == 'win' else None},
        'dependencies': [r for r in requirements if r['category'] == 'dependency'],
        'constraints': constraints,
        'source_requirements': discovery.get('source_requirements', []),
        'configure_checks': (setup or {}).get('configure_checks', []),
        'patches': patches,
        'preparation': {'packages': packages, 'package_manager': 'brew' if target == 'mac' else 'apt-get',
                        'audit': (setup or {}).get('packages', []),
                        'status': 'configured' if configured else 'unverified'},
        'configure': {'system': 'waf' if command.startswith('./waf ') else 'configured_command' if command else None,
                      'argv': install_command, 'observed_result': configuration},
        'build': {'system': 'waf' if command.startswith('./waf ') else None,
                  'argv_by_config': {'Debug': ['./waf', 'build'], 'Release': ['./waf', 'build']} if command.startswith('./waf ') else {},
                  'verified': ready},
        'environment': {'variables': (setup or {}).get('environment', {}), 'prefix': None, 'sdk': None},
        'outputs': {'build_destination': f'Builds/CreativeSuite/Built/{repo_name}/{target}',
                    'install_destination': f'Applications/{"macOS" if target == "mac" else "Linux" if target == "linux" else "Windows"}/{name}'},
        'evidence': {'status': discovery.get('status'), 'urls': discovery.get('evidence_urls', []),
                     'inspection': discovery.get('evidence_inspection', [])},
        'readiness': {'ready': ready, 'configured': configured,
                      'issues': sorted(set(issues)), 'setup_result': setup},
    }


def write_recipe(recipe: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(recipe, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def read_recipe(path: Path, *, application: str, target: str) -> dict | None:
    """Read the persistent recipe; never silently fall back on malformed recipes."""
    if not path.is_file():
        return None
    recipe = json.loads(path.read_text(encoding='utf-8'))
    if (recipe.get('schema') != RECIPE_SCHEMA or
            re.sub(r'[^a-z0-9]+', '-', str(recipe.get('identity', {}).get('application', '')).lower()).strip('-') !=
            re.sub(r'[^a-z0-9]+', '-', application.lower()).strip('-') or
            recipe.get('identity', {}).get('platform') != target):
        raise ValueError(f'Invalid recipe schema or identity: {path}')
    packages = recipe.get('preparation', {}).get('packages')
    argv = recipe.get('configure', {}).get('argv')
    if not isinstance(packages, list) or not all(isinstance(x, str) for x in packages):
        raise ValueError(f'Invalid preparation.packages in {path}')
    if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        raise ValueError(f'Invalid configure.argv in {path}')
    return recipe





def authored_instructions_preserved(generated: dict, authored: dict) -> bool:
    """Compare executable instructions, not stale run-state in an Inspect candidate.

    Inspect is allowed to reset historical Setup/Build success flags. Every
    other authored field, including toolchains, dependency pins, and commands,
    must survive byte-for-byte at the JSON value level. The authored recipe's
    full source SHA256 is checked independently by review_inspected_recipe.
    """
    for key, value in authored.items():
        if key == 'readiness':
            continue  # transient result, not an authored build instruction
        if key == 'build' and isinstance(value, dict):
            actual = generated.get('build')
            if not isinstance(actual, dict):
                return False
            for field, authored_value in value.items():
                if field not in ('verified', 'last_result') and actual.get(field) != authored_value:
                    return False
            continue
        if generated.get(key) != value:
            return False
    return True


def review_inspected_recipe(name: str, target: str, builds_root: Path) -> dict:
    """Validate the cached Inspect candidate against the *authored* recipe.

    Inspect is read-only; its output is evidence, not an approved executable
    build plan. During migration a missing cache must not destroy known-good
    macOS/Linux workflows. An existing but stale or modified cache is rejected
    so Setup never trusts mismatched inspection evidence.
    """
    source = recipe_path(name, target)
    candidate = (Path(builds_root) / 'CreativeSuite' / 'Inspection' / 'Sources'
                 / source.parent.parent.name / target / 'recipe.json')
    if not candidate.is_file():
        return {'status': 'not_inspected', 'path': str(candidate),
                'instruction': f'Run ./Cinter inspect {source.parent.parent.name} before promoting the inspected recipe'}
    generated = read_recipe(candidate, application=name, target=target)
    metadata = generated.get('inspection_generation')
    if not isinstance(metadata, dict) or metadata.get('schema') != 'cinterforge.inspect.recipe-generation.v1':
        raise ValueError(f'Invalid inspected recipe provenance: {candidate}; rerun ./Cinter inspect {source.parent.parent.name}')
    if not source.is_file():
        return {'status': 'incomplete', 'path': str(candidate),
                'instruction': f'No authored build recipe exists at {source}; inspect is not an executable build plan'}
    authored = read_recipe(source, application=name, target=target)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != metadata.get('source_recipe_sha256'):
        raise ValueError(f'Stale inspected recipe for {name}/{target}: {source} changed since Inspect; '
                         f'run ./Cinter inspect {source.parent.parent.name}')
    if not authored_instructions_preserved(generated, authored):
        raise ValueError(f'Inspected recipe differs from authored {name}/{target} instructions; '
                         f'run ./Cinter inspect {source.parent.parent.name} and review differences')
    if metadata.get('runtime_state_reset') is True:
        # A candidate cannot inherit an old READY flag, even if a previous
        # Build succeeded. The immutable source hash still binds it to the
        # complete original recipe and its historical run-state evidence.
        if (generated.get('readiness', {}).get('ready') is not False or
                generated.get('readiness', {}).get('configured') is not False or
                generated.get('readiness', {}).get('setup_result') is not None or
                generated.get('build', {}).get('verified') is not False or
                'last_result' in generated.get('build', {}) or
                metadata.get('authored_runtime_evidence') != {
                    'readiness': authored.get('readiness'),
                    'build_verified': authored.get('build', {}).get('verified'),
                    'build_last_result': authored.get('build', {}).get('last_result'),
                }):
            raise ValueError(f'Invalid inspected recipe runtime-state reset: {candidate}; rerun Inspect')
    comparison = candidate.parent / 'RecipeComparison.json'
    if not comparison.is_file():
        raise ValueError(f'Missing inspection comparison: {comparison}')
    record = json.loads(comparison.read_text(encoding='utf-8'))
    if (record.get('generatedSha256') != hashlib.sha256(candidate.read_bytes()).hexdigest()
            or record.get('sourceRecipeSha256') != digest
            or record.get('status') != 'schema_valid_authored_recipe_preserved'
            or record.get('promotedToBuildLifecycle') is not False):
        raise ValueError(f'Invalid or changed inspected recipe comparison: {comparison}; '
                         f'run ./Cinter inspect {source.parent.parent.name}')
    acceptance_path = candidate.parent / 'RecipeAcceptance.json'
    acceptance = json.loads(acceptance_path.read_text(encoding='utf-8')) if acceptance_path.is_file() else {}
    return {'status': 'authored_recipe_matches_inspection', 'path': str(candidate),
            'source_checkout_status': metadata.get('source_checkout_status', 'unknown'),
            'candidate_plan_status': acceptance.get('planStatus', 'not_audited'),
            'historical_evidence_conflicts': acceptance.get('historicalEvidenceConflicts', []),
            'requirements_reviewed': False, 'build_test_verified': False}


def retain_recipe_instructions(generated: dict, existing: dict | None) -> dict:
    """Update discovery/audit without overwriting the manually refined build plan."""
    if existing is not None:
        generated['preparation']['packages'] = list(existing['preparation']['packages'])
        generated['preparation']['package_manager'] = existing['preparation']['package_manager']
        generated['configure']['argv'] = list(existing['configure']['argv'])
        generated['configure']['system'] = existing['configure'].get('system')
        generated['build'] = dict(existing.get('build', generated['build']))
        generated['build']['verified'] = bool(generated['readiness']['ready'])
        generated['environment']['prefix'] = existing.get('environment', {}).get('prefix')
        generated['environment']['sdk'] = existing.get('environment', {}).get('sdk')
        if "source_dependencies" in existing:
            generated["source_dependencies"] = existing["source_dependencies"]
        # A frozen Ubuntu requirement audit is authored application data, not
        # transient upstream-evidence discovery. Retain it across recipe updates.
        if 'cfos_ubuntu_requirements' in existing:
            generated['cfos_ubuntu_requirements'] = existing['cfos_ubuntu_requirements']
    return generated


def record_build_result(name: str, target: str, success: bool, version: str | None = None) -> None:
    """Record completed Build outcome, not just Waf configure success.

    Invoked by the Cinter build lifecycle at start and on completion. A failed
    attempt invalidates any earlier success; no stale READY after failures.
    """
    path = recipe_path(name, target)
    if not path.is_file():
        return  # Non-recipe Creative Suite applications remain supported.
    # This directory also contains setup-dependency manifests for apps such as
    # cinterforge-os. They are not build-readiness recipes and must not be
    # rewritten as such. Only this module's recipe schema tracks build results.
    document = json.loads(path.read_text(encoding='utf-8'))
    if document.get('schema') == 'cinterforge.creative-suite.setup-dependencies.v1':
        return
    if document.get('schema') != RECIPE_SCHEMA:
        raise ValueError(f'Unknown Creative Suite recipe schema: {path}')
    identity = document.get('identity')
    if not isinstance(identity, dict) or not isinstance(identity.get('application'), str):
        raise ValueError(f'Missing recipe identity.application: {path}')
    recipe = read_recipe(path, application=identity['application'], target=target)
    setup = recipe.get('readiness', {}).get('setup_result') or {}
    configured = bool(setup.get('configured', setup.get('ready'))) and not setup.get('errors')
    verified = bool(success and configured)
    recipe.setdefault('build', {})['verified'] = verified
    recipe['build']['last_result'] = {'success': bool(success), 'version': version}
    readiness = recipe.setdefault('readiness', {})
    readiness['configured'] = configured
    readiness['ready'] = verified
    issues = [i for i in readiness.get('issues', []) if i != 'Build has not completed successfully since the latest build attempt']
    if verified:
        issues = [i for i in issues if i != 'Build environment has not been confirmed READY by setup']
    else:
        issues.append('Build has not completed successfully since the latest build attempt')
    readiness['issues'] = sorted(set(issues))
    write_recipe(recipe, path)


if __name__ == '__main__':
    import argparse
    cli = argparse.ArgumentParser()
    cli.add_argument('operation', choices=('build-start', 'build-success', 'build-failure'))
    cli.add_argument('application')
    cli.add_argument('platform', choices=tuple(ARCH))
    cli.add_argument('--version')
    args = cli.parse_args()
    record_build_result(args.application, args.platform, args.operation == 'build-success', args.version)
