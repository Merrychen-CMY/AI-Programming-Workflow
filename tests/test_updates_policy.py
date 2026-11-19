import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from validate_repo import validate_update_policy, validate_task_upgrade_policy, validate_initialization_recovery_policy


class UpdatePolicyTests(unittest.TestCase):
    def test_initialization_recovery_is_not_a_general_unknown_override(self):
        value = json.loads((ROOT / 'policy/initialization-recovery.json').read_text())
        self.assertEqual(validate_initialization_recovery_policy(value), [])
        for key, v in value.items():
            if type(v) is bool:
                changed = copy.deepcopy(value); changed[key] = not v
                self.assertTrue(validate_initialization_recovery_policy(changed))

    def test_task_upgrade_does_not_expand_authority_or_replay_work(self):
        policy = json.loads((ROOT / 'policy/task-upgrades.json').read_text())
        self.assertEqual(validate_task_upgrade_policy(policy), [])
        for key, value in policy.items():
            if type(value) is bool:
                changed = copy.deepcopy(policy); changed[key] = not value
                self.assertTrue(validate_task_upgrade_policy(changed), key)
        changed = copy.deepcopy(policy); changed['supported_source_versions'].append('3.3.0')
        self.assertTrue(validate_task_upgrade_policy(changed))

    def test_update_authority_cannot_expand_or_erase_history(self):
        policy = json.loads((ROOT / 'policy/updates.json').read_text())
        self.assertEqual(validate_update_policy(policy), [])
        for key, value in {'repository': 'another/repository', 'task_history_mutation': True,
                           'confirmed_tasks_pin_manifest': False, 'automatic_task_migration': True,
                           'unknown_clear_or_replay': True, 'paid_api_calls': True,
                           'network_sends_project_or_gpt_credentials': True,
                           'automatic_host_plugin_replacement': True}.items():
            with self.subTest(key=key):
                changed = copy.deepcopy(policy)
                changed[key] = value
                self.assertTrue(validate_update_policy(changed))


if __name__ == '__main__':
    unittest.main()
