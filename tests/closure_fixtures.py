"""Synthetic explicit L1 assessment for tests that intentionally create a final report."""
import json

def assessment(summary='Fixture L1 assessed no feasible remaining solution', evidence=None, *, control=False):
    return {'summary': summary, 'solutions_considered': ['Fixture alternative was checked and cannot meet the fixed AC'],
            'authority_review': 'Fixture authority cannot resolve this requirement',
            'resource_review': 'Additional fixture attempts provide no benefit; existing usage retained',
            'termination_basis': 'no_feasible_solution', 'evidence': evidence or ['run/state.json' if control else 'state.json']}

def reason(summary='Fixture L1 assessed no feasible remaining solution', evidence=None, *, control=False):
    return json.dumps(assessment(summary, evidence, control=control))
