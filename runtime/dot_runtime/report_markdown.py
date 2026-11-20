"""Readable, immutable Markdown views of the existing L1 review records."""
import json
import re
from pathlib import Path
import uuid

from .snapshot import _filesystem_path, _reject_link, _root
from .input_material import bounded_view, encoded


def _block(value):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    fence = '`' * max(3, 1 + max((len(x) for x in re.findall(r'`+', text)), default=0))
    return fence + 'json\n' + text + '\n' + fence


def _write(directory, stem, title, sections):
    from .controller import file_hash
    import hashlib
    text = '# ' + title + '\n\n' + '\n\n'.join(
        '## ' + heading + '\n\n' + content for heading, content in sections) + '\n'
    content = text.encode('utf-8')
    sha = hashlib.sha256(content).hexdigest()
    path = _root(Path(directory), exists=False) / (stem + '-' + sha[:16] + '.md')
    disk = _filesystem_path(path)
    disk.parent.mkdir(parents=True, exist_ok=True)
    _root(path.parent, exists=True)
    if disk.exists():
        _reject_link(path)
        if disk.read_bytes() != content:
            raise ValueError('Existing Markdown report differs; cannot overwrite an earlier report')
    else:
        temporary = disk.with_name(disk.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as output:
                output.write(content)
            if disk.exists():
                _reject_link(path)
                if disk.read_bytes() != content:
                    raise ValueError('Existing Markdown report differs; cannot overwrite an earlier report')
            else:
                temporary.replace(disk)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return {'path': str(path), 'sha256': file_hash(path), 'title': title,
            'mime_type': 'text/markdown'}


def review(root, record, number, approval=None, usage=None, *, preflight=False):
    label = '需求' if record['kind'] == 'requirements' else '执行计划'
    approved = bool(approval and approval.get('review_number') == number)
    conclusion = 'L1 已在委托范围内批准。' if approved else 'L1 未批准；须按现有流程处理以下审核问题。'
    source = {key: value for key, value in record.items()
              if key not in ('markdown_report', 'markdown_error', 'markdown_required', 'markdown_context')}
    result = source.get('result', {})
    return _write(Path(root) / ('requirements-review/reports' if preflight else 'reports'),
        'l1-' + record['kind'] + '-review-' + str(number), 'L1 ' + label + '审核报告', [
            ('L1 结论', conclusion + '\n\n独立审核状态：' + str(record.get('status', '未记录'))),
            ('独立审核结果与未完成项', _block({'summary': result.get('summary'),
                'findings': result.get('findings', []), 'refusal': source.get('refusal'),
                'approval': approval if approved else None})),
            ('额度、实际消耗及调整', _block(usage if usage is not None else {'recorded': False})),
            ('审核基线与完整证据记录', _block({
                'baseline': source.get('baseline'), 'kind': source['kind'],
                'review_number': number, 'l1_session_id': source.get('l1_session_id'),
                'reviewer_session_id': result.get('session_id'),
                'evidence_files': source.get('evidence_files', []),
                'record': 'requirements-review/state.json' if preflight else 'state.json',
                'note': '完整结果、命令输出及历史保留在原始记录中；使用证据路径或分页读取，不在本报告重复嵌入。'})),
            ('使用说明', '本文件是现有审核记录的可读副本，不新增人工审批，不改变原始证据或授权。'
             '审核通过不等于产品已实现或已通过测试。')])


def final(root, report, *, preflight=False, state=None):
    import hashlib
    source = {key: value for key, value in report.items()
              if key not in ('markdown_report', 'markdown_error', 'markdown_required')}
    submission = source['l1_submission']
    closure = source.get('l1_closure_assessment', {}).get('assessment', {})
    tasks = state.get('approved_plan', {}).get('tasks') if state else None
    completion = ({'completed_tasks': state.get('completed_tasks', []),
                   'pending_tasks': [{k: bounded_view(task[k], 400) if k == 'instruction' else task[k]
                                      for k in ('id', 'instruction', 'depends_on') if k in task}
                                     for task in tasks if task['id'] not in state.get('completed_tasks', [])]}
                  if tasks is not None else {'approved_execution_plan': False,
                      'note': '尚无已批准的执行计划；未完成工作以 L1 结论和结案评估为准。'})
    issues = [{**{k: issue[k] for k in ('id', 'root_cause', 'candidate_id') if k in issue},
               **{k: bounded_view(issue.get('analysis', issue)[k], 600) for k in ('root_cause_key', 'summary',
                   'recommended_action', 'source_record', 'source_sha256') if k in issue.get('analysis', issue)}}
              for issue in source.get('issues', [])]
    governance = source.get('resource_governance', {
        'implementation_attempts': source.get('implementation_attempts'),
        'revision_count': source.get('revision_count'), 'l1_reviews': len(source.get('l1_reviews', []))})
    if 'history' in governance:
        changes = []
        for event in governance['history']:
            record = event.get('record', event)
            payload = record.get('payload', {})
            allocation = payload.get('after', {})
            changes.append({'at': record.get('at'), 'kind': record.get('kind'),
                'reason': payload.get('reason', allocation.get('reason', payload.get('proposal', {}).get('reason'))),
                'before': payload.get('before'), 'after': allocation.get('limits'),
                'changes': payload.get('changes')})
        governance = {**governance, 'history': changes}
    governance = {k: bounded_view(v, 4000) if k in ('history', 'budget_review', 'pending_authorization') else v
                  for k, v in governance.items()}
    def view(value):
        return _block(bounded_view(value, 12000))
    return _write(Path(root) / ('preflight-reports' if preflight else 'reports'),
        'l1-final', 'L1 最终审核报告', [
            ('L1 结论', submission['summary'] if len(submission['summary'].encode('utf-8')) <= 16000
             else view(submission['summary'])),
            ('DOT 原始结果', view({'outcome': source['outcome'], 'reason': source.get('reason'),
                'dot_report_sha256': submission['dot_report_sha256'],
                'delivery_executed': source.get('delivery_executed', False),
                'execution_started': source.get('execution_started')})),
            ('未完成项与限制', _block({k: bounded_view(v, 1600) for k, v in {
                'task_completion': completion, 'recorded_issues': issues,
                'closure_assessment': closure, 'approved_test_commands': source.get('approved_test_commands'),
                'documentation_checks': source.get('documentation_checks'), 'compliance': source.get('compliance'),
                'api_review': source.get('api_review')}.items()})),
            ('额度、实际消耗及调整', view(governance)),
            ('完整报告与证据索引', _block({
                'record': 'preflight-report.json' if preflight else 'final-report.json',
                'source_content_sha256': hashlib.sha256(encoded(source)).hexdigest(),
                'hash_scope': 'JSON excluding markdown_report, markdown_error and markdown_required; canonical sorted UTF-8 JSON',
                'issues_count': len(source.get('issues', [])),
                'l1_reviews_count': len(source.get('l1_reviews', [])),
                'note': '原始 JSON 保留全部证据、失败与消耗。摘要中的 omitted 表示省略展示，不表示不存在或通过；按原始记录路径分页查看。'})),
            ('审核边界', 'JSON 原始记录及其摘要仍是审批依据，本文件摘要也绑定在提交记录中。'
             '用户审核报告不授权重新执行、部署或消除未决操作。')])


def verify(report, root):
    """Validate a submitted Markdown view when present; old reports stay readable."""
    from .controller import file_hash
    descriptor = report.get('markdown_report')
    if descriptor is None:
        if report.get('markdown_required'):
            raise ValueError('Markdown report is pending; use the original L1 tool to generate it without replaying work')
        return None
    base = Path(root).resolve()
    path = Path(descriptor['path'])
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(base) or path.suffix != '.md':
        raise ValueError('Markdown report must stay inside the task report directory')
    current = base
    for part in path.relative_to(base).parts:
        current /= part
        _reject_link(current)
    if file_hash(path) != descriptor['sha256']:
        raise ValueError('Markdown report changed; the displayed assessment cannot be approved')
    return descriptor


def pending(record):
    return bool(record.get('markdown_required') and not record.get('markdown_report'))


def generate(record, renderer, *args, **kwargs):
    """A failed derived-file write must not masquerade as an unresolved worker."""
    record['markdown_required'] = True
    try:
        record['markdown_report'] = renderer(*args, **kwargs)
        record.pop('markdown_error', None)
    except (OSError, ValueError) as exc:
        record['markdown_error'] = {'code': 'MARKDOWN_GENERATION_FAILED', 'error_type': type(exc).__name__}


def delivery(record, retry):
    descriptor = record.get('markdown_report')
    if descriptor:
        return {'report_files': [descriptor]}
    return {'report_files': [], 'operation_status': 'COMPLETED_REPORT_PENDING',
            'report_generation': record.get('markdown_error'),
            'next': 'The original review or submission is recorded. Retry ' + retry +
                    ' only to generate its missing Markdown; do not rerun workers or change the original assessment.'}
