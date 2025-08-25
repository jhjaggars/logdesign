"""
Unit tests for DynamoDB tenant service
"""

import pytest
from botocore.exceptions import ClientError
from src.services.dynamo import TenantService, TenantNotFoundError, DynamoDBError


class TestTenantService:
    """Test cases for TenantService class"""
    
    def test_get_tenant_success(self, tenant_service, sample_tenant_data):
        """Test successful tenant retrieval"""
        # Create a tenant first
        tenant_service.create_tenant(sample_tenant_data)
        
        # Retrieve the tenant
        result = tenant_service.get_tenant("test-tenant")
        
        assert result["tenant_id"] == "test-tenant"
        assert result["log_distribution_role_arn"] == "arn:aws:iam::123456789012:role/TestRole"
        assert result["log_group_name"] == "/aws/logs/test-tenant"
        assert result["target_region"] == "us-east-1"
        assert result["enabled"] is True
        assert result["desired_logs"] == ["app1", "app2"]
    
    def test_get_tenant_not_found(self, tenant_service):
        """Test tenant retrieval when tenant doesn't exist"""
        with pytest.raises(TenantNotFoundError, match="Tenant 'nonexistent' not found"):
            tenant_service.get_tenant("nonexistent")
    
    def test_create_tenant_success(self, tenant_service, sample_tenant_data):
        """Test successful tenant creation"""
        result = tenant_service.create_tenant(sample_tenant_data)
        
        assert result["tenant_id"] == "test-tenant"
        assert result["enabled"] is True  # Default value should be set
        
        # Verify tenant was actually created
        retrieved = tenant_service.get_tenant("test-tenant")
        assert retrieved["tenant_id"] == "test-tenant"
    
    def test_create_tenant_minimal_data(self, tenant_service, sample_tenant_minimal):
        """Test tenant creation with minimal required fields"""
        result = tenant_service.create_tenant(sample_tenant_minimal)
        
        assert result["tenant_id"] == "minimal-tenant"
        assert result["enabled"] is True  # Should default to True
        assert "desired_logs" not in result or result["desired_logs"] is None
    
    def test_create_tenant_duplicate(self, tenant_service, sample_tenant_data):
        """Test tenant creation with duplicate tenant_id"""
        # Create tenant first time
        tenant_service.create_tenant(sample_tenant_data)
        
        # Try to create again - should fail
        with pytest.raises(DynamoDBError, match="already exists"):
            tenant_service.create_tenant(sample_tenant_data)
    
    def test_update_tenant_success(self, tenant_service, sample_tenant_data):
        """Test successful tenant update"""
        # Create tenant first
        tenant_service.create_tenant(sample_tenant_data)
        
        # Update tenant
        update_data = {
            "log_group_name": "/aws/logs/updated-tenant",
            "enabled": False,
            "desired_logs": ["new-app"]
        }
        
        result = tenant_service.update_tenant("test-tenant", update_data)
        
        assert result["tenant_id"] == "test-tenant"
        assert result["log_group_name"] == "/aws/logs/updated-tenant"
        assert result["enabled"] is False
        assert result["desired_logs"] == ["new-app"]
        # Other fields should remain unchanged
        assert result["log_distribution_role_arn"] == "arn:aws:iam::123456789012:role/TestRole"
    
    def test_update_tenant_not_found(self, tenant_service):
        """Test tenant update when tenant doesn't exist"""
        update_data = {"enabled": False}
        
        with pytest.raises(TenantNotFoundError, match="Tenant 'nonexistent' not found"):
            tenant_service.update_tenant("nonexistent", update_data)
    
    def test_update_tenant_no_fields(self, tenant_service, sample_tenant_data):
        """Test tenant update with no fields to update"""
        tenant_service.create_tenant(sample_tenant_data)
        
        with pytest.raises(DynamoDBError, match="No fields to update"):
            tenant_service.update_tenant("test-tenant", {})
    
    def test_patch_tenant_success(self, tenant_service, sample_tenant_data):
        """Test successful tenant patch (partial update)"""
        # Create tenant first
        tenant_service.create_tenant(sample_tenant_data)
        
        # Patch tenant
        patch_data = {"enabled": False}
        
        result = tenant_service.patch_tenant("test-tenant", patch_data)
        
        assert result["tenant_id"] == "test-tenant"
        assert result["enabled"] is False
        # Other fields should remain unchanged
        assert result["log_group_name"] == "/aws/logs/test-tenant"
        assert result["desired_logs"] == ["app1", "app2"]
    
    def test_delete_tenant_success(self, tenant_service, sample_tenant_data):
        """Test successful tenant deletion"""
        # Create tenant first
        tenant_service.create_tenant(sample_tenant_data)
        
        # Delete tenant
        result = tenant_service.delete_tenant("test-tenant")
        assert result is True
        
        # Verify tenant is gone
        with pytest.raises(TenantNotFoundError):
            tenant_service.get_tenant("test-tenant")
    
    def test_delete_tenant_not_found(self, tenant_service):
        """Test tenant deletion when tenant doesn't exist"""
        with pytest.raises(TenantNotFoundError, match="Tenant 'nonexistent' not found"):
            tenant_service.delete_tenant("nonexistent")
    
    def test_list_tenants_empty(self, tenant_service):
        """Test listing tenants when table is empty"""
        result = tenant_service.list_tenants()
        
        assert result["tenants"] == []
        assert result["count"] == 0
        assert result["limit"] == 50
        assert "last_key" not in result
    
    def test_list_tenants_with_data(self, populated_table):
        """Test listing tenants with data"""
        result = populated_table.list_tenants()
        
        assert len(result["tenants"]) == 3
        assert result["count"] == 3
        assert result["limit"] == 50
        
        # Verify all tenants are present
        tenant_ids = [t["tenant_id"] for t in result["tenants"]]
        assert "tenant-1" in tenant_ids
        assert "tenant-2" in tenant_ids
        assert "tenant-3" in tenant_ids
    
    def test_list_tenants_with_limit(self, populated_table):
        """Test listing tenants with limit"""
        result = populated_table.list_tenants(limit=2)
        
        assert len(result["tenants"]) == 2
        assert result["count"] == 2
        assert result["limit"] == 2
        # Should have pagination key since we limited results
        assert "last_key" in result
    
    def test_validate_tenant_config_valid(self, tenant_service, sample_tenant_data):
        """Test tenant configuration validation for valid tenant"""
        tenant_service.create_tenant(sample_tenant_data)
        
        result = tenant_service.validate_tenant_config("test-tenant")
        
        assert result["tenant_id"] == "test-tenant"
        assert result["valid"] is True
        assert len(result["checks"]) > 0
        
        # Check that required fields are validated
        check_fields = [check["field"] for check in result["checks"]]
        assert "log_distribution_role_arn" in check_fields
        assert "log_group_name" in check_fields
        assert "target_region" in check_fields
    
    def test_validate_tenant_config_invalid_role_arn(self, tenant_service):
        """Test tenant configuration validation with invalid role ARN"""
        invalid_tenant = {
            "tenant_id": "invalid-tenant",
            "log_distribution_role_arn": "invalid-arn",
            "log_group_name": "/aws/logs/invalid-tenant",
            "target_region": "us-east-1"
        }
        tenant_service.create_tenant(invalid_tenant)
        
        result = tenant_service.validate_tenant_config("invalid-tenant")
        
        assert result["tenant_id"] == "invalid-tenant"
        assert result["valid"] is False
        
        # Find the role ARN validation check
        role_check = next(
            (check for check in result["checks"] 
             if check["field"] == "log_distribution_role_arn" and check["status"] == "invalid"),
            None
        )
        assert role_check is not None
        assert "invalid" in role_check["message"]
    
    def test_validate_tenant_config_missing_fields(self, tenant_service):
        """Test tenant configuration validation with missing fields"""
        incomplete_tenant = {
            "tenant_id": "incomplete-tenant",
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/TestRole"
            # Missing log_group_name and target_region
        }
        
        # Use low-level DynamoDB operation to create incomplete tenant
        tenant_service.table.put_item(Item=incomplete_tenant)
        
        result = tenant_service.validate_tenant_config("incomplete-tenant")
        
        assert result["tenant_id"] == "incomplete-tenant"
        assert result["valid"] is False
        
        # Check for missing field validations
        missing_checks = [
            check for check in result["checks"] 
            if check["status"] == "missing"
        ]
        assert len(missing_checks) >= 2  # log_group_name and target_region
    
    def test_validate_tenant_config_not_found(self, tenant_service):
        """Test tenant configuration validation when tenant doesn't exist"""
        with pytest.raises(TenantNotFoundError, match="Tenant 'nonexistent' not found"):
            tenant_service.validate_tenant_config("nonexistent")


class TestTenantServiceEdgeCases:
    """Test edge cases and error scenarios"""
    
    def test_initialization(self):
        """Test service initialization"""
        service = TenantService("test-table", "us-west-2")
        assert service.table_name == "test-table"
        assert service.region == "us-west-2"
    
    def test_initialization_defaults(self):
        """Test service initialization with defaults"""
        service = TenantService("test-table")
        assert service.table_name == "test-table"
        assert service.region == "us-east-1"
    
    def test_lazy_initialization(self, tenant_service):
        """Test that DynamoDB resources are initialized lazily"""
        # Before accessing properties, should be None
        assert tenant_service._dynamodb is None
        assert tenant_service._table is None
        
        # After accessing, should be initialized
        _ = tenant_service.dynamodb
        assert tenant_service._dynamodb is not None
        
        _ = tenant_service.table
        assert tenant_service._table is not None