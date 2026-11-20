"""Portable C policy sent to workers and pinned with their approved baseline."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'standards/embedded-c/v1.3/project-constraints'
POLICY = ROOT / 'policy/embedded-c.json'


def baseline_digest():
    # Include readable guidance, provenance and machine rules. Missing files fail closed.
    paths = [POLICY, *(DIRECTORY / name for name in ('adoption.json', 'source.json', 'README.md', 'readable-style.md'))]
    values = {str(i): hashlib.sha256(path.read_bytes()).hexdigest() for i, path in enumerate(paths)}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def status(state=None):
    """Read-only view of the pinned rules and candidate-bound review records."""
    state = state or {}
    catalog = json.loads((DIRECTORY / 'adoption.json').read_text(encoding='utf-8'))
    digest = baseline_digest()
    candidate = state.get('candidate') or {}
    matched = bool(candidate and candidate.get('baseline', {}).get('project_constraints') == digest)
    reviews = [entry for entry in state.get('sessions', []) if entry.get('role') == 'review'
               and candidate.get('id') and entry.get('candidate_id') == candidate['id']]
    return {'workflow_version': (ROOT / 'VERSION').read_text(encoding='utf-8').strip(),
            'project_constraints_revision': catalog['revision'], 'rules_digest': digest,
            'coding_standard': (state.get('approved_plan') or {}).get('coding_standard'),
            'approved_rules_match': (state.get('approved_baseline') or {}).get('project_constraints') == digest,
            'candidate_id': candidate.get('id'), 'candidate_rules_match': matched,
            'independent_review_status': ('STALE' if candidate and not matched else
                reviews[-1]['result'].get('status', 'NOT_RUN') if reviews else 'NOT_RUN'),
            'evidence_scope': 'Recorded review for this candidate and pinned rules; not a new verification or final acceptance.'}


def prompt(*, compact=False, root=None):
    catalog = json.loads((DIRECTORY / 'adoption.json').read_text(encoding='utf-8'))
    if compact and root is not None:
        from .input_material import material_packet
        catalog = material_packet(root, catalog, 2400)
    return ('\nPORTABLE C PROJECT CONSTRAINTS (apply only to applicable C/H and tasks):\n'
            'Huawei takes precedence. Apply PC-U01 through PC-U26 by scope, including Allman braces, '
            'precomputed if results with preserved short-circuit, definition/header documentation and semantic bool. '
            'PC-M01: Do not define macros with #define in owned .c files. Place them in the corresponding .h: '
            'public macros in the module public header, implementation-only macros in a private header not exposed through public includes. '
            'Preserve values, suffixes, expansion semantics, conditional branches and definition/include order; verify affected translation units. '
            'Review actual candidate .c/.h paths and route findings to L2 before L3 repairs. '
            'PC-M02: Keep main/entry files focused on startup and top-level flow; put complete drivers, protocols and algorithms in cohesive modules. '
            'L2 records responsibilities, paths, public interfaces and private state in the existing plan; L4 reviews actual ownership and call/include boundaries. '
            'Do not split by function counts, add empty wrappers or expand scope for advisory refactors. '
            'Modules do not require one RTOS task or build component each. Drivers own hardware, not business decisions; expose contracts, not private state. '
            'Apply existing ARCH-001/TIME-001/LOG-001/HIL-001: bound blocking, queues and critical sections; no unbounded busy waits or unverified long interrupt masking. '
            'Reuse logging facilities; a dedicated logging task is conditional, not mandatory. Preserve filtering, drops and emergency diagnostics. '
            'PC-U22: update relevant documentation, risk-based tests and delivery records; host tests exercise actual C, not a rewritten algorithm. '
            'Comment-only changes do not require new tests by default. Keep first failures; test corrections need L2 analysis and L1/L1-V review. '
            'Protect ongoing acquisition and exclusive device access. Execute already-authorized recovery with evidence; otherwise L2 proposes and L1 assesses within authority. '
            'For public functions, keep @param and @return/@retval only at the header declaration; retain Doxygen implementation @details/@par and @see header in .c. '
            'Do not duplicate machine tags or self-copydoc the same function. Internal functions document their contracts at the definition. '
            'Tool/rule conflicts go to L2 investigation and L1 assessment, not blind product retries. '
            'Function Doxygen is not enough: explain meaningful internal stages, branches, failure exits and state commits in concise natural Chinese. '
            'Put in-function flow comments on the right of their code and align comment starts within each function using spaces, including nested branches. '
            'Do not move a short stage summary above code; only documented Huawei/readability exceptions may use another placement. '
            'Use action/object function names and meaningful variable names, with units where needed; preserve existing SDK/ABI and task scope. '
            'Feature C files need a truthful file-header call-flow overview, including asynchronous triggers where applicable. '
            'Inspect actual candidate paths and comments, not comment counts, AI-style scores or Doxygen PASS alone. '
            'Legacy B330 filenames, line numbers and phase restrictions are not new-project limits. '
            'This does not expand approved paths or authorize cleanup/refactoring. '
            'Independent review must report applicable PC-U IDs, candidate evidence, exclusions and unverified items. '
            'Doxygen or a generic PASS does not prove these semantic requirements.\n'
            + ('Read the referenced applicable clauses before deciding a standards issue. Full independent compliance coverage remains required.\n' if compact else '')
            + json.dumps(catalog, ensure_ascii=False, sort_keys=True))
