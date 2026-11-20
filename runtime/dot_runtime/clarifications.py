"""Append-only facts within an unchanged delegation; facts never grant authority."""
import json
import re
import time
import uuid
from pathlib import Path

from .controller import atomic_json, digest, read_json


STAGES = ('planning', 'build', 'test', 'documentation', 'delivery')
UNRESOLVED = re.compile(r'\b(?:UNKNOWN|TBD|TODO|N/?A)\b|未知|待确认|待补充|尚未确定|未提供', re.I)


class PlanningNeedsInfo(ValueError):
    def __init__(self, reason, gaps, response_id):
        super().__init__(reason)
        self.reason, self.gaps, self.response_id = reason, gaps, response_id


def validate_facts(value):
    if not isinstance(value, dict) or set(value) != {'reason', 'facts'}:
        raise ValueError('Facts require reason and facts only; delegation/budget edits are forbidden')
    if not isinstance(value['reason'], str) or not value['reason'].strip():
        raise ValueError('Explain why these facts are being supplemented')
    facts = value['facts']
    if not isinstance(facts, list) or not 1 <= len(facts) <= 50:
        raise ValueError('Supply 1 to 50 sourced facts in one revision')
    names = set()
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != {'name', 'value', 'source', 'stage'}:
            raise ValueError('Each fact requires name, value, source and stage')
        if any(not isinstance(v, str) or not v.strip() for v in fact.values()):
            raise ValueError('Fact fields must be nonempty strings')
        if fact['stage'] not in STAGES or fact['name'] in names:
            raise ValueError('Invalid fact stage or duplicate name')
        if UNRESOLVED.fullmatch(fact['value'].strip()) or UNRESOLVED.fullmatch(fact['source'].strip()):
            raise ValueError('An unknown value or source is not a clarification')
        names.add(fact['name'])
    serialized = json.dumps(value, ensure_ascii=False)
    if 'REPLACE_WITH_' in serialized or 'sk-proj-' in serialized:
        raise ValueError('Facts contain placeholders or resemble an API secret')
    if len(serialized.encode('utf-8')) > 50_000:
        raise ValueError('Facts exceed the 50 KB limit; batch only relevant facts')
    return value


def records(root):
    root = Path(root)
    index = root / 'facts-index.json'
    entries = read_json(index) if index.exists() else []
    if not isinstance(entries, list):
        raise ValueError('Invalid clarification index')
    result, previous = [], None
    for number, entry in enumerate(entries, 1):
        if (not isinstance(entry, dict) or set(entry) != {'file', 'sha256'}
                or not re.fullmatch(r'[0-9a-f]{32}\.json', entry['file'])):
            raise ValueError('Invalid clarification record reference')
        record = read_json(root / 'facts' / entry['file'])
        if digest(record) != entry['sha256'] or record['previous'] != previous or record['revision'] != number:
            raise ValueError('Clarification history changed')
        validate_facts(record['supplement'])
        result.append(record)
        previous = entry['sha256']
    return result


def context(root):
    return json.dumps(records(root), ensure_ascii=False)


def planning_context(root):
    """Deduplicate identical facts, retaining conflicting values and their provenance."""
    from .input_material import material_packet
    history = records(root)
    facts = {}
    for record in history:
        for fact in record['supplement']['facts']:
            key = digest(fact)
            facts[key] = {**fact, 'last_revision': record['revision']}
    stages = {stage: [] for stage in STAGES}
    for fact in sorted(facts.values(), key=lambda f: f['last_revision'], reverse=True):
        stages[fact['stage']].append(fact)
    return json.dumps({
        'facts_by_stage': material_packet(root, stages, 18000),
        'full_history': material_packet(root, history, 1200),
        'revision_count': len(history),
        'instructions': 'Identical facts are listed once; conflicting values and sources remain. '
            'These observations do not override approved requirements or grant authority. Use current '
            'approval receipts to determine the baseline; investigate conflicts. Later-stage facts are '
            'not automatically planning blockers, but their dependent operations remain gated. '
            'Read referenced originals for omitted details; do not transcribe recorded facts again.'}, ensure_ascii=False)


def signature(root):
    return digest(records(root))


def check_remaining_time(root, delegation):
    # A facts update must not restart the original API/wall-time ledger.
    ledger = Path(root) / 'api' / 'ledger.json'
    if ledger.exists() and time.time() - read_json(ledger)['started_at'] >= delegation['limits']['wall_time_seconds']:
        if delegation.get('adaptive_limits'):
            from .resources import BudgetReview
            raise BudgetReview('wall_time_seconds', time.time() - read_json(ledger)['started_at'], 1,
                               delegation['limits']['wall_time_seconds'], delegation['limits']['wall_time_seconds'])
        raise ValueError('Delegated execution wall-time budget exhausted')


def append(root, value, l1_session_id, delegation, controller=None):
    validate_facts(value)
    check_remaining_time(root, delegation)
    root = Path(root)
    old = records(root)
    count = controller.state.get('revision_count', 0) if controller else len(old)
    if count >= delegation['limits']['revisions']:
        raise ValueError('L1 revision budget exhausted; facts cannot reset counters')
    if controller:
        # Reserve before writing: a crash cannot restore the consumed revision.
        controller.state['revision_count'] = count + 1
        controller.state['pending_clarification_revision'] = True
        controller.save()
    record = {'revision': len(old) + 1, 'previous': digest(old[-1]) if old else None,
              'at': time.time(), 'l1_session_id': l1_session_id, 'supplement': value,
              'authority': 'L1_SUPPLEMENT_PENDING_INDEPENDENT_REVIEW'}
    name = uuid.uuid4().hex + '.json'
    (root / 'facts').mkdir(exist_ok=True)
    atomic_json(root / 'facts' / name, record)
    index = [{'file': entry['file'], 'sha256': entry['sha256']}
             for entry in read_json(root / 'facts-index.json')] if old else []
    index.append({'file': name, 'sha256': digest(record)})
    atomic_json(root / 'facts-index.json', index)
    return record
