"""Native-reviewed reconciliation of a stopped operation, without replaying it.

Local files are an audit trail, not a boundary against the same OS account. A
model proposal never supplies human approval or a process-liveness verdict.
"""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from .controller import atomic_json, digest, file_hash, now, read_json, run_lock
from .initialization_recovery import _encoded_hash, _identity, _path
from .snapshot import _filesystem_path, _reject_link, _root

PROPOSAL = 'control/operation-recovery-proposal.json'
JOURNAL = 'operation-recoveries.json'
PENDING = 'operation-recovery-pending.json'
TERMINAL = ('REPORT_PENDING', 'DELIVERY_READY', 'SUCCESS', 'FAILED', 'INCOMPLETE', 'UNKNOWN')


def _files(task):
    _root(task, exists=True)
    files = {}
    def walk(directory):
        for item in sorted(_filesystem_path(directory).iterdir()):
            path = directory / item.name
            mode = _reject_link(path).st_mode
            name = path.relative_to(task).as_posix()
            if name == PENDING or path.name == '.lock':
                continue
            if stat.S_ISDIR(mode):
                walk(path)
            elif stat.S_ISREG(mode):
                files[name] = file_hash(path)
            else:
                raise ValueError('Recovery requires regular local records')
    walk(task)
    return files


def boot_identity():
    """Stable boot identity from the OS; unavailable is not proof of a reboot."""
    try:
        if sys.platform.startswith('linux'):
            return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if os.name == 'nt':
            import ctypes
            query = ctypes.WinDLL('ntdll').NtQuerySystemInformation
            query.argtypes = (ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
            query.restype = ctypes.c_long
            information, length = ctypes.create_string_buffer(48), ctypes.c_ulong()
            if query(3, information, len(information), ctypes.byref(length)) == 0:
                boot_time = ctypes.c_longlong.from_buffer(information).value
                return 'windows-boot-' + str(boot_time) if boot_time > 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def pid_status(pid):
    """Probe without signalling or killing; permission errors remain UNKNOWN."""
    if type(pid) is not int or pid <= 0:
        return 'UNKNOWN'
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return 'STOPPED' if ctypes.get_last_error() == 87 else 'UNKNOWN'
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return 'UNKNOWN'
            return 'ACTIVE' if code.value == 259 else 'STOPPED'
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return 'ACTIVE'
    except ProcessLookupError:
        return 'STOPPED'
    except (PermissionError, OSError):
        return 'UNKNOWN'


def record_process_start(output, pid):
    path = Path(output) / 'process-record.json'
    if path.exists():
        raise ValueError('A process evidence directory must be fresh')
    record = {'schema_version': 1, 'pid': pid, 'boot_id': boot_identity(), 'started_at': now(),
              'status': 'RUNNING', 'process_tree_status': 'UNKNOWN'}
    atomic_json(path, record)
    return record


def record_process_end(output, pid, *, tree_stopped=False):
    path = Path(output) / 'process-record.json'
    record = read_json(path)
    if record.get('pid') != pid:
        raise ValueError('Process identity changed while recording exit')
    record.update(ended_at=now(), status='EXITED', process_tree_status='STOPPED' if tree_stopped else 'UNKNOWN')
    atomic_json(path, record)
    return record


def process_inventory(*, metadata_only=False):
    """Read the current user's entire local process list, including ancestry.

    Never accept an inaccessible process as absent. System/other-user processes
    cannot be children of this user's sandboxed worker and are excluded only
    when ownership is known. Windows uses native process APIs; Linux reads procfs directly.
    metadata_only reads ancestry across owners for completed-review reconciliation;
    it does not provide the command/owner evidence needed by general recovery.
    """
    if os.name == 'nt':
        return _windows_process_inventory(metadata_only=metadata_only)
    if sys.platform.startswith('linux'):
        rows = []
        for item in Path('/proc').iterdir():
            if not item.name.isdecimal():
                continue
            try:
                if not metadata_only and item.stat().st_uid != os.getuid():
                    continue
                raw = (item / 'stat').read_text()
                fields = raw[raw.rfind(')') + 2:].split()
                command = '' if metadata_only else (item / 'cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', errors='replace').strip()
                if not metadata_only and not command and fields[0] != 'Z':
                    raise ValueError('Process command line unavailable: ' + item.name)
                rows.append({'pid': int(item.name), 'ppid': int(fields[1]), 'command': command,
                             'name': raw[raw.find('(') + 1:raw.rfind(')')]})
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError as exc:
                raise ValueError('Permission denied reading process ' + item.name) from exc
        return rows
    raise ValueError('Independent process inventory is not implemented for this OS')


def _windows_process_inventory(*, metadata_only=False):
    """Use native read-only process APIs; no elevation or process termination."""
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    ntdll = ctypes.WinDLL('ntdll')
    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', w.DWORD),
                    ('cntThreads', w.DWORD), ('th32ParentProcessID', w.DWORD),
                    ('pcPriClassBase', w.LONG), ('dwFlags', w.DWORD), ('szExeFile', w.WCHAR * 260)]
    class UnicodeString(ctypes.Structure):
        _fields_ = [('Length', w.USHORT), ('MaximumLength', w.USHORT), ('Buffer', ctypes.c_void_p)]
    kernel.CreateToolhelp32Snapshot.argtypes = (w.DWORD, w.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        function = getattr(kernel, name)
        function.argtypes = (w.HANDLE, ctypes.POINTER(ProcessEntry)); function.restype = w.BOOL
    kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD); kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    kernel.ProcessIdToSessionId.argtypes = (w.DWORD, ctypes.POINTER(w.DWORD)); kernel.ProcessIdToSessionId.restype = w.BOOL
    advapi.OpenProcessToken.argtypes = (w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)); advapi.OpenProcessToken.restype = w.BOOL
    advapi.GetTokenInformation.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD))
    advapi.GetTokenInformation.restype = w.BOOL
    advapi.GetLengthSid.argtypes = (ctypes.c_void_p,); advapi.GetLengthSid.restype = w.DWORD
    query = ntdll.NtQueryInformationProcess
    query.argtypes = (w.HANDLE, w.ULONG, ctypes.c_void_p, w.ULONG, ctypes.POINTER(w.ULONG)); query.restype = ctypes.c_long
    def user_sid(handle):
        token, size = w.HANDLE(), w.DWORD()
        if not advapi.OpenProcessToken(handle, 8, ctypes.byref(token)):
            return None
        try:
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not size.value: return None
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, 1, buffer, size.value, ctypes.byref(size)): return None
            pointer = ctypes.c_void_p.from_buffer(buffer).value
            length = advapi.GetLengthSid(pointer)
            return ctypes.string_at(pointer, length) if length else None
        finally:
            kernel.CloseHandle(token)
    own = kernel.OpenProcess(0x1000, False, os.getpid())
    if not own: raise ValueError('Cannot query current process identity')
    try:
        owner = user_sid(own)
    finally:
        kernel.CloseHandle(own)
    session = w.DWORD()
    if not owner or not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ValueError('Cannot establish current process owner/session')
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value: raise ValueError('Windows process inventory is unavailable')
    rows, unknown = [], []
    try:
        item = ProcessEntry(); item.dwSize = ctypes.sizeof(item)
        valid = kernel.Process32FirstW(snapshot, ctypes.byref(item))
        if not valid: raise ValueError('Windows process inventory is empty or unreadable')
        while valid:
            pid, ppid, name = int(item.th32ProcessID), int(item.th32ParentProcessID), item.szExeFile
            valid = kernel.Process32NextW(snapshot, ctypes.byref(item))
            if pid <= 4: continue
            if metadata_only:
                rows.append({'pid': pid, 'ppid': ppid, 'name': name})
                continue
            observed_session = w.DWORD()
            if not kernel.ProcessIdToSessionId(pid, ctypes.byref(observed_session)):
                if pid_status(pid) != 'STOPPED': unknown.append(pid)
                continue
            if observed_session.value != session.value: continue
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                if pid_status(pid) != 'STOPPED': unknown.append(pid)
                continue
            try:
                identity = user_sid(handle)
                if identity is None:
                    if pid_status(pid) != 'STOPPED': unknown.append(pid)
                    continue
                if identity != owner: continue
                size = w.ULONG()
                query(handle, 60, None, 0, ctypes.byref(size))
                if not size.value or size.value > 1_000_000:
                    if pid_status(pid) != 'STOPPED': unknown.append(pid)
                    continue
                buffer = ctypes.create_string_buffer(size.value)
                if query(handle, 60, buffer, size.value, ctypes.byref(size)) != 0:
                    if pid_status(pid) != 'STOPPED': unknown.append(pid)
                    continue
                text = UnicodeString.from_buffer(buffer)
                address, begin, end = text.Buffer or 0, ctypes.addressof(buffer), ctypes.addressof(buffer) + len(buffer)
                if text.Length % 2 or not begin <= address <= address + text.Length <= end:
                    unknown.append(pid); continue
                command = ctypes.string_at(address, text.Length).decode('utf-16-le')
                if not command.strip(): unknown.append(pid); continue
                rows.append({'pid': pid, 'ppid': ppid, 'name': name, 'command': command})
            finally:
                kernel.CloseHandle(handle)
    finally:
        kernel.CloseHandle(snapshot)
    if unknown:
        raise ValueError('Process ownership or command line could not be read for PID(s): ' + ', '.join(map(str, unknown[:20])))
    return rows


