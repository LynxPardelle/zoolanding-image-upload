import copy
from pathlib import Path
import unittest
import yaml
from tools.prepare_thn_production_template import prepare_template
ROOT=Path(__file__).resolve().parents[1]
class ProductionTemplateTests(unittest.TestCase):
    def source(self): return yaml.safe_load((ROOT/'template.yaml').read_text())
    def test_public_uploader_resources_and_parameters_are_unchanged(self):
        source=self.source();result=prepare_template(source)
        for logical,value in source['Resources'].items():
            if not logical.startswith('Thn'): self.assertEqual(result['Resources'][logical],value,logical)
        for name,value in source['Parameters'].items():
            if not name.startswith(('Thn','ProvisionThn','EnableThn')): self.assertEqual(result['Parameters'][name],value,name)
    def test_private_resources_remain_retained_closed_and_isolated(self):
        result=prepare_template(self.source());body=yaml.safe_dump(result)
        for marker in ('zoolanding-image-upload-test','zoolanding-content-hub-test','#test#','private/test/'):
            self.assertNotIn(marker,body)
        for name in ('ProvisionThnPrivateUploadV2State','EnableThnPrivateUploadV2'): self.assertEqual(result['Parameters'][name]['Default'],'false')
        props=result['Resources']['ThnPrivateImageUploadV2Function']['Properties']
        self.assertEqual(props['AutoPublishAlias'],'production');self.assertEqual(props['Environment']['Variables']['THN_DEPLOYMENT_ENVIRONMENT'],'production');self.assertEqual(props['FunctionName'],'zoolanding-image-upload-production-ThnImageUploadV2')
        for logical in ('ThnPrivateUploadTransactionsV2Table','ThnPrivateUploadV2Store'):
            self.assertEqual(result['Resources'][logical]['DeletionPolicy'],'Retain');self.assertEqual(result['Resources'][logical]['UpdateReplacePolicy'],'Retain')
    def test_unknown_identity_cannot_enter_private_projection(self):
        source=self.source();source['Resources']['ThnPrivateImageUploadV2Function']['Properties']['FunctionName']='zoolanding-unknown-test-function'
        with self.assertRaises(ValueError): prepare_template(source)

    def test_native_sam_translation_closed_and_active(self):
        import os
        from unittest.mock import patch
        from samtranslator.translator.transform import transform
        for enabled in ('false','true'):
            candidate=prepare_template(self.source())
            for item in candidate['Resources'].values():
                if item['Type']=='AWS::Serverless::Function':
                    item['Properties']['CodeUri']={'Bucket':'synthetic-package','Key':'reviewed.zip','Version':'synthetic-version'}
            parameters={name:value.get('Default') for name,value in candidate['Parameters'].items() if 'Default' in value}
            parameters.update(ThnProductionDependencyGate='CONFIRMED_PRODUCTION_BINDINGS')
            for name in parameters:
                if name.startswith('ProvisionThn'): parameters[name]='true'
                elif name.startswith('EnableThn'): parameters[name]=enabled
                elif 'TerminationProtectionGate' in name: parameters[name]='CONFIRMED_ENABLED'
                elif 'DescriptorVersionId' in name or 'AuthPolicyVersion' in name: parameters[name]='synthetic-production-v1'
                elif 'Sha256' in name: parameters[name]='a'*64
            if 'EnvironmentName' in parameters:
                parameters.update(EnvironmentName='prod',AuthSessionTableName='synthetic-v1-session',AuthUserStateTableName='synthetic-v1-state')
            with patch.dict(os.environ,{'AWS_DEFAULT_REGION':'us-east-1'}):
                native=transform(candidate,parameters,{'AWSLambdaBasicExecutionRole':'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'})
            self.assertNotIn('Transform',native)
            self.assertTrue(all(not item['Type'].startswith('AWS::Serverless::') for item in native['Resources'].values()))

            def refs(value):
                if isinstance(value,dict):
                    if 'Ref' in value and isinstance(value['Ref'],str): yield value['Ref']
                    if 'Fn::GetAtt' in value:
                        target=value['Fn::GetAtt']
                        yield target[0] if isinstance(target,list) else target.split('.')[0]
                    if 'Fn::Sub' in value:
                        import re
                        sub=value['Fn::Sub'];expr=sub if isinstance(sub,str) else sub[0]
                        local=set() if isinstance(sub,str) else set(sub[1])
                        for token in re.findall(r'\$\{([^}!]+)\}',expr):
                            head=token.split('.')[0]
                            if head not in local: yield head
                    for child in value.values(): yield from refs(child)
                elif isinstance(value,list):
                    for child in value: yield from refs(child)
            known=set(native['Resources'])|set(native.get('Parameters',{}))
            dangling={ref for ref in refs(native['Resources']) if not ref.startswith('AWS::') and ref not in known}
            self.assertEqual(dangling,set())
