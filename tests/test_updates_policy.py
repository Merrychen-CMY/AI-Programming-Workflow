import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from validate_repo import validate_update_policy


class UpdatePolicyTests(unittest.TestCase):
    def test_update_authority_cannot_expand_or_erase_history(self):
        policy = json.loads((ROOT / 'policy/updates.json').read_text())
        self.assertEqual(validate_update_policy(policy), [])
        for key, value in {'repository': 'another/repository', 'task_history_mutation': True,
                           'confirmed_tasks_pin_manifest': False, 'automatic_task_migration': True,
                           'unknown_clear_or_replay': True, 'paid_api_calls': True,
                           'network_sends_project_or_credentials': True,
                           'automatic_host_plugin_replacement': True}.items():
            with self.subTest(key=key):
                changed = copy.deepcopy(policy)
                changed[key] = value
                self.assertTrue(validate_update_policy(changed))


if __name__ == '__main__':
    unittest.main()