def process_creation_time(pid):
    """OS identity evidence for Windows PID reuse; absence proves nothing."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetProcessTimes.argtypes = (w.HANDLE, *[ctypes.POINTER(w.FILETIME)] * 4)
    kernel.GetProcessTimes.restype = w.BOOL
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle: return None
    try:
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]): return None
        return ((times[0].dwHighDateTime << 32) + times[0].dwLowDateTime) / 10_000_000 - 11644473600
    finally:
        kernel.CloseHandle(handle)


def completed_review_processes(task, settings, state, marker):
    """Narrow read-only completion recovery; never infer product completion."""
    if state is not None or not marker or marker.get('name') != 'l1_approve':
        return None
    try:
        from datetime import datetime
        from . import requirements_review as rr
        from .upgrades import journal
        receipt = read_json(task / 'confirmation.json')
        _, manifest = journal(task, receipt)
        binding = rr._binding(settings)
        binding['workflow_manifest'] = manifest
        approval = rr._require_approved_binding(settings, binding)
        value = rr._load(task / 'control')
        number = approval['review_number']
        if number != len(value['reviews']) or value['state'] != 'REQUIREMENTS_APPROVED':
            return None
        review = value['reviews'][number - 1]
        path = task / 'control/requirements-review/outputs' / str(number) / 'process-record.json'
        relative = path.relative_to(task / 'control').as_posix()
        if not any(e['path'] == relative and e['sha256'] == file_hash(path) for e in review['evidence_files']):
            return None
        host = marker.get('process') or read_json(task / 'host-process.json')
        worker = read_json(path)
        dates = [marker['at'], host['started_at'], review['at'], worker['started_at'],
                 worker['ended_at'], approval['at'], host['ended_at']]
        dates = [datetime.fromisoformat(t.replace('Z', '+00:00')) for t in dates]
        if dates != sorted(dates) or host.get('operation') != 'l1_approve':
            return None
        records = [host, worker]
        if any(r.get('status') != 'EXITED' or type(r.get('pid')) is not int or r['pid'] <= 4 for r in records):
            return None
        checks = [{'operation': name, 'record': record, 'status': pid_status(record['pid'])}
                  for name, record in zip(('host', 'completed_requirements_review'), records)]
        for check in checks:
            if check['status'] == 'ACTIVE':
                created = process_creation_time(check['record']['pid'])
                ended = datetime.fromisoformat(check['record']['ended_at'].replace('Z', '+00:00')).timestamp()
                if created is not None and created > ended:
                    check.update(status='STOPPED', pid_reused=True,
                                 reason='OS creation time is after the recorded operation ended', created_at=created)
        if any(c['status'] != 'STOPPED' for c in checks):
            return {'status': 'ACTIVE' if any(c['status'] == 'ACTIVE' for c in checks) else 'UNKNOWN',
                    'checks': checks, 'completed_approval': approval}
        rows = process_inventory(metadata_only=True)
        descendants = {r['pid'] for r in records}
        while True:
            expanded = descendants | {r['pid'] for r in rows if r['ppid'] in descendants}
            if expanded == descendants: break
            descendants = expanded
        reused = {c['record']['pid'] for c in checks if c.get('pid_reused')}
        matches = [{k: r[k] for k in ('pid', 'ppid', 'name')} for r in rows if r['pid'] in descendants and r['pid'] not in reused]
        return {'status': 'ACTIVE' if matches else 'REQUIRES_NATIVE_ATTESTATION', 'checks': checks,
            'completed_approval': approval, 'independent_scan': {'status': 'ACTIVE' if matches else 'NO_RELEVANT_PROCESS',
                'matches': matches, 'method': 'OS parent-PID tree of the hash-bound completed read-only review',
                'inspected_count': len(rows)},
            'limitations': 'No command-line access to unrelated processes is required for this completed read-only review. '
                'A reparented or detached child cannot be excluded by a parent-PID scan. Native user must verify '
                'the cited process and side-effect evidence. This does not resolve product operations or API uncertainty.'}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def independent_process_check(task, settings):
    """Legacy fallback: report scan evidence, not a claim of verified side effects."""
    try:
        rows = process_inventory()
        by_pid = {row['pid']: row for row in rows}
        ancestors, current = set(), os.getpid()
        while current in by_pid and current not in ancestors:
            ancestors.add(current)
            current = by_pid[current]['ppid']
        # Exclude only this read-only helper and its direct ancestors. Other
        # Codex instances remain conservative conflicts even for other tasks.
        ignored = set(ancestors)
        patterns = [str(task).replace('\\', '/').lower(), str(settings['project']).replace('\\', '/').lower(),
                    'harness_workflow.py']
        executor = Path(settings.get('codex', 'codex')).stem.lower()
        def relevant_process(row):
            command = row['command'].replace('\\', '/').lower()
            return (any(pattern and pattern in command for pattern in patterns)
                    or Path(row['name']).stem.lower() == executor
                    or bool(re.search(r'(?:^|[/\s"\'\\])' + re.escape(executor) + r'\.(?:js|exe|cmd)(?:$|[\s"\'])', command)))
        relevant = {row['pid'] for row in rows if row['pid'] not in ignored and relevant_process(row)}
        changed = True
        while changed:
            changed = False
            for row in rows:
                if row['ppid'] in relevant and row['pid'] not in relevant:
                    relevant.add(row['pid']); changed = True
        matches = [{'pid': row['pid'], 'ppid': row['ppid'], 'name': row['name'],
                    'command_sha256': digest(row['command'])} for row in rows if row['pid'] in relevant]
        return {'status': 'ACTIVE' if matches else 'NO_RELEVANT_PROCESS', 'matches': matches,
                'method': 'OS full current-user session process inventory with task/project/executor match and descendant closure',
                'inspected_count': len(rows),
                'limitations': 'This scan cannot prove the absence of a detached child that no longer contains task identifiers. '
                               'The user must independently verify the cited process-tree and side-effect evidence before approval.'}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return {'status': 'UNKNOWN', 'reason': str(exc), 'matches': [],
                'next': 'Obtain a complete OS process inventory and real process-tree/side-effect evidence; keep task paused'}


def process_status(task, settings, state, marker, *, preflight_active=None, research_active=None):
    """Require durable runtime process identity and complete tree reconciliation.

    A missing parent PID is insufficient: a detached child may still be alive.
    Different verified boot identities prove all old local processes stopped.
    For the same boot, runtime-recorded tree termination and a dead PID are
    both required. Model-supplied PIDs/booleans are deliberately ignored.
    """
    from .research import active as research_pending
    investigations = research_active if research_active is not None else research_pending(task / 'control')
    completed = completed_review_processes(task, settings, state, marker) if not investigations else None
    if completed is not None:
        return completed
    current_boot = boot_identity()
    checks = []
    activities = [('host', marker)] if marker else []
    activities += [(key, state[key]) for key in ('active_run', 'analysis_activity') if state and state.get(key)]
    preflight = preflight_active or _preflight_active(task)
    if preflight:
        activities.append(('preflight_requirements_review', preflight))
    activities += [('research', item) for item in investigations]
    for name, activity in activities:
        record = activity.get('process')
        if not record and name == 'host' and (task / 'host-process.json').exists():
            stored = read_json(task / 'host-process.json')
            if stored.get('name', stored.get('operation')) == marker.get('name'):
                record = stored
        if not record and activity.get('output'):
            output = Path(activity['output'])
            if not output.is_absolute() or not output.resolve().is_relative_to((task / 'control').resolve()):
                raise ValueError('Process evidence output escapes control directory')
            path = _path(task, (output / 'process-record.json').relative_to(task).as_posix())
            record = read_json(path) if path.exists() else None
        status, reason = 'UNKNOWN', 'No durable runtime process/tree record; obtain actual process reconciliation evidence'
        if isinstance(record, dict):
            if record.get('boot_id') and current_boot and record['boot_id'] != current_boot:
                status, reason = 'STOPPED', 'OS boot changed; no process from the recorded boot can remain active'
            else:
                observed = pid_status(record.get('pid'))
                if observed == 'ACTIVE':
                    status, reason = 'ACTIVE', 'Recorded PID is alive; PID reuse is conservatively blocked'
                elif observed == 'STOPPED' and record.get('process_tree_status') == 'STOPPED' and record.get('boot_id') and current_boot:
                    status, reason = 'STOPPED', 'Recorded PID stopped and runtime recorded complete process-tree termination'
                else:
                    reason = 'Parent absence does not prove children stopped, or OS process query is unavailable'
        checks.append({'operation': name, 'status': status, 'reason': reason,
                       'record': record, 'current_boot_id': current_boot})
    overall = 'ACTIVE' if any(x['status'] == 'ACTIVE' for x in checks) else (
        'STOPPED' if checks and all(x['status'] == 'STOPPED' for x in checks) else 'UNKNOWN')
    scan = independent_process_check(task, settings) if overall == 'UNKNOWN' else None
    if scan and scan['status'] == 'ACTIVE':
        overall = 'ACTIVE'
    elif scan and scan['status'] == 'NO_RELEVANT_PROCESS':
        overall = 'REQUIRES_NATIVE_ATTESTATION'
    return {'status': overall, 'checks': checks, 'independent_scan': scan,
            'limitations': 'Only recorded local processes are checked. Native confirmation must additionally verify '
                           'workspace/device/network side effects against the cited records; no evidence is invented.'}


def _preflight_active(task):
    from .requirements_review import status
    return status(task / 'control').get('active_run')


def _guard(task, receipt, session, *, finishing=False):
    settings, original = _identity(task, receipt, session)
    run = task / 'control/run'
    state = read_json(run / 'state.json') if (run / 'state.json').exists() else None
    if ((task / 'control/preflight-report.json').exists() or (run / 'final-report.json').exists()
            or (task / 'preflight-review.json').exists() or (state and (state.get('report') or state.get('state') in TERMINAL))):
        raise ValueError('Operation recovery cannot reopen a final report or terminal task')
    if (task / 'runtime-upgrade-pending.json').exists() or (task / 'initialization-recovery-pending.json').exists():
        raise ValueError('Finish the other local transaction before operation recovery')
    from . import api_recovery
    # Local-process reconciliation never resolves an API request. Keeping that
    # independent guard avoids deadlocking host recovery with API review.
    api_recovery.load(task / 'control')
    marker = read_json(task / 'interrupted.json') if (task / 'interrupted.json').exists() else None
    from .research import active as research_pending
    if not finishing and not marker and not (state and (state.get('active_run') or state.get('analysis_activity'))) and not _preflight_active(task) and not research_pending(task / 'control'):
        raise ValueError('No interrupted operation to reconcile')
    if marker and marker.get('status') != 'UNKNOWN':
        raise ValueError('Unsupported host interruption record')
    from .initialization_recovery import incomplete_native_state
    if marker and marker.get('name') == 'dot_initialize' and (not state or incomplete_native_state(task, settings)):
        raise ValueError('Use the dedicated uncommitted initialization recovery')
    return settings, original, state, marker


def _binding(task, settings, state, marker):
    from .onboarding import fingerprint
    from .research import active as research_pending
    return {'state_sha256': file_hash(task / 'control/run/state.json') if state is not None else None,
            'host_interruption': marker, 'active_run': state.get('active_run') if state else None,
            'analysis_activity': state.get('analysis_activity') if state else None,
            'preflight_requirements_review': _preflight_active(task),
            'research_active': research_pending(task / 'control'),
            'project': fingerprint(settings['project']),
            'confirmation_sha256': file_hash(task / 'confirmation.json')}


def _validate_proposal(task, proposal):
    required = {'reason', 'analysis', 'resume_plan', 'side_effects', 'evidence'}
    if not isinstance(proposal, dict) or set(proposal) != required:
        raise ValueError('Recovery proposal requires reason, analysis, resume_plan, side_effects and evidence')
    for key in required - {'evidence'}:
        if not isinstance(proposal[key], str) or not proposal[key].strip() or len(proposal[key]) > 6000:
            raise ValueError('Recovery proposal text must be concrete, nonempty and bounded: ' + key)
    if not isinstance(proposal['evidence'], list) or not 1 <= len(proposal['evidence']) <= 20:
        raise ValueError('Recovery requires 1-20 sourced evidence references')
    for item in proposal['evidence']:
        if (not isinstance(item, dict) or set(item) != {'path', 'sha256', 'description'}
                or not isinstance(item['description'], str) or not item['description'].strip()
                or len(item['description']) > 1000 or not isinstance(item.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
            raise ValueError('Invalid recovery evidence reference')
        name = item['path']
        if not isinstance(name, str) or name.startswith(('operation-recovery', 'control/operation-recovery')):
            raise ValueError('Recovery proposal cannot cite itself as evidence')
        path = _path(task, 'control/' + name)
        if not path.is_file() or file_hash(path) != item['sha256']:
            raise ValueError('Recovery evidence is missing or changed: ' + name)
    return copy.deepcopy(proposal)


def prepare(task, receipt, session, proposal):
    """Stage L1's sourced plan; no clearing, approval, API call or product action.

    Caller must hold the native task lock. Internal control/run locks prevent
    concurrent controller changes while binding the submitted plan.
    """
    with _locks(task):
        if (task / PENDING).exists():
            raise ValueError('OPERATION_RECOVERY_PENDING: finish the approved transaction first')
        settings, _, state, marker = _guard(task, receipt, session)
        material = _validate_proposal(task, proposal)
        binding = _binding(task, settings, state, marker)
        record = {'schema_version': 1, 'at': now(), 'authority': 'delegated_l1', 'session': session,
                  'proposal': material, 'binding': binding}
        record['sha256'] = digest(record)
        archive = task / 'control/operation-recovery-proposals' / (record['sha256'] + '.json')
        archive.parent.mkdir(exist_ok=True)
        atomic_json(archive, record)
        atomic_json(task / PROPOSAL, record)
        return {'status': 'RECOVERY_PROPOSED', 'sha256': record['sha256'],
                'next': 'workflow_recover_operation for read-only checks and native user confirmation',
                'operation_replayed': False, 'interruption_cleared': False}


def _journal(task, receipt):
    value = read_json(task / JOURNAL) if (task / JOURNAL).exists() else {'schema_version': 1, 'records': []}
    if value.get('schema_version') != 1 or not isinstance(value.get('records'), list):
        raise ValueError('Invalid operation recovery journal')
    previous = None
    for sequence, entry in enumerate(value['records'], 1):
        body = {k: v for k, v in entry.items() if k != 'sha256'}
        if (entry.get('sha256') != digest(body) or entry.get('sequence') != sequence
                or entry.get('previous_sha256') != previous or entry.get('session') != receipt['session']
                or entry.get('authority') != 'local_harness_user'
                or entry.get('confirmation_sha256') != file_hash(task / 'confirmation.json')):
            raise ValueError('Operation recovery journal changed')
        previous = entry['sha256']
    return value


def records_for_control(root):
    """Validated recovery audit entries for status, review inputs and final reports."""
    task = Path(root).parent
    if not (task / JOURNAL).exists():
        return []
    _root(task, exists=True)
    _path(task, JOURNAL)
    _path(task, 'confirmation.json')
    return _journal(task, read_json(task / 'confirmation.json'))['records']


def preview(task, receipt, session):
    if (task / PENDING).exists():
        raise ValueError('OPERATION_RECOVERY_PENDING: finish the approved transaction first')
    settings, original, state, marker = _guard(task, receipt, session)
    _journal(task, receipt)
    staged = read_json(task / PROPOSAL)
    if (staged.get('sha256') != digest({k: v for k, v in staged.items() if k != 'sha256'})
            or staged.get('authority') != 'delegated_l1' or staged.get('session') != session):
        raise ValueError('Invalid L1 recovery proposal')
    proposal = _validate_proposal(task, staged['proposal'])
    binding = _binding(task, settings, state, marker)
    if staged['binding'] != binding:
        raise ValueError('Recovery proposal is stale; have L1 assess the changed operation or project')
    processes = process_status(task, settings, state, marker)
    files = _files(task)
    if binding != _binding(task, settings, state, marker) or files != _files(task):
        raise ValueError('Records changed during recovery preview')
    from . import resources, api_recovery
    api = api_recovery.report(task / 'control')
    from .initialization_recovery import effective_baseline, journal as initialization_journal
    initializations = initialization_journal(task, receipt)['records']
    baseline_before = effective_baseline(task, receipt)
    adoption = {'applies_before_initialization': state is None,
                'before': baseline_before, 'after': binding['project'],
                'changed': state is None and baseline_before != binding['project'],
                'initialization_recovery_sha256': initializations[-1]['sha256'] if initializations else None,
                'effect': 'Native approval explicitly adopts the displayed pre-initialization project baseline; '
                          'independent requirements review and new planning remain required. Original confirmation is retained.'}
    value = {'project': settings['project'], 'binding': binding, 'proposal': proposal,
             'proposal_sha256': staged['sha256'], 'processes': processes,
             'ready': processes['status'] in ('STOPPED', 'REQUIRES_NATIVE_ATTESTATION'), 'records_sha256': digest(files),
             'usage': resources.report(task / 'control', original, state), 'api_review': api,
             'baseline_adoption': adoption,
             'action': 'Archive original interruption and state, preserve evidence/usage/completed work, '
                       'clear only these reconciled guards and invalidate approvals. Continue with L2 investigation '
                       'and L1 independent baseline review; never replay or dispatch the interrupted operation.'}
    from .controller import ROOT
    value['maintenance_runtime'] = {'version': (ROOT / 'VERSION').read_text().strip(),
                                    'manifest_sha256': file_hash(ROOT / 'MANIFEST.json')}
    if processes.get('completed_approval') and not adoption['changed']:
        value['action'] = ('Archive only reconciled host interruption records. Preserve the completed independent '
                           'requirements approval and all consumption. Continue with planning; do not rerun the review. '
                           'The task runtime remains pinned; upgrading still requires its separate native approval.')
    # Wall-clock usage and irrelevant process-count churn are display context,
    # not approval changes. All persisted counters/records remain hash-bound.
    material = {k: v for k, v in value.items() if k != 'usage'}
    material['processes'] = copy.deepcopy(processes)
    if material['processes'].get('independent_scan'):
        material['processes']['independent_scan'].pop('inspected_count', None)
    value['sha256'] = digest(material)
    return value


def _locks(task):
    stack = ExitStack()
    try:
        for directory in (task / 'control', task / 'control/run'):
            if directory.exists():
                _root(directory, exists=True)
                stack.enter_context(run_lock(directory))
        return stack
    except BaseException:
        stack.close()
        raise


def _finish(task, receipt, session):
    intent = read_json(task / PENDING)
    if intent.get('sha256') != digest({k: v for k, v in intent.items() if k != 'sha256'}):
        raise ValueError('Operation recovery intent damaged; no automatic continuation')
    settings, _, _, _ = _guard(task, receipt, session, finishing=True)
    entry = intent['entry']
    from .onboarding import fingerprint
    if (entry.get('session') != session or entry.get('confirmation_sha256') != file_hash(task / 'confirmation.json')
            or entry['binding']['project'] != fingerprint(settings['project'])):
        raise ValueError('Approved recovery snapshot changed')
    original_state = intent['original_state']
    original_marker = copy.deepcopy(entry['binding']['host_interruption'])
    if original_marker and not original_marker.get('process'):
        host_check = next((item for item in entry['processes'].get('checks', []) if item.get('operation') == 'host'), None)
        if host_check and host_check.get('record'):
            original_marker['process'] = host_check['record']
    observed = process_status(task, settings, original_state, original_marker,
                              preflight_active=entry['binding'].get('preflight_requirements_review'),
                              research_active=entry['binding'].get('research_active'))
    if observed['status'] not in ('STOPPED', 'REQUIRES_NATIVE_ATTESTATION'):
        raise ValueError('Old process state is active or unknown; keep recovery paused')
    actual, expected = _files(task), dict(intent['before_files'])
    for source, target in intent['moves']:
        old, new = _path(task, source), _path(task, target)
        rewritten = source in intent['writes'] and actual.get(source) == _encoded_hash(intent['writes'][source])
        if (not old.exists() and not new.exists()) or (old.exists() and new.exists() and not rewritten):
            raise ValueError('Operation recovery archive locations are inconsistent')
        if new.exists():
            expected[target] = expected.pop(source)
    for name, value in intent['writes'].items():
        _path(task, name)
        if actual.get(name) == _encoded_hash(value):
            expected[name] = actual[name]
    if actual != expected:
        raise ValueError('Operation recovery records changed; no files overwritten')
    for source, target in intent['moves']:
        old, new = _path(task, source), _path(task, target)
        if not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            _filesystem_path(old).replace(_filesystem_path(new))
    for name, value in intent['writes'].items():
        path = _path(task, name)
        if not path.exists() or file_hash(path) != _encoded_hash(value):
            atomic_json(path, value)
    _journal(task, receipt)
    (task / PENDING).unlink()
    pending_api = entry.get('api_review', {}).get('unresolved') or entry.get('api_review', {}).get('pending')
    return {'status': 'OPERATION_RECONCILED', 'record': entry, 'operation_replayed': False,
            'product_execution_started': False,
            'next': ('l1_review_api and, if required, workflow_review_api_retry; API uncertainty and product execution guard retained'
                     if pending_api else 'dot_plan with exactly the approved requirements; completed review and usage retained'
                     if entry['processes'].get('completed_approval') and not entry['baseline_adoption']['changed']
                     else 'L2 read-only investigation, then L1 independent baseline review before product execution')}


def dispatch(task, receipt, request):
    """Private native pipe only. Caller holds task lock; never expose commit to L1."""
    with _locks(task):
        action, session = request['action'], request['session']
        if action == 'operation_preview' and (task / PENDING).exists():
            intent = read_json(task / PENDING)
            if intent.get('sha256') != digest({k: v for k, v in intent.items() if k != 'sha256'}):
                raise ValueError('Operation recovery intent damaged; no automatic continuation')
            _guard(task, receipt, session, finishing=True)
            if intent['entry'].get('session') != session:
                raise ValueError('Approved operation recovery belongs to another native session')
            return {'status': 'OPERATION_RECOVERY_PENDING', 'finish_required': True,
                    'approved_preview_sha256': intent['entry']['approved_preview_sha256'],
                    'authority': intent['entry']['authority'], 'product_execution_started': False}
        if action == 'operation_finish':
            if not (task / PENDING).exists():
                return {'status': 'NO_PENDING_OPERATION_RECOVERY'}
            return _finish(task, receipt, session)
        plan = preview(task, receipt, session)
        if action == 'operation_preview':
            return plan
        if action != 'operation_commit' or request.get('sha256') != plan['sha256']:
            raise ValueError('Recovery snapshot changed during native confirmation; review again')
        if not plan['ready']:
            raise ValueError('Old process state is active or unknown; native approval cannot override it')
        _, _, state, _ = _guard(task, receipt, session)
        log = _journal(task, receipt)
        entry = {k: v for k, v in plan.items() if k != 'sha256'}
        entry.update(sequence=len(log['records']) + 1, at=now(), session=session,
                     authority='local_harness_user', confirmation_sha256=file_hash(task / 'confirmation.json'),
                     approved_preview_sha256=plan['sha256'],
                     previous_sha256=log['records'][-1]['sha256'] if log['records'] else None)
        entry['sha256'] = digest(entry)
        archive = 'operation-recovery-archives/' + entry['sha256']
        move_names = ['interrupted.json', 'host-process.json', 'control/run/state.json', 'control/run/decision-request.json']
        if not state:
            move_names.append('control/planning-status.json')
        moves = [[name, archive + '/' + name] for name in move_names
                 if (task / name).exists()]
        history = {'schema_version': 1, 'records': [*log['records'], entry]}
        writes = {JOURNAL: history, 'control/operation-recovery-history.json': history}
        decision = {'owner': 'L2', 'escalation_owner': 'L1', 'investigation_status': 'OPEN',
                    'reason': 'Native-reviewed operation reconciliation completed; investigate and independently review before execution',
                    'recovery_sha256': entry['sha256'], 'proposal': plan['proposal'], 'next': 'dot_investigate then L1 review'}
        if plan['api_review']['unresolved'] or plan['api_review']['pending']:
            decision['next'] = 'l1_review_api and, if required, workflow_review_api_retry; API uncertainty is retained and product execution stays blocked'
            decision['unresolved_api_retained'] = True
        if state:
            updated = copy.deepcopy(state)
            updated.update(active_run=None, analysis_activity=None, approvals={}, state='DECISION_PENDING')
            updated['operation_recovery'] = {'sha256': entry['sha256'], 'requires_independent_review': True}
            updated.setdefault('history', []).append({'at': entry['at'], 'action': 'native_operation_reconciled',
                                                      'recovery_sha256': entry['sha256'], 'previous_state': state['state']})
            writes['control/run/state.json'] = updated
            writes['control/run/decision-request.json'] = decision
        else:
            writes['control/planning-status.json'] = {'status': 'NEEDS_INFO', 'reason': decision['reason'],
                                                     'gaps': [], 'recovery_sha256': entry['sha256']}
            if plan['baseline_adoption']['changed']:
                writes['control/planning-status.json']['reason'] = 'User adopted the displayed changed baseline during operation recovery; stage requirements, independently review, and replan before initialization'
            preflight = plan['binding']['preflight_requirements_review']
            if preflight:
                from .requirements_review import prepare_reconciliation
                path, value = prepare_reconciliation(task / 'control', preflight,
                    {'authority': 'local_harness_user', 'session': session, 'all_processes_stopped': True,
                     'verification_method': plan['processes']['status'],
                     'approved_preview_sha256': plan['sha256'],
                     'evidence': [item['path'] for item in plan['proposal']['evidence']]})
                source = 'control/' + path
                moves.append([source, archive + '/' + source])
                writes[source] = value
        intent = {'entry': entry, 'original_state': state, 'before_files': _files(task), 'moves': moves, 'writes': writes}
        intent['sha256'] = digest(intent)
        atomic_json(task / PENDING, intent)
        return _finish(task, receipt, session)
