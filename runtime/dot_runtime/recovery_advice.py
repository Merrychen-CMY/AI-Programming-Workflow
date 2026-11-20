"""Actionable guidance from known outcomes; never changes state or grants authority."""


def advice(code, owner, steps, verify, entries=()):
    return {'code': code, 'owner': owner, 'steps': steps, 'verify': verify,
            'existing_entries': list(entries), 'advisory_only': True,
            'boundary': 'Use current authority and controller gates. Preserve failures and usage. '
                        'Do not replay unresolved operations or finalize without L1 assessment.'}


def tool_failure(result):
    kind = result.get('error_type')
    if kind in ('FileNotFoundError', 'NotADirectoryError'):
        return advice('LOCATE_INPUT', 'L2', [
            'Inspect the failing path and its nearest existing parent; search only the relevant project/tool location.',
            'Check whether the input must already exist or should be produced by an approved earlier step.',
            'Use the observed correct path, or propose the missing prerequisite action; do not create a placeholder.'],
            'Confirm the intended file/tool identity and rerun only the affected authorized operation.')
    if kind == 'PermissionError':
        return advice('FILE_ACCESS', 'L2', [
            'Identify the exact operation and path; check locks, access rights and the permitted file consumer.',
            'For encrypted environments inspect actual reader/writer behavior through the existing diagnostics.',
            'Propose an authorized path/consumer or request the missing permission via L1; do not disable protection.'],
            'Verify content and consumer behavior; reconcile any uncertain write before another attempt.')
    if kind in ('UnicodeDecodeError', 'LookupError'):
        return advice('READ_FORMAT', 'L2', [
            'Inspect the file type and encoding; select an installed authorized reader for binary/PDF/image content.',
            'Preserve the original file and record the source and any extraction limitations.'],
            'Compare extracted content with the actual source; do not treat unreadable text as missing requirements.')
    if result.get('status') in ('FAIL', 'ERROR', 'TIMEOUT'):
        return advice('TOOL_DIAGNOSIS', 'L2', [
            'Read the recorded error, exit status and evidence; identify a concrete changed condition or alternative.',
            'Investigate within the current assignment; send L1 only remaining baseline/resource/permission decisions.'],
            'Record the actual outcome and evidence of the affected check; no unchanged blind retry.')


def for_status(value):
    # Final reports and native user decisions are not self-repair opportunities.
    if value.get('report') or value.get('preflight_report') or value.get('user_stop'):
        return None
    state = value.get('state')
    if state in ('AUTHORIZATION_PENDING', 'API_AUTHORIZATION_PENDING'):
        return advice('AWAIT_AUTHORIZATION', 'L1', [
            'Present the pending exact proposal through its existing native approval entry; silence is not approval.'],
            'Confirm the recorded approval and current state before dependent work.')
    if (value.get('research_active') or value.get('recovery_required')
            or value.get('requirements_review', {}).get('active_run')
            or (value.get('decision') or {}).get('unresolved_product_operation')):
        return advice('RECONCILE_OPERATION', 'L1', [
            'Inspect existing process IDs, records and evidence with L2; do not repeat the original operation.',
            'Use the applicable existing native recovery entry and preserve the original uncertain record.'],
            'Verify operation outcome and the recovery receipt before execution resumes.')
    if state == 'API_REVIEW_PENDING':
        return advice('API_REVIEW', 'L1', [
            'Read api_review.unresolved and its diagnostic: distinguish NOT_SENT, REJECTED and unknown outcome.',
            'Assess timeout, remaining time and the next request through l1_review_api; ambiguous retry uses native approval.'],
            'Check the new receipt and ledger; increasing a timeout does not establish the previous request outcome.',
            ['l1_review_api'])
    if state == 'BUDGET_REVIEW_PENDING':
        return advice('RESOURCE_REVIEW', 'L1', [
            'Obtain remaining work, used resources, failure evidence and a concrete adjustment proposal from L2.',
            'Approve a justified allocation with l1_adjust_limits under the current delegation.'],
            'Confirm the effective allocation and unchanged cumulative consumption.', ['l1_adjust_limits'])
    planning = value.get('planning') or {}
    if planning.get('code') == 'API_INPUT_TOO_LARGE':
        return advice('COMPACT_INPUT', 'L1', [
            'Inspect component_bytes and planning_component_bytes; retain originals and remove repeated supplementary logs.',
            'Use stage-grouped facts and hash-bound evidence references. L2 reads omitted details itself.',
            'If the approved requirements need rewriting, compare goals/AC/constraints and independently reapprove.'],
            'Verify payload size and unchanged authority/acceptance before the next dot_plan; NOT_SENT is not API uncertainty.',
            ['dot_read_record', 'dot_plan'])
    if state == 'NEEDS_INFO':
        groups = {}
        for gap in planning.get('gaps', []):
            groups.setdefault(gap.get('stage', 'planning'), []).append(gap)
        result = advice('INVESTIGATE_GAPS', 'L2', [
            'Read existing evidence first; investigate only remaining technical facts using direct tools or dot_research.',
            'Separate planning prerequisites from later build/test/documentation/delivery prerequisites.',
            'Do not block unrelated work for a later-stage fact if the existing plan can enforce its dependency; '
            'otherwise ask L1 for a concrete baseline decision. Do not invent gating fields.',
            'Submit sourced facts and proposed wording/verification impact to L1, not a bare question list.'],
            'Confirm current-stage facts and enforced later-stage dependencies; use existing baseline review for actual changes.',
            ['dot_research', 'dot_read_record'])
        result['gaps_by_stage'] = groups
        return result
    if state == 'PLAN_REJECTED':
        return advice('CORRECT_DRAFT', 'L2', [
            'Read the saved validator feedback and correct the completed draft without changing approved goals.',
            'If the same input was rejected again, obtain new evidence or propose a different solution; do not repeat it.',
            'Resource or baseline decisions go to L1 with a concrete proposal.'],
            'Validate the revised plan before initialization and independent approval.', ['dot_plan'])
    if state == 'DECISION_PENDING':
        initialized = bool(value.get('run_id'))
        return advice('INVESTIGATE_DECISION' if initialized else 'REPAIR_REQUIREMENTS', 'L1', [
            'Read the current decision and latest independent findings; L2 obtains missing technical evidence.',
            ('Use dot_investigate then l1_resume_decision for an evidenced recovery, or revise the affected baseline.'
             if initialized else 'Correct the actual requirements issue with l1_prepare_requirements and request independent approval; do not initialize to bypass it.')],
            'Confirm the selected recovery and required revalidation; a pause is not final failure.',
            ['dot_investigate', 'l1_resume_decision'] if initialized else ['l1_prepare_requirements', 'l1_approve'])
