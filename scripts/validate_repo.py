#!/usr/bin/env python3
"""Validate this reference repository; never authorizes execution or delivery.

Python 3.10+, standard library only. MANIFEST is a drift detector, not a signature.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {'.git', '__pycache__', '.venv', 'local-project', 'private-evidence'}
ROUTES = {
    'implementation': 'L3', 'product_requirement': 'L1',
    'task_plan_or_design': 'L2', 'verification_plan_or_test': 'L1-V',
    'test_infrastructure': 'L4', 'evidence_collection': 'L4',
    'delivery_target_or_authorization': 'L5',
    'security': 'authorized_security_owner', 'unknown': None, 'mixed': 'L2',
}


def repository_files(root):
    return sorted(p for p in root.rglob('*') if p.is_file()
                  and not any(part in IGNORED for part in p.relative_to(root).parts)
                  and p.name not in {'MANIFEST.json', '.env'}
                  and p.suffix != '.pyc')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_manifest(root):
    return {
        'schema_version': 1,
        'workflow_version': (root / 'VERSION').read_text(encoding='utf-8').strip(),
        'purpose': 'Content drift detection only; not a signature or approval.',
        'files': {p.relative_to(root).as_posix(): digest(p) for p in repository_files(root)},
    }


def validate_manifest(root, manifest):
    errors = []
    actual = make_manifest(root)
    if manifest.get('workflow_version') != actual['workflow_version']:
        errors.append('MANIFEST version mismatch')
    expected = manifest.get('files', {})
    for path in sorted(set(expected) | set(actual['files'])):
        if expected.get(path) != actual['files'].get(path):
            errors.append(f'MANIFEST missing, added or changed file: {path}')
    return errors


def validate_policy(policy):
    errors = []
    expected = {
        'execution_authority': False,
        'reflow.sources': ['L4', 'L5'],
        'reflow.intake_owner': 'L2', 'reflow.triage_owner': 'L2',
        'reflow.require_issue_id': True,
        'reflow.require_evidence_before_classification': True,
        'reflow.allow_direct_l4_l5_to_l3': False,
        'candidate_rules.immutable': True,
        'candidate_rules.reuse_pass_across_candidates': False,
        'candidate_rules.plan_or_verifier_semantic_change': 'new_approval_and_rerun_all_required_checks',
        'approval_rules.L5_can_approve_delivery': False,
        'approval_rules.merge_implies_release': False,
        'approval_rules.example_is_authorization': False,
        'closure.G3_requires_issue_CLOSED': False,
        'closure.preserve_all_attempts': True,
        'closure.implementation_or_verification': 'independent_L4_reverification_and_trusted_verdict_then_L2_close',
        'closure.delivery_only': 'approval_service_and_delivery_guard_validation_L5_review_then_L2_close',
        'budgets.reset_on_restart': False,
    }
    for path, wanted in expected.items():
        value = policy
        for key in path.split('.'):
            value = value.get(key) if isinstance(value, dict) else None
        if value != wanted or type(value) is not type(wanted):
            errors.append(f'Invariant {path}: expected {wanted!r}, got {value!r}')
    if policy.get('roles') != ['L1', 'L1-V', 'L2', 'L3', 'L4', 'L5']:
        errors.append('Six distinct roles including both L1 roles are required')
    routes = policy.get('reflow', {}).get('routes', {})
    for kind, owner in ROUTES.items():
        if kind not in routes or routes[kind].get('owner') != owner:
            errors.append(f'Reflow route {kind} must be owned by {owner!r} after L2 triage')
    required_inputs = {'product_code', 'dependencies', 'toolchain', 'build_configuration', 'artifacts', 'integration_base'}
    if not required_inputs.issubset(policy.get('candidate_rules', {}).get('new_id_on', [])):
        errors.append('Changed candidate inputs must create a new candidate ID')
    for key, value in policy.get('budgets', {}).items():
        if key != 'reset_on_restart' and (type(value) is not int or value < 1):
            errors.append(f'Budget {key} must be a positive integer')
    return errors


def check_local_link(root, source, target):
    target = target.strip().strip('<>')
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return []
    destination = (source.parent / unquote(parsed.path)).resolve()
    if not destination.is_relative_to(root.resolve()):
        return [f'Link escapes repository: {source.relative_to(root)} -> {target}']
    if not destination.exists():
        return [f'Missing link: {source.relative_to(root)} -> {target}']
    return []


def validate_repository(root):
    errors, documents = [], {}
    for file in repository_files(root):
        if file.suffix == '.json':
            try:
                documents[file.relative_to(root).as_posix()] = json.loads(file.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError) as exc:
                errors.append(f'Invalid JSON: {file.relative_to(root)}: {exc}')
        if file.suffix == '.md':
            content = file.read_text(encoding='utf-8')
            content = re.sub(r'^```[^\n]*\n.*?^```\s*$', '', content, flags=re.M | re.S)
            for target in re.findall(r'!?\[[^\]\n]*\]\(([^)\n]+)\)', content):
                errors.extend(check_local_link(root, file, target))
    required = ['AGENTS.md', 'START_HERE.md', 'README.md', 'INDEX.json', 'VERSION',
                'docs/core-rules.md', '.github/workflows/validate.yml',
                'policy/workflow.json', 'policy/roles.json', 'policy/rejection-tests.json',
                'policy/l4-failures.json', 'policy/l5-delivery.json']
    errors.extend(f'Missing required file: {name}' for name in required if not (root / name).is_file())
    if errors:
        return errors
    policy, index = documents['policy/workflow.json'], documents['INDEX.json']
    errors.extend(validate_policy(policy))
    version = (root / 'VERSION').read_text(encoding='utf-8').strip()
    if version != policy.get('policy_version') or version != index.get('workflow_version'):
        errors.append('VERSION, INDEX and policy versions differ')
    paths = [index['entrypoint'], *index['always_read'], *index['original_examples']]
    for group in ('role_reading', 'event_reading'):
        for entries in index[group].values():
            paths.extend(entries)
    paths.extend(row['path'] for row in index['full_reference'])
    if [row['chapter'] for row in index['full_reference']] != list(range(1, 22)):
        errors.append('Full reference must preserve all 21 chapters in order')
    roles = documents['policy/roles.json']['roles']
    if sorted(row['id'] for row in roles) != sorted(policy['roles']):
        errors.append('Role documents and policy role IDs differ')
    paths.extend(row['path'] for row in roles)
    for path in paths:
        errors.extend(check_local_link(root, root / 'INDEX.json', path))
    for name, layer, count in [('l4-failures', 'L4', 16), ('l5-delivery', 'L5', 12)]:
        matrix = documents[f'policy/{name}.json']
        if matrix.get('source_layer') != layer or any(matrix.get(k) != 'L2' for k in ('intake_owner', 'triage_owner')):
            errors.append(f'{name}: all findings must enter L2 triage')
        rows = matrix['scenarios']
        if len(rows) != count or len({r['id'] for r in rows}) != count:
            errors.append(f'{name}: expected {count} unique scenarios')
        for row in rows:
            if not all(row.get(key) for key in ('observation', 'l2_analysis_and_blocking', 'handler_after_l2_triage', 'resume_or_close_conditions')):
                errors.append(f'{name}: incomplete scenario {row.get("id")}')
    cases = documents['policy/rejection-tests.json']['cases']
    if len(cases) != 48 or len({c['id'] for c in cases}) != 48:
        errors.append('Expected 48 unique acceptance/rejection scenarios')
    if any(c.get('execution_status') != 'NOT_RUN' or c.get('evidence_ref') is not None for c in cases):
        errors.append('Reference scenarios must not claim real execution; store actual results in the product project')
    for capability, implemented in index['capabilities'].items():
        if capability != 'static_repo_validation' and implemented is not False:
            errors.append(f'Reference repo cannot claim runtime capability: {capability}')
    for name in ('project-context', 'task-draft'):
        template = documents[f'templates/{name}.json']
        if template.get('status') != 'DRAFT':
            errors.append(f'{name}: template must remain DRAFT')
    try:
        manifest = json.loads((root / 'MANIFEST.json').read_text(encoding='utf-8'))
        errors.extend(validate_manifest(root, manifest))
    except (OSError, ValueError) as exc:
        errors.append(f'MANIFEST unavailable: {exc}; maintainers may use --refresh-manifest after review')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-manifest', action='store_true', help='Record reviewed file hashes; does not approve content')
    args = parser.parse_args()
    if args.refresh_manifest:
        (ROOT / 'MANIFEST.json').write_text(json.dumps(make_manifest(ROOT), ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    errors = validate_repository(ROOT)
    if errors:
        for error in errors:
            print(f'ERROR: {error}')
        return 1
    print(f'STATIC_SPEC_CHECKS_PASS: {len(repository_files(ROOT))} files; 21 chapters; 16 L4 + 12 L5 scenarios.')
    print('48 product/HIL acceptance scenarios remain NOT_RUN. No execution, approval or delivery authority is granted.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
