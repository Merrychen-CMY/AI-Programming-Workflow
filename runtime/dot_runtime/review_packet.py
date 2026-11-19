"""Controller-owned source records for independent review; never model assertions."""
import json
from pathlib import Path
from .snapshot import _filesystem_path


def prepare(controller, workspace, original, root, number):
    from .controller import ROOT, atomic_json, digest, file_hash, now, read_json
    from . import api_recovery, resources
    state = controller.state
    records = workspace / 'sources'
    _filesystem_path(records).mkdir()
    index = []

    def add(name, value, source):
        target = records / name
        _filesystem_path(target.parent).mkdir(parents=True, exist_ok=True)
        atomic_json(target, value)
        index.append({'path': 'sources/' + name, 'source': source, 'sha256': file_hash(target)})

    add('initial-snapshot.json', state['initial_snapshot'], 'controller initial_snapshot; local recorded baseline')
    add('usage.json', {'at': now(), 'run_id': state['run_id'], 'review_number': number,
        'includes_current_review': True, 'actual_usage': resources.usage(root, state) if root else {
            'implementation_attempts': state['implementation_attempts'], 'l1_reviews': len(state['l1_reviews']),
            'revisions': state.get('revision_count', 0)},
        'note': 'Point-in-time counters include this reserved review. Elapsed time advances; earlier snapshots are historical.'},
        'controller counters and API ledger; not L1 prose')
    if root:
        root = Path(root)
        for relative in ('api/ledger.json', 'resources.json', 'api-review.json'):
            source = root / relative
            if source.exists():
                add(relative, read_json(source), str(source))
        add('api-status.json', api_recovery.report(root), 'controller reconciliation of API ledger and retry grants')
        task = root.parent
        receipt_path, settings_path = task / 'confirmation.json', task / 'bridge.json'
        if receipt_path.exists() and settings_path.exists():
            receipt, settings = read_json(receipt_path), read_json(settings_path)
            if receipt['bridge_sha256'] != digest(settings) or receipt['delegation_sha256'] != digest(original):
                raise ValueError('Confirmed review source binding changed')
            add('startup-confirmation.json', receipt, str(receipt_path))
            environment = json.loads(settings.get('environment_context', '{}'))
            add('inspection.json', environment.get('inspection', {}),
                'environment_context.inspection in bridge.json bound by startup-confirmation.bridge_sha256')
    # Copy exact pinned package sources, not quoted descriptions of those sources.
    sources = ['templates/execution-plan.md', 'templates/local-execution-plan.example.json', 'DOT.md',
               'docs/runtime.md']
    standard_root = ROOT / 'standards/embedded-c/v1.3'
    disk_standard = _filesystem_path(standard_root)
    sources += [(standard_root / p.relative_to(disk_standard)).relative_to(ROOT).as_posix()
                for p in disk_standard.rglob('*') if p.is_file() and p.suffix in ('.md', '.json')]
    for name in sources:
        source = ROOT / name
        if not _filesystem_path(source).exists():
            continue
        target = records / 'workflow' / name
        _filesystem_path(target.parent).mkdir(parents=True, exist_ok=True)
        _filesystem_path(target).write_bytes(_filesystem_path(source).read_bytes())
        index.append({'path': 'sources/workflow/' + name, 'source': 'pinned workflow/' + name,
                      'sha256': file_hash(target)})
    atomic_json(workspace / 'source-index.json', {'at': now(), 'run_id': state['run_id'],
        'review_number': number, 'workflow_manifest_sha256': file_hash(ROOT / 'MANIFEST.json'),
        'records': index, 'limits': 'Local provenance, not a signed service. Existence checks are not builds or HIL.'})
    disk_workspace = _filesystem_path(workspace)
    paths = [workspace / p.relative_to(disk_workspace) for p in sorted(disk_workspace.rglob('*')) if p.is_file()]
    return [{'path': p.relative_to(controller.run_dir).as_posix(), 'sha256': file_hash(p)} for p in paths]
