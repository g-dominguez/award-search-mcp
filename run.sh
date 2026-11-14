#!/bin/bash
# Started by Lambda Web Adapter when a copy of the Lambda starts (see template.yaml).
# The adapter then forwards each HTTPS request to this server on port 8080.
# $LAMBDA_RUNTIME_DIR is where Lambda keeps boto3.
PYTHONPATH=$PYTHONPATH:$LAMBDA_RUNTIME_DIR \
exec python -m award_search.http_app
