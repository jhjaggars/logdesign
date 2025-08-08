#!/bin/bash

# Apply amador overlay configuration to running vector-logs DaemonSet
echo "Applying amador configuration to vector-logs DaemonSet..."

# Patch environment variables individually to avoid conflicts
oc set env daemonset/vector-logs -n logging \
  VECTOR_LOG=DEBUG \
  AWS_REGION=us-east-1 \
  AWS_DEFAULT_REGION=us-east-1 \
  S3_BUCKET_NAME=cp-logs-test \
  S3_WRITER_ROLE_ARN=arn:aws:iam::692859910892:role/ManagedOpenShift-Observability \
  CLUSTER_ID=scuppett-oepz

if [ $? -eq 0 ]; then
    echo "Successfully applied amador configuration"
    echo "DaemonSet will now perform a rolling update with new environment variables"
else
    echo "Failed to apply amador configuration"
    exit 1
fi
