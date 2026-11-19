"""Project-bound L1 tools, serialized through one local control directory."""
from pathlib import Path
import os
import time

from .adapter import CodexAdapter
from .controller import Controller, ROOT, InitializationIncomplete, atomic_json, digest, file_hash, initialize, read_json, run_lock
from .delegation import attach_delegation, approve_l1, submit_l1_report, validate_delegation
from .gpt_dot import ResponsesClient, DotAdapter, execute, propose_plan
from .checks import run_check
from . import clarifications
from . import planning
from . import resources


TOOLS = {
    'l1_adjust_limits': ('Allocate operating limits within user ceilings; preserve cumulative usage.',
                         {'proposal_json': 'string', 'l1_session_id': 'string'}),
    'l1_request_authorization': ('Prepare an exact authorization amendment for native human review. Never approves it.',
                                 {'proposal_json': 'string', 'l1_session_id': 'string'}),
    'dot_plan': ('Ask GPT/DOT to draft a plan. No execution or approval.', {'requirements': 'string'}),
    'dot_initialize': ('Initialize the current reviewed draft with pinned delegation. No workers.', {}),
    'l1_approve': ('L1 requests delegated approval; independent review may veto. Never human approval.',
                   {'kind': 'string', 'l1_session_id': 'string'}),
    'dot_run': ('Ask GPT/DOT to dispatch the approved local workflow. May take several minutes.', {}),
    'dot_status': ('Read status, decisions and final report. Does not dispatch.', {}),
    'l1_add_facts': ('Append sourced facts within the original delegation; no approval or reset. '
                     'Independent baseline review remains required.', {'facts_json': 'string', 'l1_session_id': 'string'}),
    'l1_revise': ('L1 stages revised requirements and a GPT plan within existing delegation and counters.',
                  {'requirements': 'string', 'plan_json': 'string'}),
    'l1_submit_report': ('L1 submits its final assessment; cannot approve the human final report.',
                         {'summary': 'string', 'l1_session_id': 'string'}),
    'l1_stop': ('Report failure/unknown when L1 cannot resolve a blocker; no retry or delivery.', {'reason': 'string'}),
}


