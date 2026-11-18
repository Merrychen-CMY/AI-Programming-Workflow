"""L1 approval within a pinned delegation. Local records are not authenticated identities."""
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from .project_constraints import prompt as constraints_prompt
from .communication import l1_prompt as communication_prompt


def validate_delegation(value, enforce_time=True):
    if value.get('schema_version') != 1 or value.get('authority') != 'delegated_l1':
        raise ValueError('Explicit delegated_l1 policy required; never impersonate a human approval')
    for key in ('source_message', 'raw_input_sha256', 'expires_at'):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError('Missing delegation ' + key)
    if len(value['raw_input_sha256']) != 64:
        raise ValueError('Raw input must be bound by SHA-256')
    expiry = datetime.fromisoformat(value['expires_at'])
    if expiry.tzinfo is None or (enforce_time and expiry <= datetime.now(timezone.utc)):
        raise ValueError('Delegation expired or timezone missing')
    if not isinstance(value.get('hard_constraints'), list) or not value['hard_constraints'] or any(
            not isinstance(item, str) or not item.strip() for item in value['hard_constraints']):
        raise ValueError('Record the user hard constraints before delegation')
    paths = value.get('allowed_paths')
    if not isinstance(paths, list) or not paths:
        raise ValueError('Delegation needs bounded implementation paths')
    for name in paths:
        if not isinstance(name, str) or not name or '\\' in name or ':' in name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
            raise ValueError('Delegation paths must stay inside the project')
    for key in ('implementation_attempts', 'same_root_cause_attempts', 'infrastructure_retries',
                'timeout_seconds', 'l1_reviews', 'revisions', 'api_calls', 'api_max_output_tokens',
                'wall_time_seconds'):
        number = value.get('limits', {}).get(key)
        minimum = 0 if key in ('revisions', 'infrastructure_retries') else 1
        if type(number) is not int or number < minimum:
            raise ValueError('Invalid delegation limit: ' + key)
    if value.get('external_delivery') is not False:
        raise ValueError('This bridge has no external delivery executor')


def check_plan_bounds(plan, delegation, enforce_time=True):
    validate_delegation(delegation, enforce_time=enforce_time)
    for path in plan['allowed_paths']:
        if not any(parent == '.' or path == parent.rstrip('/') or path.startswith(parent.rstrip('/') + '/')
                   for parent in delegation['allowed_paths']):
            raise ValueError('Plan expands delegated paths: ' + path)
    for key, number in plan['budgets'].items():
        if key in delegation['limits'] and number > delegation['limits'][key]:
            raise ValueError('Plan expands delegated budget: ' + key)


def check_binding(state, enforce_time=True):
    from .controller import digest, file_hash, read_json
    binding = state['delegation']
    value = read_json(binding['path'])
    validate_delegation(value, enforce_time=enforce_time)
    if digest(value) != binding['sha256'] or file_hash(binding['raw_input']) != value['raw_input_sha256']:
        raise ValueError('Original input or delegation changed; cannot silently expand authority')
    started = state.get('delegated_started_at', datetime.fromisoformat(state['created_at']).timestamp())
    if enforce_time and datetime.now(timezone.utc).timestamp() - started >= value['limits']['wall_time_seconds']:
        raise ValueError('Delegated execution wall-time budget exhausted')
    if state.get('approved_plan'):
        check_plan_bounds(state['approved_plan'], value, enforce_time=enforce_time)
    return value


def bounded_timeout(state, requested):
    value = check_binding(state)
    current = datetime.now(timezone.utc).timestamp()
    start = state.get('delegated_started_at', datetime.fromisoformat(state['created_at']).timestamp())
    remaining = min(start + value['limits']['wall_time_seconds'],
                    datetime.fromisoformat(value['expires_at']).timestamp()) - current
    if remaining < 1:
        raise ValueError('No delegated execution time remains')
    return min(requested, int(remaining))


def attach_delegation(controller, path, raw_input):
    from .controller import digest, file_hash, read_json, run_lock
    with run_lock(controller.run_dir):
        controller.state = read_json(controller.state_path)
        state = controller.state
        if state['state'] != 'REVIEW_PENDING' or state['approvals'] or state.get('delegation'):
            raise ValueError('Delegation must be attached once, before any approval')
        path, raw_input = Path(path).resolve(), Path(raw_input).resolve()
        for item in (path, raw_input):
            if item == Path(state['project']) or Path(state['project']) in item.parents:
                raise ValueError('Delegation inputs must be outside worker project')
        value = read_json(path)
        validate_delegation(value)
        if file_hash(raw_input) != value['raw_input_sha256']:
            raise ValueError('Original user input hash mismatch')
        check_plan_bounds(read_json(state['plan']), value)
        state['delegation'] = {'path': str(path), 'raw_input': str(raw_input), 'sha256': digest(value)}
        state['l1_reviews'] = []
        state['revision_count'] = 0
        controller.save()
        return state


