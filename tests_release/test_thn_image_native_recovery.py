"""Observed native recovery representations, with synthetic provider values only."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tools import thn_image_recovery as recovery
from tools import thn_test_release as release
from test_thn_image_recovery import RecoveryServices, fixture_seal
from test_thn_test_release import ACCOUNT, FUNCTION


class NativeRecoveryServices(RecoveryServices):
    def __init__(self):
        super().__init__()
        self.native_initial = False
        self.native_change = True
        self.native_final = True
        self.template_drift = None

    def get_template(self, **kwargs):
        selected = dict(kwargs)
        if kwargs['TemplateStage'] == 'Original' and (
                (kwargs.get('ChangeSetName') and self.native_change)
                or (self.executed and self.native_final)
                or (not kwargs.get('ChangeSetName') and not self.executed and self.native_initial)):
            selected['TemplateStage'] = 'Processed'
        result = super().get_template(**selected)
        if self.template_drift:
            self.template_drift(kwargs, result['TemplateBody'])
        return result

    def describe_change_set(self, **kwargs):
        result = super().describe_change_set(**kwargs)
        for change in result['Changes']:
            item = change['ResourceChange']
            if item['LogicalResourceId'] == FUNCTION + 'Versiona1b2c3d4e5':
                item.update(Action='Modify', Replacement='True')
        return result


class NativeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.session = NativeRecoveryServices()
        self.seal = fixture_seal(self.session)
        self.session.calls.clear()
        self.env = {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
                    'GITHUB_SHA': 'a' * 40, 'THN_RECOVERY_EXECUTION': 'execute'}

    def run_recovery(self):
        with patch.object(recovery.time, 'sleep'):
            return recovery.run(self.session, self.env, ACCOUNT, self.seal)

    def test_observed_native_change_and_persisted_template_complete_recovery(self):
        result = self.run_recovery()
        self.assertEqual(result['decision'], 'executed')
        self.assertEqual(result['finalResources'], 7)
        self.assertFalse(result['blogActive'])
        self.assertTrue(dict(self.session.calls)['execute']['DisableRollback'])
        self.assertFalse(any(n.startswith('delete') or n == 'put_object' for n, _ in self.session.calls))

    def test_observed_native_change_verify_does_not_execute(self):
        self.env['THN_RECOVERY_EXECUTION'] = 'verify'
        self.assertEqual(self.run_recovery()['decision'], 'verified')
        self.assertFalse(self.session.executed)

    def test_final_original_may_remain_the_exact_sam_source(self):
        self.session.native_final = False
        self.assertEqual(self.run_recovery()['decision'], 'executed')

    def test_initial_original_cannot_use_the_final_representation_exception(self):
        self.session.native_initial = True
        with self.assertRaisesRegex(release.ReleaseBlocked, 'recovery_template_changed'):
            self.run_recovery()
        self.assertNotIn('create_change_set', dict(self.session.calls))

    def test_change_templates_must_match_sealed_documents_exactly(self):
        for stage in ('Original', 'Processed'):
            with self.subTest(stage=stage):
                self.session = NativeRecoveryServices()
                def drift(request, template):
                    if request.get('ChangeSetName') and request['TemplateStage'] == stage:
                        template['Description'] = 'unreviewed'
                self.session.template_drift = drift
                with self.assertRaisesRegex(release.ReleaseBlocked, 'recovery_change_set_mismatch'):
                    self.run_recovery()
                self.assertFalse(self.session.executed)

    def test_different_final_original_is_not_accepted(self):
        def drift(request, template):
            if self.session.executed and request['TemplateStage'] == 'Original':
                template['Description'] = 'unreviewed'
        self.session.template_drift = drift
        with self.assertRaisesRegex(release.ReleaseBlocked, 'recovery_template_changed'):
            self.run_recovery()
        self.assertTrue(self.session.executed)

    def test_native_classification_exception_never_replaces_an_existing_resource(self):
        variants = [
            ('version', {'PhysicalResourceId': 'existing-version'}),
            ('version', {'Action': 'Add'}),
            ('version', {'Replacement': 'Conditional'}),
            ('version', {'LogicalResourceId': FUNCTION, 'ResourceType': 'AWS::Lambda::Function'}),
            ('alias', {'Replacement': 'True'}),
            ('alias', {'Action': 'Modify'}),
        ]
        for target, mutation in variants:
            with self.subTest(target=target, mutation=mutation):
                self.session = NativeRecoveryServices()
                describe = self.session.describe_change_set
                def changed(**kwargs):
                    result = describe(**kwargs)
                    kind = 'AWS::Lambda::Version' if target == 'version' else 'AWS::Lambda::Alias'
                    item = next(v['ResourceChange'] for v in result['Changes'] if v['ResourceChange']['ResourceType'] == kind)
                    item.update(deepcopy(mutation))
                    return result
                self.session.describe_change_set = changed
                with self.assertRaisesRegex(release.ReleaseBlocked, 'recovery_destructive_or_unrelated_change'):
                    self.run_recovery()
                self.assertFalse(self.session.executed)


if __name__ == '__main__':
    unittest.main()
