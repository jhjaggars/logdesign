#!/bin/bash

# Revert to development overlay configuration for vector-logs DaemonSet
echo "Reverting to development configuration for vector-logs DaemonSet..."

# Revert environment variables to development configuration
oc set env daemonset/vector-logs -n logging \
  VECTOR_LOG=DEBUG \
  AWS_REGION=us-east-2 \
  AWS_DEFAULT_REGION=us-east-2 \
  S3_BUCKET_NAME=multi-tenant-logging-development-central-12345678 \
  S3_WRITER_ROLE_ARN=arn:aws:iam::641875867446:role/multi-tenant-logging-development-central-s3-writer-role \
  CLUSTER_ID=scuppett-oepz

if [ $? -eq 0 ]; then
    echo "Successfully reverted to development configuration"
    echo "DaemonSet will now perform a rolling update with original environment variables"
else
    echo "Failed to revert to development configuration"
    exit 1
fi