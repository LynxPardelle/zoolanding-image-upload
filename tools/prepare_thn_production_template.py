"""Offline production-only private-image extension, preserving public v1."""
from copy import deepcopy
import json
import sys
from pathlib import Path

_ANCHORS=(
 ('zoolanding-image-upload-test','zoolanding-image-upload-production'),
 ('zoolanding-content-hub-test','zoolanding-content-hub-prod'),
 ('zlp-thn-private-upload-test-','zlp-thn-private-upload-production-'),
 ('zlp-thn-ch-test-','zlp-thn-ch-production-'),
 ('thn-journal-test-v2','thn-journal-production-v2'),
 ('#test#','#production#'),('private/test/','private/production/'),(':test',':production'),('Aliastest','Aliasproduction'),
)

def _project(value):
    if isinstance(value,dict): return {key:_project(item) for key,item in value.items()}
    if isinstance(value,list): return [_project(item) for item in value]
    if not isinstance(value,str): return value
    if value=='test': return 'production'
    for old,new in _ANCHORS: value=value.replace(old,new)
    if '-test-' in value or '#test#' in value or 'private/test/' in value: raise ValueError('production_source_identity_unrecognized')
    return value

def prepare_template(source):
    if not isinstance(source,dict) or source.get('Transform')!='AWS::Serverless-2016-10-31' or not {'ImageUploadFunction','ThnPrivateImageUploadV2Function','ThnPrivateUploadV2Store'}.issubset(source.get('Resources',{})):
        raise ValueError('production_source_resources_invalid')
    result=deepcopy(source)
    result['Parameters']['ThnProductionDependencyGate']={'Type':'String','Default':'BLOCKED','AllowedValues':['BLOCKED','CONFIRMED_PRODUCTION_BINDINGS']}
    result['Rules']['ThnPrivateUploadV2ActivationRule']['Assertions'].append({'Assert':{'Fn::Equals':[{'Ref':'ThnProductionDependencyGate'},'CONFIRMED_PRODUCTION_BINDINGS']},'AssertDescription':'Verify production authoring, registry and immutable descriptor before processor activation.'})
    for logical,value in list(result['Resources'].items()):
        if logical.startswith('Thn'): result['Resources'][logical]=_project(value)
    props=result['Resources']['ThnPrivateImageUploadV2Function']['Properties'];props['Environment']['Variables']['THN_DEPLOYMENT_ENVIRONMENT']='production'
    result['Metadata']=_project(result.get('Metadata',{}));result['Metadata']['ThnProductionLifecycle']={'Profile':'production','AutomaticActivation':False,'PublicV1Resources':'preserved unchanged'}
    return result

def main(argv=None):
    import yaml
    args=sys.argv[1:] if argv is None else argv
    if len(args)!=2: raise ValueError('production_template_arguments_invalid')
    Path(args[1]).write_text(json.dumps(prepare_template(yaml.safe_load(Path(args[0]).read_text(encoding='utf-8'))),sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return 0
if __name__=='__main__': raise SystemExit(main())
