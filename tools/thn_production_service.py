"""Planned production names. They are not assertions of deployed/approved IAM.

Auth/Hub deploy roles match read-only GitHub production variables captured
2026-09-27. API/Image roles, CFN execution roles and private package bucket
require separately reviewed bootstrap and effective permission proof.
"""
from types import MappingProxyType
CONFIG=MappingProxyType({'service': 'image', 'repository': 'zoolanding-image-upload', 'stack': 'zoolanding-image-upload', 'deployRole': 'zoolanding-deployer-image-upload-production-github-deploy', 'executionRole': 'zoolanding-deployer-image-upload-production-cfn-exec', 'sourceTemplate': 'template.yaml', 'bucket': 'zlp-thn-production-releases-765932874577-us-east-1'})
