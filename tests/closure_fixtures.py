"""Synthetic explicit L1 assessment for tests that intentionally create a final report."""
import json

def assessment(summary='Fixture L1 assessed no feasible remaining solution'):
    return {'summary': summary, 'solutions_considered': ['Fixture alternative was checked and cannot meet the fixed AC'],
            'authority_review': 'Fixture authority cannot resolve this requirement',
            'resource_review': 'Additional fixture attempts provide no benefit; existing usage retained',
            'termination_basis': 'no_feasible_solution', 'evidence': ['fixture: isolated test evidence']}

def reason(summary='Fixture L1 assessed no feasible remaining solution'):
    return json.dumps(assessment(summary))
