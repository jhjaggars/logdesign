"""
Pytest configuration and shared fixtures for API tests
"""

import pytest
import boto3
from moto import mock_aws
from src.services.dynamo import TenantService


@pytest.fixture(scope="function")
def aws_credentials():
    """Mocked AWS Credentials for moto"""
    import os
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture(scope="function")
def dynamodb_table(aws_credentials):
    """Create a mocked DynamoDB table for testing"""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        
        # Create the table
        table = dynamodb.create_table(
            TableName="test-tenant-configs",
            KeySchema=[
                {"AttributeName": "tenant_id", "KeyType": "HASH"}
            ],
            AttributeDefinitions=[
                {"AttributeName": "tenant_id", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST"
        )
        
        yield table


@pytest.fixture(scope="function")
def tenant_service(dynamodb_table):
    """Create a TenantService instance with mocked DynamoDB"""
    return TenantService(table_name="test-tenant-configs", region="us-east-1")


@pytest.fixture
def sample_tenant_data():
    """Sample tenant data for testing"""
    return {
        "tenant_id": "test-tenant",
        "log_distribution_role_arn": "arn:aws:iam::123456789012:role/TestRole",
        "log_group_name": "/aws/logs/test-tenant",
        "target_region": "us-east-1",
        "enabled": True,
        "desired_logs": ["app1", "app2"]
    }


@pytest.fixture
def sample_tenant_minimal():
    """Minimal tenant data for testing"""
    return {
        "tenant_id": "minimal-tenant",
        "log_distribution_role_arn": "arn:aws:iam::123456789012:role/MinimalRole",
        "log_group_name": "/aws/logs/minimal-tenant",
        "target_region": "us-west-2"
    }


@pytest.fixture
def multiple_tenants():
    """Multiple tenant configurations for list testing"""
    return [
        {
            "tenant_id": "tenant-1",
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/Role1",
            "log_group_name": "/aws/logs/tenant-1",
            "target_region": "us-east-1",
            "enabled": True
        },
        {
            "tenant_id": "tenant-2",
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/Role2",
            "log_group_name": "/aws/logs/tenant-2",
            "target_region": "us-west-2",
            "enabled": False,
            "desired_logs": ["payment-service"]
        },
        {
            "tenant_id": "tenant-3",
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/Role3",
            "log_group_name": "/aws/logs/tenant-3",
            "target_region": "eu-west-1",
            "enabled": True,
            "desired_logs": ["user-service", "auth-service"]
        }
    ]


@pytest.fixture
def populated_table(tenant_service, multiple_tenants):
    """A tenant service with pre-populated test data"""
    for tenant in multiple_tenants:
        tenant_service.create_tenant(tenant)
    return tenant_service