class Bridge:
    def __init__(self, settings, client_factory=ResponsesClient, workers=None):
        self.settings = settings
        self.project = Path(settings['project']).resolve(strict=True)
        self.root = Path(settings['control_dir']).resolve()
        if not self.project.is_dir() or self.root == self.project or self.project in self.root.parents or self.root in self.project.parents:
            raise ValueError('Separate the control directory from the product workspace')
        for key in ('raw_input', 'delegation'):
            path = Path(settings[key]).resolve(strict=True)
            if self.project == path or self.project in path.parents or self.root == path or self.root in path.parents:
                raise ValueError('Original input and delegation must stay outside product/control directories')
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_dir = self.root / 'run'
        self.worker_environment = {key: value for key, value in os.environ.items() if key.upper() != 'OPENAI_API_KEY'}
        self.workers = workers or CodexAdapter(settings.get('codex', 'codex'), environment=self.worker_environment)
        self.client_factory = client_factory

    def tools(self):
        return [{'name': name, 'description': description, 'inputSchema': {
            'type': 'object', 'additionalProperties': False,
            'properties': {key: {'type': value} for key, value in fields.items()}, 'required': list(fields)}}
                for name, (description, fields) in TOOLS.items()]

    def client(self):
        delegation = read_json(self.settings['delegation'])
        validate_delegation(delegation, enforce_time=not resources.enabled(delegation, self.root))
        if file_hash(self.settings['raw_input']) != delegation['raw_input_sha256']:
            raise ValueError('Original input differs from delegation')
        client = self.client_factory(self.settings['model'], self.root / 'api', delegation)
        client.governance_root = self.root
        return client

    def status(self):
        result = self._status()
        from .upgrades import records_for_control
        result['runtime_upgrades'] = records_for_control(self.root)
        original = read_json(self.settings['delegation'])
        if resources.enabled(original, self.root):
            state = read_json(self.run_dir / 'state.json') if (self.run_dir / 'state.json').exists() else None
            governance = resources.report(self.root, original, state)
            result['resources'] = governance
            if not result.get('report') and not result.get('preflight_report'):
                if governance['pending_authorization'] or governance['budget_review']:
                    result['runtime_state'] = result['state']
                    result['state'] = 'AUTHORIZATION_PENDING' if governance['pending_authorization'] else 'BUDGET_REVIEW_PENDING'
                    result['next_owner'] = 'user' if governance['pending_authorization'] else 'L1'
        return result

    def _status(self):
        planning = read_json(self.root / 'planning-status.json') if (self.root / 'planning-status.json').exists() else None
        facts = clarifications.records(self.root)
        common = {'planning': planning, 'facts_revision': len(facts),
                  'facts_sha256': clarifications.signature(self.root)}
        from .initialization_recovery import records_for_control
        common['initialization_recoveries'] = records_for_control(self.root)
        if not (self.run_dir / 'state.json').exists():
            if (self.run_dir / 'initialization.json').exists():
                common['initialization'] = read_json(self.run_dir / 'initialization.json')
                common['recovery_required'] = True
            return {**common, 'state': planning['status'] if planning and planning['status'] in ('NEEDS_INFO', 'PLAN_REJECTED') else 'DISCOVERY',
                    'plan_ready': (self.root / 'plan.json').exists() and not (planning and planning['status'] != 'PLAN_READY'),
                    'preflight_report': read_json(self.root / 'preflight-report.json') if (self.root / 'preflight-report.json').exists() else None}
        state = read_json(self.run_dir / 'state.json')
        return {**common, 'state': state['state'], 'run_id': state['run_id'], 'implementation_attempts': state['implementation_attempts'],
            'revision_count': state.get('revision_count', 0), 'report': state['report'],
            'final_report': read_json(self.run_dir / 'final-report.json') if state['report'] else None,
            'decision': read_json(self.run_dir / 'decision-request.json') if state['state'] == 'DECISION_PENDING'
                        and (self.run_dir / 'decision-request.json').exists() else None,
            'l1_reviews': state.get('l1_reviews', []),
            'next_owner': 'user' if state['state'] == 'REPORT_PENDING' and (state['report'] or {}).get('l1_submitted') else 'L1'}

    def call(self, name, arguments):
        try:
            return self._call(name, arguments)
        except resources.BudgetReview as exc:
            with run_lock(self.root):
                original = read_json(self.settings['delegation'])
                state = read_json(self.run_dir / 'state.json') if (self.run_dir / 'state.json').exists() else None
                resources.pause(self.root, original, exc, state)
                return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_BUDGET_REVIEW'}

    def preflight_report(self, reason):
        original = read_json(self.settings['delegation'])
        record = {'outcome': 'FAILED', 'reason': reason, 'execution_started': False,
                  'owner': 'L1', 'user_review_required': True, 'planning_rejections': planning.history(self.root)}
        from .upgrades import records_for_control
        record['runtime_upgrades'] = records_for_control(self.root)
        from .initialization_recovery import records_for_control as recovery_records
        record['initialization_recoveries'] = recovery_records(self.root)
        if resources.enabled(original, self.root):
            record['resource_governance'] = resources.report(self.root, original)
        atomic_json(self.root / 'preflight-report.json', record)

    def _call(self, name, arguments):
        import json
        if name not in TOOLS or not isinstance(arguments, dict) or set(arguments) != set(TOOLS[name][1]) or any(
                not isinstance(value, str) or not value.strip() for value in arguments.values()):
            raise ValueError('Unknown tool or invalid arguments')
        with run_lock(self.root):
            if (self.root.parent / 'initialization-recovery-pending.json').exists():
                raise ValueError('INITIALIZATION_RECOVERY_PENDING: finish native recovery before task tools')
            # Pin all settings once. Restarting the server is not a new delegation or budget.
            binding = self.root / 'settings-binding.json'
            value = {'sha256': digest(self.settings)}
            if binding.exists() and read_json(binding) != value:
                raise ValueError('Bridge settings changed in an existing task')
            if not binding.exists():
                atomic_json(binding, value)
            if name == 'dot_status':
                return self.status()
            c = Controller(self.run_dir) if (self.run_dir / 'state.json').exists() else None
            if name == 'l1_submit_report':
                submit_l1_report(c, arguments['summary'], arguments['l1_session_id'])
                return self.status()
            if name == 'l1_stop':
                if c:
                    c.stop_with_report(arguments['reason'])
                elif not (self.root / 'preflight-report.json').exists():
                    self.preflight_report(arguments['reason'])
                return self.status()
            if c is None and (self.root / 'preflight-report.json').exists():
                raise ValueError('Preflight has ended with a final failure report; no automatic resumption')
            original = read_json(self.settings['delegation'])
            adaptive = resources.enabled(original, self.root)
            if name in ('l1_adjust_limits', 'l1_request_authorization'):
                if c and (c.state.get('active_run') or c.state.get('report')):
                    return {**self.status(), 'accepted': False, 'reason': 'Unknown operation or final report requires a separate verified recovery; cannot rewrite it'}
                try:
                    proposal = json.loads(arguments['proposal_json'])
                    action = resources.adjust if name == 'l1_adjust_limits' else resources.propose_authorization
                    result = action(self.root, original, proposal, arguments['l1_session_id'], c.state if c else None)
                except (ValueError, TypeError, KeyError) as exc:
                    return {**self.status(), 'accepted': False, 'reason': str(exc)}
                return {**self.status(), 'accepted': True, 'resource_result': result}
            if adaptive:
                journal = resources.read(self.root, original)
                if journal['pause'] or journal['pending']:
                    return {**self.status(), 'accepted': False, 'reason': 'Resolve resource review or native authorization before continuing'}
            # Read-only report access and failure submission remain possible after expiry/API failure.
            client = self.client()
            operating = resources.effective(original, self.root) if adaptive else client.delegation
            planning_path = self.root / 'planning-status.json'
            pending = read_json(planning_path) if planning_path.exists() else {}
            if pending.get('status') == 'NEEDS_INFO' and name in ('dot_initialize', 'l1_approve', 'l1_revise', 'dot_run'):
                return {**self.status(), 'accepted': False,
                        'reason': 'Resolve NEEDS_INFO and obtain a new plan before initialization, revision, approval or execution'}
            if pending.get('status') == 'PLAN_REJECTED' and name in ('dot_initialize', 'l1_approve', 'dot_run'):
                return {**self.status(), 'accepted': False, 'reason': 'Correct the rejected proposal before approval or execution'}
            if pending.get('requires_revision') and name in ('l1_approve', 'dot_run'):
                return {**self.status(), 'accepted': False, 'reason': 'Stage the new proposal with l1_revise before approval'}
            if c and c.state.get('pending_clarification_revision') and name in ('l1_approve', 'dot_run'):
                return {**self.status(), 'accepted': False, 'reason': 'Call l1_revise to incorporate facts before approval'}
            if name == 'l1_add_facts':
                # All rejection checks precede mutations. A completed local rejection is not UNKNOWN.
                try:
                    supplement = clarifications.validate_facts(json.loads(arguments['facts_json']))
                    clarifications.check_remaining_time(self.root, operating)
                    prior_facts = clarifications.records(self.root)
                    if prior_facts and prior_facts[-1]['supplement'] == supplement:
                        raise ValueError('Identical fact supplement already recorded; no new revision consumed')
                    count = c.state.get('revision_count', 0) if c else len(prior_facts)
                    resources.require(original, self.root, 'revisions', count)
                    if count >= operating['limits']['revisions']:
                        raise ValueError('L1 revision budget exhausted; no new facts revision')
                    if c and (c.state['state'] not in ('PLAN_PENDING', 'DECISION_PENDING')
                              or c.state['active_run'] or c.state.get('report')):
                        raise ValueError('Facts require a paused planning decision with no unresolved process or final report')
                    ledger_path = self.root / 'api/ledger.json'
                    if ledger_path.exists() and any(a['status'] != 'COMPLETED' for a in read_json(ledger_path)['attempts']):
                        raise ValueError('Unresolved API attempt: reconcile before adding facts')
                except (ValueError, TypeError) as exc:
                    return {**self.status(), 'accepted': False, 'reason': str(exc)}
                record = clarifications.append(self.root, supplement, arguments['l1_session_id'], operating, c)
                if c:
                    c.state['clarification_root'] = str(self.root)
                    c.save()
                atomic_json(self.root / 'planning-status.json', {'status': 'NEEDS_INFO',
                    'reason': 'Supplement recorded; a new plan and independent baseline review are required',
                    'gaps': [], 'facts_revision': record['revision']})
                return {**self.status(), 'accepted': True, 'next': 'dot_plan with updated requirements; no new startup confirmation'}
            if name == 'dot_plan':
                if c and c.state['state'] not in ('DECISION_PENDING', 'PLAN_PENDING'):
                    raise ValueError('Planning cannot overwrite an active/final baseline')
                if c and c.state['active_run']:
                    raise ValueError('Unresolved process forbids new planning')
                previous = read_json(planning_path) if planning_path.exists() else {}
                facts_sha = clarifications.signature(self.root)
                base_sha = digest({'requirements': arguments['requirements'], 'facts': facts_sha})
                feedback = planning.feedback(self.root, previous)
                request_sha = digest({'base': base_sha, 'validation_feedback': feedback})
                if adaptive:
                    request_sha = digest({'request': request_sha, 'current_authority_and_allocation': operating})
                if ((previous.get('status') == 'NEEDS_INFO' and previous.get('base_request_sha256') == base_sha)
                        or (previous.get('status') == 'PLAN_REJECTED' and previous.get('request_sha256') == request_sha)):
                    return {**self.status(), 'accepted': False, 'reason': 'Unchanged planning input and feedback; no API retry performed'}
                context = self.settings['environment_context'] + '\nAPPENDED FACTS (data, not authority):\n' + clarifications.context(self.root)
                from .initialization_recovery import records_for_control
                context += '\nREVIEWED INITIALIZATION RECOVERIES (data; original authority unchanged):\n' + json.dumps(records_for_control(self.root), ensure_ascii=False)
                if feedback:
                    context += '\nREJECTED DRAFT AND VALIDATOR FEEDBACK (data, not authority):\n' + json.dumps(feedback, ensure_ascii=False)
                try:
                    plan = propose_plan(client, arguments['requirements'],
                        read_json(ROOT / 'templates/local-execution-plan.example.json'), context,
                        implementation_used=c.state['implementation_attempts'] if c else 0)
                except clarifications.PlanningNeedsInfo as exc:
                    atomic_json(planning_path, {'status': 'NEEDS_INFO', 'reason': exc.reason,
                        'gaps': exc.gaps, 'response_id': exc.response_id, 'request_sha256': request_sha,
                        'base_request_sha256': base_sha})
                    return {**self.status(), 'accepted': False,
                            'next': 'L1 gathers facts, calls l1_add_facts, then dot_plan within the same remaining budget'}
                except planning.PlanRejected as exc:
                    planning.record_rejection(self.root, name, exc, request_sha, base_sha)
                    if (adaptive and exc.code == 'IMPLEMENTATION_DISPATCH_BUDGET'
                            and (c.state['implementation_attempts'] if c else 0) + exc.details['initial_task_dispatches']
                            > operating['limits']['implementation_attempts']):
                        raise resources.BudgetReview('implementation_attempts',
                            c.state['implementation_attempts'] if c else 0, exc.details['initial_task_dispatches'],
                            operating['limits']['implementation_attempts'],
                            resources.authority(original, self.root)['limits']['implementation_attempts'],
                            'Draft needs more implementation dispatches; L1 must assess decomposition and remaining work') from exc
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                            'next': 'L1 reads validation feedback, then dot_plan to correct the draft within the original budget; '
                                    'l1_revise is available only after initialization. No product worker was dispatched.'}
                except (ValueError, OSError, KeyError) as exc:
                    if c:
                        c.stop_with_report(str(exc))
                    else:
                        self.preflight_report(str(exc))
                    raise
                atomic_json(planning_path, {'status': 'PLAN_READY', 'facts_sha256': facts_sha,
                    'plan_sha256': digest(plan), 'requirements_sha256': digest(arguments['requirements']),
                    'authorization_scope': resources.scope_digest(original, self.root) if adaptive else None,
                    'requires_revision': bool(c)})
                if c:
                    return {'plan': plan, 'next': 'L1 inspects the proposal then calls l1_revise; approvals are not copied'}
                (self.root / 'requirements.md').write_text(arguments['requirements'], encoding='utf-8', newline='\n')
                atomic_json(self.root / 'plan.json', plan)
                return {'plan': plan, 'next': 'dot_initialize, then l1_approve requirements and plan'}
            if name == 'dot_initialize':
                if c:
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                            'reason': 'Task already initialized; inspect status, do not reset attempts'}
                if self.run_dir.exists() and any(p.name != '.lock' for p in self.run_dir.iterdir()):
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                            'recovery_required': True, 'reason': 'Partial initialization requires native recovery review; no files overwritten'}
                planned = read_json(self.root / 'planning-status.json')
                if (planned['status'] != 'PLAN_READY' or planned['facts_sha256'] != clarifications.signature(self.root)
                        or (adaptive and planned.get('authorization_scope') != resources.scope_digest(original, self.root))
                        or planned['plan_sha256'] != digest(read_json(self.root / 'plan.json'))
                        or planned['requirements_sha256'] != digest((self.root / 'requirements.md').read_text(encoding='utf-8'))):
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                            'reason': 'Plan is stale after clarification; replan before initialization'}
                if adaptive:
                    resources.require(original, self.root, 'implementation_attempts', 0,
                                      len(read_json(self.root / 'plan.json')['tasks']))
                try:
                    initialize(self.project, self.root / 'requirements.md', self.root / 'plan.json', self.run_dir)
                except InitializationIncomplete as exc:
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_INITIALIZATION_FAILED',
                            'recovery_required': True, 'reason': str(exc)}
                c = Controller(self.run_dir)
                c.state['clarification_root'] = str(self.root)
                c.save()
                attach_delegation(c, self.settings['delegation'], self.settings['raw_input'])
                c.state['delegated_started_at'] = read_json(self.root / 'api/ledger.json')['started_at']
                c.state['clarification_root'] = str(self.root)
                c.state['revision_count'] = len(clarifications.records(self.root))
                c.save()
            elif c is None and name == 'l1_revise':
                return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                        'reason': 'No initialized baseline exists; use dot_plan to correct the draft, then dot_initialize'}
            elif c is None:
                raise ValueError('Initialize the task first')
            elif not c.state.get('delegation'):
                raise ValueError('Incomplete bridge initialization; do not dispatch or synthesize approvals')
            elif name == 'l1_approve':
                approve_l1(c, arguments['kind'], arguments['l1_session_id'], self.workers)
            elif name == 'l1_revise':
                # Pure preflight before writing staged inputs or changing the controller baseline.
                if c.state['state'] not in ('PLAN_PENDING', 'DECISION_PENDING') or c.state['active_run'] or c.state.get('report'):
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                            'reason': 'Revision requires a paused baseline with no unresolved process or final report'}
                from .delegation import check_binding
                delegation = check_binding(c.state)
                clarifications.check_remaining_time(self.root, delegation)
                if not c.state.get('pending_clarification_revision') and c.state['revision_count'] >= delegation['limits']['revisions']:
                    resources.check_controller(c.state, 'revisions', c.state['revision_count'])
                    c.stop_with_report('L1 revision budget exhausted')
                    return self.status()
                try:
                    try:
                        proposal = json.loads(arguments['plan_json'])
                    except ValueError as exc:
                        raise planning.PlanRejected(str(exc), code='PLAN_JSON') from exc
                    planning.validate_proposal(proposal, delegation, c.state['implementation_attempts'])
                except planning.PlanRejected as exc:
                    planning.record_rejection(self.root, name, exc)
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                            'next': 'Correct the proposal and call l1_revise again; no baseline or revision counter changed'}
                # Never overwrite an approved file in place, even if staging fails.
                revisions = self.root / 'revisions'
                revisions.mkdir(exist_ok=True)
                tag = str(time.time_ns())
                requirements, plan = revisions / (tag + '.md'), revisions / (tag + '.json')
                requirements.write_text(arguments['requirements'], encoding='utf-8', newline='\n')
                atomic_json(plan, proposal)
                c.revise(requirements, plan)
                atomic_json(planning_path, {'status': 'PLAN_READY', 'facts_sha256': clarifications.signature(self.root),
                    'plan_sha256': digest(proposal), 'requirements_sha256': digest(arguments['requirements']),
                    'requires_revision': False})
            elif name == 'dot_run':
                if c.state['state'] not in ('EXECUTING', 'VERIFYING'):
                    raise ValueError('Only approved work can run')
                if c.state['active_run']:
                    c.stop_with_report('Prior process requires reconciliation; no redispatch')
                else:
                    c.adapter = DotAdapter(client, self.workers)
                    c.check_runner = lambda *args: run_check(*args, environment=self.worker_environment)
                    try:
                        execute(client, c)
                    except (ValueError, OSError, KeyError) as exc:
                        if c.state['state'] in ('EXECUTING', 'VERIFYING', 'DECISION_PENDING'):
                            c.stop_with_report(str(exc))
                        else:
                            raise
            return self.status()
