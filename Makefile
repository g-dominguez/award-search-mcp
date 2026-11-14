# How `sam build` packages the Lambda (see template.yaml). Only the server code, its start
# script and requirements.txt go in, so tests, evals and fixtures stay out.
# Packages are downloaded for Lambda's machines (Linux, arm64, Python 3.12), not this one.

build-AwardSearchFunction:
	python3 -m pip install --requirement requirements.txt --target "$(ARTIFACTS_DIR)" \
		--platform manylinux2014_aarch64 --implementation cp --python-version 3.12 \
		--only-binary=:all: --quiet
	cp -R award_search run.sh "$(ARTIFACTS_DIR)/"
	rm -rf "$(ARTIFACTS_DIR)/award_search/__pycache__"
