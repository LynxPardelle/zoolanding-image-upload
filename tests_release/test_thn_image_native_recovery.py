"""Observed native recovery representations, with synthetic provider values only."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import thn_image_recovery as recovery
from tools import thn_test_release as release
from test_thn_image_recovery import RecoveryServices, fixture_seal
from test_thn_test_release import ACCOUNT, ACCOUNT_HASH, FUNCTION, selection


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

    def test_enable_reuses_only_the_exact_sealed_native_template(self):
        native = self.session.get_template(TemplateStage='Processed')['TemplateBody']
        native.pop('Globals', None)
        with patch.dict(recovery.APPROVED_BASELINE, {'processedSha256': recovery.digest(native)}):
            result = release.recovered_native_enable_template(native, native, 'false')
            self.assertEqual(result, native)
            self.assertIsNot(result, native)
            for original, processed, enabled in (
                    ({**native, 'Description': 'unreviewed'}, native, 'false'),
                    (native, {**native, 'Description': 'unreviewed'}, 'false'),
                    (native, native, 'true'),
                    ({**native, 'Transform': 'AWS::Serverless-2016-10-31'}, native, 'false')):
                with self.subTest(enabled=enabled, original=original.get('Description')):
                    with self.assertRaises(release.ReleaseBlocked):
                        release.recovered_native_enable_template(original, processed, enabled)

    def test_recovered_native_enable_uses_existing_template_without_s3_upload(self):
        self.session.executed = True
        original_get_template = self.session.get_template

        def native_get_template(**kwargs):
            result = original_get_template(**kwargs)
            result['TemplateBody'].pop('Globals', None)
            return result

        self.session.get_template = native_get_template
        native = native_get_template(TemplateStage='Original')['TemplateBody']
        env = {**self.env, 'ARTIFACTS_BUCKET': 'example-artifacts',
               'GITHUB_REPOSITORY': 'LynxPardelle/zoolanding-image-upload',
               'GITHUB_REF': 'refs/heads/test', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
               'EXPECTED_SOURCE_SHA': self.env['GITHUB_SHA'], 'AWS_REGION': 'us-east-1',
               'AWS_DEFAULT_REGION': 'us-east-1', 'THN_V2_TEST_PARAMETERS_JSON': selection()}
        self.session.get_caller_identity = lambda: {'Account': ACCOUNT,
            'Arn': f'arn:aws:sts::{ACCOUNT}:assumed-role/zoolanding-deployer-image-upload-test-github-deploy/synthetic'}
        requests = []

        class StopAfterTransport(Exception):
            pass

        def capture_change_set(**kwargs):
            requests.append(kwargs)
            raise StopAfterTransport

        self.session.create_change_set = capture_change_set
        self.session.calls.clear()
        with patch.object(release, 'ACCOUNT_HASH', ACCOUNT_HASH), \
                patch.dict(recovery.APPROVED_BASELINE, {'processedSha256': recovery.digest(native)}), \
                patch.object(release, 'verify_dependencies', return_value='reviewed-dependencies'), \
                patch.object(release, '_inventory', return_value={}), \
                patch.object(release, '_verify_retained_state'), \
                patch.object(release, 'verify_runtime'):
            with self.assertRaises(StopAfterTransport):
                release.run_release(self.session, env, Path('unused'), 'enable')
        self.assertEqual(len(requests), 1)
        self.assertIs(requests[0].get('UsePreviousTemplate'), True)
        self.assertNotIn('TemplateURL', requests[0])
        self.assertFalse(any(name == 'put_object' for name, _ in self.session.calls))

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
