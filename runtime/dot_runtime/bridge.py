"""Project-bound L1 tools, serialized through one local control directory."""
from pathlib import Path
import os
import time

from .adapter import CodexAdapter
from .controller import Controller, ROOT, InitializationIncomplete, atomic_json, digest, file_hash, initialize, read_json, run_lock
from .delegation import ApprovalRejected, attach_delegation, approve_l1, submit_l1_report, validate_delegation
from .gpt_dot import ResponsesClient, DotAdapter, execute, propose_plan
from .checks import run_check
from . import clarifications
from . import planning
from . import resources
from . import api_recovery
from . import requirements_review


TOOLS = {
    'dot_research': ('Gather sourced technical facts using full Codex tools before planning or while paused; no product changes/tests.', {'question': 'string'}),
    'l1_prepare_requirements': ('Stage requirements before independent review and DOT planning.', {'requirements': 'string'}),
    'dot_investigate': ('Continue bounded read-only L2 investigation while paused. No product dispatch.', {'investigation_json': 'string'}),
    'l1_resume_decision': ('L1 selects an evidenced in-scope continuation after L2 analysis.', {'decision_json': 'string'}),
    'dot_read_record': ('Read a bounded page of this task record; no replay, writes or API request.',
                        {'path': 'string', 'offset': 'string'}),
    'l1_review_api': ('Assess an API failure or adjust its bounded wait; ambiguous retries require native approval.',
                     {'proposal_json': 'string', 'l1_session_id': 'string'}),
    'l1_adjust_limits': ('L1 approves finite resource allocations and increases under its delegation; preserve cumulative usage.',
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
    'l1_stop': ('Finalize only after explicit L1 assessment. reason is JSON matching templates/l1-closure.example.json; bare reasons are rejected.', {'reason': 'string'}),
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
        from .research import run as research_run
        client.research = lambda question: research_run(self.settings, self.workers, question,
            state=read_json(self.run_dir / 'state.json') if (self.run_dir / 'state.json').exists() else None)
        from .local_tools import LocalTools
        client.local_tools = LocalTools(self.settings, client, self.worker_environment)
        from .host_tools import HostTools
        relay = getattr(self, 'host_relay', None)
        client.host_tools = HostTools(self.settings, client, relay) if relay else None
        return client

    def status(self):
        result = self._status()
        from .upgrades import records_for_control
        result['runtime_upgrades'] = records_for_control(self.root)
        from .operation_recovery import records_for_control as operation_records
        result['operation_recoveries'] = operation_records(self.root)
        original = read_json(self.settings['delegation'])
        if resources.enabled(original, self.root):
            state = read_json(self.run_dir / 'state.json') if (self.run_dir / 'state.json').exists() else None
            governance = resources.report(self.root, original, state)
            result['resources'] = governance
            if not result.get('report') and not result.get('preflight_report'):
                if governance['pending_authorization'] or governance['budget_review']:
                    result['runtime_state'] = result['state']
                    pending = governance['pending_authorization']
                    needs_user = bool(pending and not (resources.l1_managed(original, self.root)
                                      and set(pending['proposal']['changes']) == {'limits'}))
                    result['state'] = 'AUTHORIZATION_PENDING' if needs_user else 'BUDGET_REVIEW_PENDING'
                    result['next_owner'] = 'user' if needs_user else 'L1'
        api = api_recovery.report(self.root)
        result['api_review'] = api
        if api['state'] != 'READY' and not result.get('report') and not result.get('preflight_report'):
            result.setdefault('runtime_state', result['state'])
            result['state'] = api['state']
            result['next_owner'] = 'user' if api['pending'] else 'L1'
        from .user_stop import status as stop_status
        result['user_stop'] = stop_status(self.root)
        if result['user_stop'] and not result.get('report') and not result.get('preflight_report'):
            result.setdefault('runtime_state', result['state'])
            result['state'] = result['user_stop']['state']
            result['next_owner'] = 'user' if result['state'] == 'USER_STOP_PENDING' else 'L1'
            result['next'] = result['user_stop']['next']
        from .recovery_advice import for_status
        advice = for_status(result)
        if advice:
            result['recovery_advice'] = advice
        return result

    def _status(self):
        planning = read_json(self.root / 'planning-status.json') if (self.root / 'planning-status.json').exists() else None
        facts = clarifications.records(self.root)
        common = {'planning': planning, 'facts_revision': len(facts),
                  'facts_sha256': clarifications.signature(self.root)}
        from .research import context as research_context
        common['research'] = research_context(self.root)
        from .research import active as research_pending
        common['research_active'] = research_pending(self.root)
        from .initialization_recovery import records_for_control
        common['initialization_recoveries'] = records_for_control(self.root)
        common['requirements_review'] = requirements_review.status(self.root)
        if not (self.run_dir / 'state.json').exists():
            if (self.run_dir / 'initialization.json').exists():
                common['initialization'] = read_json(self.run_dir / 'initialization.json')
                common['recovery_required'] = True
            preflight = read_json(self.root / 'preflight-report.json') if (self.root / 'preflight-report.json').exists() else None
            if preflight:
                reviewed = (self.root.parent / 'preflight-review.json').exists()
                return {**common, 'state': 'BLOCKED' if reviewed else 'REPORT_PENDING', 'preflight_report': preflight,
                        'next_owner': 'user' if preflight.get('l1_submission') else 'L1',
                        'report': {'outcome': preflight['outcome'], 'l1_submitted': bool(preflight.get('l1_submission'))}}
            initial_review = common['requirements_review']
            state_name = (planning['status'] if planning and planning['status'] in ('NEEDS_INFO', 'PLAN_REJECTED')
                          else initial_review['state'] if self.settings.get('requirements_first') and initial_review['prepared'] else 'DISCOVERY')
            if self.settings.get('requirements_first') and initial_review['prepared']:
                if initial_review['state'] in ('REQUIREMENTS_PENDING', 'DECISION_PENDING'):
                    state_name = initial_review['state']
                elif initial_review['state'] == 'REQUIREMENTS_APPROVED' and state_name in ('NEEDS_INFO', 'PLAN_REJECTED'):
                    common['next'] = ('Requirements are approved; L2 replans with the current approved text and retained '
                        'diagnostics using dot_plan. The previous planning refusal is history, not a new requirements veto.')
            if initial_review.get('active_run'):
                state_name = 'DECISION_PENDING'
                common['next'] = ('Read requirements-review/state.json; prepare verified recovery with '
                    'l1_prepare_recovery, then workflow_recover_operation for native approval. '
                    'Do not revise, reapprove or replay the unresolved requirements review.')
            elif self.settings.get('requirements_first') and state_name == 'DECISION_PENDING':
                common['next'] = ('Read requirements_review.latest_review findings; correct the requirements with '
                    'l1_prepare_requirements, then l1_approve kind=requirements before dot_plan. '
                    'No initialized baseline exists for l1_revise.')
            return {**common, 'state': state_name,
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
        from .input_material import InputRejected
        try:
            return self._call(name, arguments)
        except InputRejected as exc:
            with run_lock(self.root):
                import uuid
                record = 'input-rejections/' + uuid.uuid4().hex + '.json'
                (self.root / 'input-rejections').mkdir(exist_ok=True)
                atomic_json(self.root / record, {'operation': name, 'diagnostic': exc.diagnostic})
                if name == 'dot_plan':
                    planning.record_rejection(self.root, name, planning.PlanRejected(
                        str(exc), code='API_INPUT_TOO_LARGE', details=exc.diagnostic))
                if (self.run_dir / 'state.json').exists():
                    with run_lock(self.run_dir):
                        c = Controller(self.run_dir)
                        if c.state['state'] in ('EXECUTING', 'VERIFYING') and not c.state['active_run'] and not c.state['report']:
                            atomic_json(self.run_dir / 'decision-request.json', {
                                'reason': str(exc), 'diagnostic': exc.diagnostic, 'record': record})
                            c.transition('DECISION_PENDING', 'Unsent input refusal requires L1 material review')
                return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                        'reason': str(exc), 'input_rejection': exc.diagnostic, 'record': record,
                        'recovery': {'coordinator': 'L1', 'investigation_owner': 'L2',
                            'user_action_required': False, 'automatic_evidence_views': True,
                            'steps': [
                                'L1 inspects component_bytes/planning_component_bytes and the original record; do not repeat unchanged input.',
                                'Retain full history locally; send current facts, relevant excerpts and hash-bound evidence references, not repeated logs.',
                                'L2 reads evidence itself and diagnoses obtainable technical facts; L1 owns organizing and approving the handoff.',
                                'If approved requirements need a concise replacement, L1 compares every goal, hard constraint, AC and permission, '
                                'then stages and independently reapproves the equivalent baseline before dot_plan.',
                                'Preserve existing unknown operations and cumulative usage. Request user input only for a real change beyond authority.']},
                        'next': 'L1 must actively organize recovery using these steps; preserve originals, not repeated request copies. '
                                'No API retry approval is needed for this NOT_SENT request. Material size alone is not grounds for l1_stop.'}
        except ApprovalRejected as exc:
            with run_lock(self.root):
                return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                        'reason': str(exc)}
        except api_recovery.ApiReview:
            with run_lock(self.root):
                return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_API_REVIEW',
                        'next': 'L1 inspects API diagnostics, remaining work and budgets, then l1_review_api; '
                                'do not submit a final failure merely because a request timed out'}
        except resources.BudgetReview as exc:
            with run_lock(self.root):
                original = read_json(self.settings['delegation'])
                state = read_json(self.run_dir / 'state.json') if (self.run_dir / 'state.json').exists() else None
                resources.pause(self.root, original, exc, state)
                return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_BUDGET_REVIEW'}

    def preflight_report(self, reason, *, l1_assessment=None):
        if (self.root / 'preflight-report.json').exists():
            raise ValueError('Existing final report cannot be replaced or reopened')
        if l1_assessment is None:
            planning.record_rejection(self.root, 'dot_plan', planning.PlanRejected(reason,
                code='L1_ASSESSMENT_REQUIRED', details={'next': 'L2 considers in-scope solutions and submits options and permission gaps to L1; do not finalize'}))
            return
        from .closure import validate_assessment
        original = read_json(self.settings['delegation'])
        l1_assessment = validate_assessment(l1_assessment, root=self.root, original=original)
        from .closure import evidence_records
        from .controller import now
        record = {'created_at': now(), 'outcome': 'FAILED', 'reason': reason, 'execution_started': False,
                  'owner': 'L1', 'user_review_required': True, 'planning_rejections': planning.history(self.root),
                  'l1_closure_assessment': {'assessment': l1_assessment, 'entry': 'l1_stop',
                      'verified_evidence': evidence_records(l1_assessment, self.root)}}
        from .upgrades import records_for_control
        record['runtime_upgrades'] = records_for_control(self.root)
        from .initialization_recovery import records_for_control as recovery_records
        record['initialization_recoveries'] = recovery_records(self.root)
        from .operation_recovery import records_for_control as operation_records
        record['operation_recoveries'] = operation_records(self.root)
        record['api_review'] = api_recovery.report(self.root)
        record['requirements_review'] = requirements_review.status(self.root)
        if record['api_review']['unresolved'] or record['requirements_review'].get('active_run'):
            record['outcome'] = 'UNKNOWN'
        if resources.enabled(original, self.root):
            record['resource_governance'] = resources.report(self.root, original)
        atomic_json(self.root / 'preflight-report.json', record)

    def _call(self, name, arguments):
        import json
        if name not in TOOLS or not isinstance(arguments, dict) or set(arguments) != set(TOOLS[name][1]):
            raise ValueError('Unknown tool or invalid arguments')
        if any(not isinstance(value, str) or not value.strip() for value in arguments.values()):
            return {'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                    'reason': 'Tool arguments must be nonempty strings; no operation started'}
        with run_lock(self.root):
            if name == 'dot_read_record':
                from .presentation import read_record
                return read_record(self.root, arguments['path'], arguments['offset'])
            if (self.root.parent / 'operation-recovery-pending.json').exists():
                if name == 'dot_status':
                    result = self.status()
                    return {**result, 'runtime_state': result['state'], 'state': 'OPERATION_RECOVERY_PENDING',
                            'next': 'workflow_recover_operation completes the already approved local transaction; no task replay'}
                raise ValueError('OPERATION_RECOVERY_PENDING: finish native recovery before task mutations')
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
            from .report_markdown import delivery
            if name == 'l1_approve':
                from .delegation import repair_review_report
                repaired = (repair_review_report(c, arguments['kind']) if c else
                            arguments['kind'] == 'requirements' and requirements_review.repair_review_report(self.settings))
                if repaired:
                    record = (c.state['l1_reviews'][-1] if c else requirements_review.status(self.root)['reviews'][-1])
                    return {**self.status(), **delivery(record, 'l1_approve kind=' + arguments['kind'])}
            if name == 'l1_submit_report':
                if c:
                    submit_l1_report(c, arguments['summary'], arguments['l1_session_id'])
                    report = read_json(self.run_dir / 'final-report.json')
                else:
                    from .delegation import submit_preflight_report
                    report = submit_preflight_report(self.root, arguments['summary'], arguments['l1_session_id'])
                return {**self.status(), **delivery(report, 'l1_submit_report')}
            if name == 'l1_stop':
                from .closure import validate_assessment
                try:
                    assessment = validate_assessment(arguments['reason'], root=self.root,
                        state=c.state if c else None, original=read_json(self.settings['delegation']))
                except ValueError as exc:
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED', 'reason': str(exc)}
                if c:
                    c.stop_with_report(assessment['summary'], l1_assessment=assessment)
                elif not (self.root / 'preflight-report.json').exists():
                    self.preflight_report(assessment['summary'], l1_assessment=assessment)
                return self.status()
            if c is None and (self.root / 'preflight-report.json').exists():
                raise ValueError('Preflight has ended with a final failure report; no automatic resumption')
            from .user_stop import status as stop_status
            if stop_status(self.root):
                return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                        'reason': 'USER_STOP: dispatch paused; obtain native stop decision or let L1 assess closure, without replay'}
            original = read_json(self.settings['delegation'])
            adaptive = resources.enabled(original, self.root)
            from .research import active as research_pending
            if research_pending(self.root):
                return {**self.status(), 'accepted': False, 'reason': 'Research worker outcome needs reconciliation; use l1_prepare_recovery and workflow_recover_operation. No replay.'}
            if name == 'l1_review_api':
                try:
                    result = api_recovery.review(self.root, original, json.loads(arguments['proposal_json']),
                                                 arguments['l1_session_id'], self.project)
                except (ValueError, TypeError, KeyError) as exc:
                    return {**self.status(), 'accepted': False, 'reason': str(exc)}
                return {**self.status(), **result}
            if name in ('l1_adjust_limits', 'l1_request_authorization'):
                if ((c and (c.state.get('active_run') or c.state.get('analysis_activity') or c.state.get('report')))
                        or requirements_review.status(self.root).get('active_run')):
                    return {**self.status(), 'accepted': False, 'reason': 'Unknown operation or final report requires a separate verified recovery; cannot rewrite it'}
                try:
                    proposal = json.loads(arguments['proposal_json'])
                    action = resources.adjust if name == 'l1_adjust_limits' else resources.propose_authorization
                    result = action(self.root, original, proposal, arguments['l1_session_id'], c.state if c else None)
                except (ValueError, TypeError, KeyError) as exc:
                    return {**self.status(), 'accepted': False, 'reason': str(exc)}
                return {**self.status(), 'accepted': True, 'resource_result': result}
            if api_recovery.unresolved(self.root) or api_recovery.load(self.root)['pending']:
                return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_API_REVIEW',
                        'reason': 'Review the recorded API request first; do not replay, approve or initialize while unresolved'}
            replanning_for_budget = False
            if adaptive:
                journal = resources.read(self.root, original)
                replanning_for_budget = (name == 'dot_plan' and not journal['pending']
                    and journal['pause'] and journal['pause']['key'] == 'implementation_attempts'
                    and not (c and (c.state.get('active_run') or c.state.get('analysis_activity') or c.state.get('report')))
                    and not requirements_review.status(self.root).get('active_run'))
                if journal['pending'] or (journal['pause'] and not replanning_for_budget):
                    return {**self.status(), 'accepted': False, 'reason': 'Resolve resource review or native authorization before continuing'}
            if name == 'dot_research':
                if ((c and (c.state.get('active_run') or c.state.get('analysis_activity') or c.state.get('report')))
                        or requirements_review.status(self.root).get('active_run')):
                    return {**self.status(), 'accepted': False, 'reason': 'Finish or reconcile the active operation before a new investigation'}
                from .research import run as research_run
                result = research_run(self.settings, self.workers, arguments['question'], state=c.state if c else None)
                return {**self.status(), 'accepted': result['status'] == 'PASS', 'investigation': result,
                        'next': 'Use sourced technical observations in the plan; changed goals/AC still require L1 baseline revision'}
            if name == 'l1_prepare_requirements':
                if c:
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                            'reason': 'Initialized baseline changes use l1_revise and independent approvals'}
                try:
                    result = requirements_review.prepare(self.settings, arguments['requirements'])
                except ValueError as exc:
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED', 'reason': str(exc)}
                return {**self.status(), 'requirements_preparation': result}
            if name == 'l1_approve' and c is None and arguments['kind'] == 'requirements':
                previous_count = requirements_review.review_count(self.root)
                try:
                    result = requirements_review.approve(self.settings, arguments['l1_session_id'], self.workers)
                except ValueError as exc:
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED', 'reason': str(exc)}
                report_result = (delivery(result['reviews'][-1], 'l1_approve kind=requirements')
                                 if len(result['reviews']) > previous_count else {'report_files': []})
                return {**self.status(), 'requirements_approval': result, **report_result}
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
                    count = c.state.get('revision_count', 0) if c else requirements_review.revision_count(self.root)
                    resources.require(original, self.root, 'revisions', count)
                    if count >= operating['limits']['revisions']:
                        raise ValueError('L1 revision budget exhausted; no new facts revision')
                    if c and (c.state['state'] not in ('PLAN_PENDING', 'DECISION_PENDING')
                              or c.state['active_run'] or c.state.get('report')):
                        raise ValueError('Facts require a paused planning decision with no unresolved process or final report')
                    if api_recovery.unresolved(self.root):
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
                return {**self.status(), 'accepted': True,
                        'next': ('l1_prepare_requirements with the updated facts, then l1_approve kind=requirements, '
                                 'then dot_plan with the approved requirements; no new startup confirmation'
                                 if c is None and self.settings.get('requirements_first')
                                 else 'dot_plan with updated requirements; no new startup confirmation')}
            if name == 'dot_plan':
                approval = None
                if c is None and self.settings.get('requirements_first'):
                    try:
                        approval = requirements_review.require_approved(self.settings, arguments['requirements'])
                    except ValueError as exc:
                        return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED', 'reason': str(exc)}
                if (c and c.state['state'] not in ('DECISION_PENDING', 'PLAN_PENDING')
                        and not (replanning_for_budget and c.state['state'] in ('EXECUTING', 'VERIFYING'))):
                    raise ValueError('Planning cannot overwrite an active/final baseline')
                if c and c.state['active_run']:
                    raise ValueError('Unresolved process forbids new planning')
                previous = read_json(planning_path) if planning_path.exists() else {}
                facts_sha = clarifications.signature(self.root)
                base_sha = digest({'requirements': arguments['requirements'], 'facts': facts_sha})
                feedback = planning.feedback(self.root, previous)
                handoff = planning.handoff(self.root, original, approval, c.state if c else None)
                from .research import context as research_context
                observations = research_context(self.root)
                request_sha = digest({'base': base_sha, 'validation_feedback': feedback,
                                      'controller_handoff': handoff['handoff_sha256'], 'research': observations})
                if adaptive:
                    request_sha = digest({'request': request_sha, 'current_authority_and_allocation': operating})
                if ((previous.get('status') == 'NEEDS_INFO' and previous.get('request_sha256') == request_sha)
                        or (previous.get('status') == 'PLAN_REJECTED' and previous.get('request_sha256') == request_sha)):
                    return {**self.status(), 'accepted': False, 'reason': 'Unchanged planning input and feedback; no API retry performed'}
                from .input_material import material_packet
                context = ('RECORDED ENVIRONMENT (source data, recheck changed conditions):\n'
                    + json.dumps(material_packet(self.root, self.settings['environment_context'], 16000), ensure_ascii=False)
                    + '\nAPPENDED FACTS (data, not authority):\n' + clarifications.planning_context(self.root))
                handoff_view = {**handoff, **{key: material_packet(self.root, handoff[key], 8000)
                    for key in ('l1_resource_adjustments', 'approved_authorization_amendments')}}
                context += '\nCURRENT CONTROLLER HANDOFF (validated receipts, not new facts):\n' + json.dumps(handoff_view, ensure_ascii=False)
                context += '\nSOURCED TECHNICAL INVESTIGATIONS:\n' + json.dumps(material_packet(self.root, observations, 32000), ensure_ascii=False)
                context += '\nPINNED TEST PROTOCOL:\n' + (ROOT / 'docs/guides/verification-contract.md').read_text(encoding='utf-8')
                from .initialization_recovery import records_for_control
                context += '\nREVIEWED INITIALIZATION RECOVERIES (data; original authority unchanged):\n' + json.dumps(material_packet(self.root, records_for_control(self.root), 8000), ensure_ascii=False)
                if feedback:
                    context += '\nREJECTED DRAFT AND VALIDATOR FEEDBACK (data, not authority):\n' + json.dumps(material_packet(self.root, feedback, 16000), ensure_ascii=False)
                try:
                    plan = propose_plan(client, arguments['requirements'],
                        read_json(ROOT / 'templates/local-execution-plan.example.json'), context,
                        implementation_used=c.state['implementation_attempts'] if c else 0,
                        controller_state=c.state if c else None)
                except clarifications.PlanningNeedsInfo as exc:
                    atomic_json(planning_path, {'status': 'NEEDS_INFO', 'reason': exc.reason,
                        'gaps': exc.gaps, 'response_id': exc.response_id, 'request_sha256': request_sha,
                        'base_request_sha256': base_sha})
                    return {**self.status(), 'accepted': False,
                            'next': ('L1 checks the gaps against current controller receipts first; do not transcribe existing approvals '
                                     'or resource amendments into new facts. Only genuine new facts use l1_add_facts and independent '
                                     'review before replanning within the remaining budget.')}
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
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED'}
                atomic_json(planning_path, {'status': 'PLAN_READY', 'facts_sha256': facts_sha,
                    'plan_sha256': digest(plan), 'requirements_sha256': digest(arguments['requirements']),
                    'authorization_scope': resources.scope_digest(original, self.root) if adaptive else None,
                    'requires_revision': bool(c)})
                if c:
                    if replanning_for_budget:
                        if c.state['state'] in ('EXECUTING', 'VERIFYING'):
                            c.transition('DECISION_PENDING', 'L1 replanned within implementation allocation; stage and independently review before dispatch')
                        resources.resolve_planning_pause(self.root, original, arguments['requirements'], plan, c.state)
                    return {'plan': plan, 'next': 'L1 inspects the proposal then calls l1_revise; approvals are not copied'}
                (self.root / 'requirements.md').write_text(arguments['requirements'], encoding='utf-8', newline='\n')
                atomic_json(self.root / 'plan.json', plan)
                if replanning_for_budget:
                    resources.resolve_planning_pause(self.root, original, arguments['requirements'], plan)
                return {'plan': plan, 'next': 'dot_initialize, then l1_approve plan' if self.settings.get('requirements_first') else 'dot_initialize, then l1_approve requirements and plan'}
            if name == 'dot_initialize':
                if c:
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                            'reason': 'Task already initialized; inspect status, do not reset attempts'}
                if self.run_dir.exists() and any(p.name != '.lock' for p in self.run_dir.iterdir()):
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                            'recovery_required': True, 'reason': 'Partial initialization requires native recovery review; no files overwritten'}
                if self.settings.get('requirements_first'):
                    try:
                        requirements_review.require_approved(self.settings)
                    except ValueError as exc:
                        return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED', 'reason': str(exc)}
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
                def prepare_initial_state(state, run_dir):
                    from .delegation import prepare_delegation
                    state['clarification_root'] = str(self.root)
                    prepare_delegation(state, self.settings['delegation'], self.settings['raw_input'])
                    state['delegated_started_at'] = read_json(self.root / 'api/ledger.json')['started_at']
                    state['revision_count'] = requirements_review.revision_count(self.root)
                    if self.settings.get('requirements_first'):
                        requirements_review.prepare_adoption(state, run_dir, self.settings)
                        state['state'] = 'PLAN_PENDING'
                        from .controller import now
                        state['history'].append({'at': now(), 'from': 'REVIEW_PENDING', 'to': 'PLAN_PENDING',
                            'reason': 'Adopted independent requirements approval obtained before DOT planning'})
                try:
                    initialize(self.project, self.root / 'requirements.md', self.root / 'plan.json', self.run_dir,
                               prepare_state=prepare_initial_state)
                except InitializationIncomplete as exc:
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_INITIALIZATION_FAILED',
                            'recovery_required': True, 'reason': str(exc)}
                c = Controller(self.run_dir)
            elif c is None and name == 'l1_revise':
                return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                        'reason': ('No initialized baseline exists; use l1_prepare_requirements, then '
                                   'l1_approve kind=requirements before dot_plan and dot_initialize'
                                   if self.settings.get('requirements_first') else
                                   'No initialized baseline exists; use dot_plan to correct the draft, then dot_initialize')}
            elif c is None:
                return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                        'reason': 'Initialize the task first; no worker started'}
            elif not c.state.get('delegation'):
                raise ValueError('Incomplete bridge initialization; do not dispatch or synthesize approvals')
            elif name == 'l1_approve':
                previous_count = len(c.state['l1_reviews'])
                approve_l1(c, arguments['kind'], arguments['l1_session_id'], self.workers)
                report_result = (delivery(c.state['l1_reviews'][-1], 'l1_approve kind=' + arguments['kind'])
                                 if len(c.state['l1_reviews']) > previous_count else {'report_files': []})
                return {**self.status(), **report_result}
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
                    from .controller import pending_dispatch_count
                    try:
                        remaining = pending_dispatch_count(c.state, arguments['requirements'], proposal)
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        raise planning.PlanRejected(str(exc), proposal=proposal) from exc
                    planning.validate_proposal(proposal, delegation, c.state['implementation_attempts'],
                                               pending_dispatches=remaining)
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
            elif name in ('dot_investigate', 'l1_resume_decision'):
                c.adapter = DotAdapter(client, self.workers)
                try:
                    if name == 'dot_investigate':
                        c.investigate(arguments['investigation_json'])
                    else:
                        c.resume_decision(arguments['decision_json'])
                except (ValueError, OSError, KeyError) as exc:
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED', 'reason': str(exc)}
            elif name == 'dot_run':
                if c.state['state'] not in ('EXECUTING', 'VERIFYING'):
                    return {**self.status(), 'accepted': False, 'operation_status': 'COMPLETED_REJECTED',
                            'reason': 'Only approved work can run; no API dispatch or worker started'}
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
