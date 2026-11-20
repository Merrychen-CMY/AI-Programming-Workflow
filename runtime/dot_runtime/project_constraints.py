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


def prompt():
    catalog = json.loads((DIRECTORY / 'adoption.json').read_text(encoding='utf-8'))
    return ('\nPORTABLE C PROJECT CONSTRAINTS (apply only to applicable C/H and tasks):\n'
            'Huawei takes precedence. Apply PC-U01 through PC-U26 by scope, including Allman braces, '
            'precomputed if results with preserved short-circuit, definition/header documentation and semantic bool. '
            'Function Doxygen is not enough: explain meaningful internal stages, branches, failure exits and state commits in concise natural Chinese. '
            'Use action/object function names and meaningful variable names, with units where needed; preserve existing SDK/ABI and task scope. '
            'Feature C files need a truthful file-header call-flow overview, including asynchronous triggers where applicable. '
            'Inspect actual candidate paths and comments, not comment counts, AI-style scores or Doxygen PASS alone. '
            'Legacy B330 filenames, line numbers and phase restrictions are not new-project limits. '
            'This does not expand approved paths or authorize cleanup/refactoring. '
            'Independent review must report applicable PC-U IDs, candidate evidence, exclusions and unverified items. '
            'Doxygen or a generic PASS does not prove these semantic requirements.\n'
            + json.dumps(catalog, ensure_ascii=False, sort_keys=True))