def approve_l1(controller, kind, l1_session_id, reviewer):
    """The L1 tool invocation requests approval; a separate read-only reviewer can veto it."""
    from .controller import atomic_json, file_hash, now, read_json, run_lock
    with run_lock(controller.run_dir):
        controller.state = read_json(controller.state_path)
        state = controller.state
        value = check_binding(state)
        if kind not in ('requirements', 'plan') or not l1_session_id.strip():
            raise ValueError('L1 may approve only requirements and plan, with its session identity')
        wanted = 'REVIEW_PENDING' if kind == 'requirements' else 'PLAN_PENDING'
        if state['state'] != wanted or state['active_run']:
            raise ValueError('L1 approval requires the matching pending artifact and no unresolved process')
        if len(state['l1_reviews']) >= value['limits']['l1_reviews']:
            controller.report('FAILED', 'L1 review budget exhausted')
            return controller.state
        from .controller import validate_plan
        plan = read_json(state['plan'])
        validate_plan(plan)
        check_plan_bounds(plan, value)
        baseline = controller.baseline()
        if kind == 'plan' and state['approvals'].get('requirements', {}).get('sha256') != baseline['requirements']:
            raise ValueError('Requirements changed before plan approval')
        number = len(state['l1_reviews']) + 1
        workspace = controller.run_dir / 'l1-review-inputs' / str(number)
        workspace.mkdir(parents=True)
        for name, source in [('requirements.md', state['requirements']), ('plan.json', state['plan']),
                             ('raw-input.md', state['delegation']['raw_input']), ('delegation.json', state['delegation']['path'])]:
            (workspace / name).write_bytes(Path(source).read_bytes())
        output = controller.run_dir / 'l1-reviews' / str(number)
        record = {'kind': kind, 'baseline': baseline, 'l1_session_id': l1_session_id, 'at': now(), 'status': 'UNKNOWN'}
        state['l1_reviews'].append(record)  # Reserve the attempt BEFORE any external dispatch.
        state['active_run'] = {'role': 'l1_independent_review', 'output': str(output), 'at': now()}
        controller.save()
        prompt = ('Independently review the ' + kind + ' baseline against raw-input.md and delegation.json. '
                  'The L1 author requests approval. You are a separate reviewer, not its author. '
                  'Inspect requirements.md and plan.json. Reject missing critical facts, changed user hard constraints, '
                  'weakened AC, circular expected results, unsafe/external commands, scope/budget expansion or '
                  'missing independent verification. Huawei C and Doxygen remain mandatory for C. '
                  'PASS requires specific source references and a complete reasoning-based review. '
                  'Do not execute project commands, edit files, approve anything or invent user decisions.'
                  + '\n' + communication_prompt() + constraints_prompt())
        try:
            result = reviewer.run('review', prompt, workspace, output, bounded_timeout(state, value['limits']['timeout_seconds']))
        except Exception as exc:
            result = {'status': 'UNKNOWN', 'summary': type(exc).__name__, 'session_id': ''}
        record['result'] = result
        record['status'] = result.get('status', 'ERROR')
        record['evidence_files'] = [{'path': p.relative_to(controller.run_dir).as_posix(), 'sha256': file_hash(p)}
                                    for p in sorted(output.rglob('*')) if p.is_file() and not p.is_symlink()]
        seen = {r.get('result', {}).get('session_id') for r in state['l1_reviews'][:-1]}
        seen |= {r['result'].get('session_id') for r in state['sessions']}
        identity = result.get('session_id')
        if result.get('status') == 'UNKNOWN':
            controller.report('UNKNOWN', 'Independent L1 review outcome unknown; no automatic replay')
            return state
        state['active_run'] = None
        controller.save()
        if (result.get('status') != 'PASS' or not identity or identity == l1_session_id or identity in seen
                or not result.get('evidence') or result.get('findings') or not record['evidence_files']):
            controller.transition('DECISION_PENDING', 'Independent L1 review rejected or lacks evidence: ' + result.get('summary', ''))
            return state
        if controller.baseline() != baseline:
            raise ValueError('Inputs changed during independent review')
        check_binding(state)
        state['approvals'][kind] = {'authority': 'delegated_l1', 'l1_session_id': l1_session_id,
            'reviewer_session_id': identity, 'review_number': number, 'sha256': baseline[kind],
            'baseline': baseline, 'delegation_sha256': state['delegation']['sha256'], 'at': now(), 'run_id': state['run_id']}
        if kind == 'plan':
            state['approved_plan'], state['approved_baseline'] = plan, baseline
        controller.transition('PLAN_PENDING' if kind == 'requirements' else 'EXECUTING',
                              'L1 delegated approval after independent review; no human receipt was created')
        return state


def submit_l1_report(controller, summary, l1_session_id):
    from .controller import atomic_json, file_hash, now, read_json, run_lock
    with run_lock(controller.run_dir):
        controller.state = read_json(controller.state_path)
        state = controller.state
        if not state.get('delegation') or state['state'] != 'REPORT_PENDING' or not summary.strip() or not l1_session_id.strip():
            raise ValueError('L1 must review a pending DOT report before submitting it to the user')
        path = controller.run_dir / 'final-report.json'
        if file_hash(path) != state['report']['sha256']:
            raise ValueError('DOT report changed before L1 review')
        report = read_json(path)
        # L1 adds its assessment but cannot upgrade the controller outcome or remove evidence.
        report['l1_submission'] = {'authority': 'delegated_l1', 'session_id': l1_session_id,
            'summary': summary, 'outcome': state['report']['outcome'], 'dot_report_sha256': file_hash(path), 'at': now()}
        atomic_json(path, report)
        archive = controller.run_dir / 'reports' / ('l1-' + now().replace(':', '-') + '.json')
        atomic_json(archive, report)
        state['report'].update(sha256=file_hash(path), l1_submitted=True, archive=archive.relative_to(controller.run_dir).as_posix())
        controller.save()
        return state
