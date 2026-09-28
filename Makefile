build-ImageUploadFunction:
	python tools/build_lambda_artifact.py ImageUploadFunction "$(ARTIFACTS_DIR)"
	python tools/check_lambda_artifacts.py ImageUploadFunction "$(ARTIFACTS_DIR)"

build-ThnPrivateImageUploadV2Function:
	python tools/build_lambda_artifact.py ThnPrivateImageUploadV2Function "$(ARTIFACTS_DIR)"
	python tools/check_lambda_artifacts.py ThnPrivateImageUploadV2Function "$(ARTIFACTS_DIR)"
