"""
Unit tests for API endpoints
"""

import json
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch
from src.app import app
from src.services.dynamo import TenantNotFoundError, DynamoDBError


@pytest.fixture
def client():
    """Create a test client for the FastAPI app"""
    return TestClient(app)


@pytest.fixture
def mock_tenant_service():
    """Create a mock tenant service"""
    return Mock()


class TestHealthEndpoint:
    """Test cases for health check endpoint"""
    
    def test_health_check_success(self, client):
        """Test successful health check"""
        response = client.get("/api/v1/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "tenant-management-api"
        assert "timestamp" in data
        assert "version" in data


class TestTenantEndpoints:
    """Test cases for tenant management endpoints"""
    
    @patch('src.app.tenant_service')
    def test_list_tenants_success(self, mock_tenant_service, client):
        """Test successful tenant listing"""
        # Setup mock
        mock_tenant_service.list_tenants.return_value = {
            "tenants": [
                {
                    "tenant_id": "test-tenant",
                    "log_distribution_role_arn": "arn:aws:iam::123456789012:role/TestRole",
                    "log_group_name": "/aws/logs/test-tenant",
                    "target_region": "us-east-1",
                    "enabled": True
                }
            ],
            "count": 1,
            "limit": 50
        }
        
        response = client.get("/api/v1/tenants")
        
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "tenants" in data["data"]
        assert "total" in data["data"]
        assert len(data["data"]["tenants"]) == 1
        mock_tenant_service.list_tenants.assert_called_once_with(limit=50, last_key=None)
    
    @patch('src.app.tenant_service')
    def test_list_tenants_with_pagination(self, mock_tenant_service, client):
        """Test tenant listing with pagination parameters"""
        # Setup mock
        mock_tenant_service.list_tenants.return_value = {
            "tenants": [],
            "count": 0,
            "limit": 10
        }
        
        response = client.get("/api/v1/tenants?limit=10&offset=20")
        
        assert response.status_code == 200
        mock_tenant_service.list_tenants.assert_called_once_with(limit=10, last_key=None)
    
    @patch('src.app.tenant_service')
    def test_list_tenants_service_error(self, mock_tenant_service, client):
        """Test tenant listing when service throws error"""
        # Setup mock to raise error
        mock_tenant_service.list_tenants.side_effect = DynamoDBError("Connection failed")
        
        response = client.get("/api/v1/tenants")
        
        assert response.status_code == 500
        data = response.json()
        assert "Failed to list tenants" in data["detail"]
    
    @patch('src.app.tenant_service')
    def test_get_tenant_success(self, mock_tenant_service, client):
        """Test successful tenant retrieval"""
        # Setup mock
        mock_tenant_service.get_tenant.return_value = {
            "tenant_id": "test-tenant",
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/TestRole",
            "log_group_name": "/aws/logs/test-tenant",
            "target_region": "us-east-1",
            "enabled": True
        }
        
        response = client.get("/api/v1/tenants/test-tenant")
        
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["tenant_id"] == "test-tenant"
        mock_tenant_service.get_tenant.assert_called_once_with("test-tenant")
    
    @patch('src.app.tenant_service')
    def test_get_tenant_not_found(self, mock_tenant_service, client):
        """Test tenant retrieval when tenant doesn't exist"""
        # Setup mock to raise TenantNotFoundError
        mock_tenant_service.get_tenant.side_effect = TenantNotFoundError("Tenant not found")
        
        response = client.get("/api/v1/tenants/nonexistent")
        
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()
    
    @patch('src.app.tenant_service')
    def test_create_tenant_success(self, mock_tenant_service, client):
        """Test successful tenant creation"""
        # Setup mock
        tenant_data = {
            "tenant_id": "new-tenant",
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/NewRole",
            "log_group_name": "/aws/logs/new-tenant",
            "target_region": "us-east-1"
        }
        mock_tenant_service.create_tenant.return_value = tenant_data
        
        response = client.post("/api/v1/tenants", json=tenant_data)
        
        assert response.status_code == 201
        data = response.json()
        assert data["data"]["tenant_id"] == "new-tenant"
        mock_tenant_service.create_tenant.assert_called_once()
    
    @patch('src.app.tenant_service')
    def test_create_tenant_invalid_data(self, mock_tenant_service, client):
        """Test tenant creation with invalid data"""
        # Setup mock
        
        invalid_data = {
            "tenant_id": "new-tenant",
            "log_distribution_role_arn": "invalid-arn",  # Invalid ARN format
            "log_group_name": "/aws/logs/new-tenant",
            "target_region": "us-east-1"
        }
        
        response = client.post("/api/v1/tenants", json=invalid_data)
        
        assert response.status_code == 422  # FastAPI validation error
        data = response.json()
        assert "detail" in data
        # Verify validation error mentions the role ARN
        error_details = str(data["detail"])
        assert "log_distribution_role_arn" in error_details
    
    @patch('src.app.tenant_service')
    def test_create_tenant_duplicate(self, mock_tenant_service, client):
        """Test tenant creation with duplicate tenant_id"""
        # Setup mock to raise DynamoDBError for duplicate
        mock_tenant_service.create_tenant.side_effect = DynamoDBError("Tenant already exists")
        
        tenant_data = {
            "tenant_id": "existing-tenant",
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/ExistingRole",
            "log_group_name": "/aws/logs/existing-tenant",
            "target_region": "us-east-1"
        }
        
        response = client.post("/api/v1/tenants", json=tenant_data)
        
        assert response.status_code == 400
        data = response.json()
        assert "already exists" in data["detail"].lower()
    
    @patch('src.app.tenant_service')
    def test_update_tenant_success(self, mock_tenant_service, client):
        """Test successful tenant update"""
        # Setup mock
        updated_data = {
            "tenant_id": "test-tenant",
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/UpdatedRole",
            "log_group_name": "/aws/logs/updated-tenant",
            "target_region": "us-west-2",
            "enabled": False
        }
        mock_tenant_service.update_tenant.return_value = updated_data
        
        update_request = {
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/UpdatedRole",
            "log_group_name": "/aws/logs/updated-tenant",
            "target_region": "us-west-2",
            "enabled": False
        }
        
        response = client.put("/api/v1/tenants/test-tenant", json=update_request)
        
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["tenant_id"] == "test-tenant"
        assert data["data"]["enabled"] is False
        mock_tenant_service.update_tenant.assert_called_once_with("test-tenant", update_request)
    
    @patch('src.app.tenant_service')
    def test_update_tenant_not_found(self, mock_tenant_service, client):
        """Test tenant update when tenant doesn't exist"""
        # Setup mock to raise TenantNotFoundError
        mock_tenant_service.update_tenant.side_effect = TenantNotFoundError("Tenant not found")
        
        update_request = {"enabled": False}
        
        response = client.put("/api/v1/tenants/nonexistent", json=update_request)
        
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()
    
    @patch('src.app.tenant_service')
    def test_patch_tenant_success(self, mock_tenant_service, client):
        """Test successful tenant patch (partial update)"""
        # Setup mock
        patched_data = {
            "tenant_id": "test-tenant",
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/TestRole",
            "log_group_name": "/aws/logs/test-tenant",
            "target_region": "us-east-1",
            "enabled": False  # Only this field was patched
        }
        mock_tenant_service.patch_tenant.return_value = patched_data
        
        patch_request = {"enabled": False}
        
        response = client.patch("/api/v1/tenants/test-tenant", json=patch_request)
        
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["tenant_id"] == "test-tenant"
        assert data["data"]["enabled"] is False
        mock_tenant_service.patch_tenant.assert_called_once_with("test-tenant", patch_request)
    
    @patch('src.app.tenant_service')
    def test_delete_tenant_success(self, mock_tenant_service, client):
        """Test successful tenant deletion"""
        # Setup mock
        mock_tenant_service.delete_tenant.return_value = True
        
        response = client.delete("/api/v1/tenants/test-tenant")
        
        assert response.status_code == 200
        data = response.json()
        assert "deleted" in data["message"].lower()
        mock_tenant_service.delete_tenant.assert_called_once_with("test-tenant")
    
    @patch('src.app.tenant_service')
    def test_delete_tenant_not_found(self, mock_tenant_service, client):
        """Test tenant deletion when tenant doesn't exist"""
        # Setup mock to raise TenantNotFoundError
        mock_tenant_service.delete_tenant.side_effect = TenantNotFoundError("Tenant not found")
        
        response = client.delete("/api/v1/tenants/nonexistent")
        
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()
    
    @patch('src.app.tenant_service')
    def test_validate_tenant_success(self, mock_tenant_service, client):
        """Test successful tenant validation"""
        # Setup mock
        validation_result = {
            "tenant_id": "test-tenant",
            "valid": True,
            "checks": [
                {"field": "log_distribution_role_arn", "status": "ok", "message": "Field is present"},
                {"field": "log_group_name", "status": "ok", "message": "Field is present"},
                {"field": "target_region", "status": "ok", "message": "Field is present"}
            ]
        }
        mock_tenant_service.validate_tenant_config.return_value = validation_result
        
        response = client.get("/api/v1/tenants/test-tenant/validate")
        
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["tenant_id"] == "test-tenant"
        assert data["data"]["valid"] is True
        assert len(data["data"]["checks"]) == 3
        mock_tenant_service.validate_tenant_config.assert_called_once_with("test-tenant")
    
    @patch('src.app.tenant_service')
    def test_validate_tenant_invalid(self, mock_tenant_service, client):
        """Test tenant validation with invalid configuration"""
        # Setup mock
        validation_result = {
            "tenant_id": "invalid-tenant",
            "valid": False,
            "checks": [
                {"field": "log_distribution_role_arn", "status": "invalid", "message": "Role ARN format is invalid"},
                {"field": "log_group_name", "status": "missing", "message": "Required field is missing"}
            ]
        }
        mock_tenant_service.validate_tenant_config.return_value = validation_result
        
        response = client.get("/api/v1/tenants/invalid-tenant/validate")
        
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["tenant_id"] == "invalid-tenant"
        assert data["data"]["valid"] is False
        assert len(data["data"]["checks"]) == 2
    
    @patch('src.app.tenant_service')
    def test_validate_tenant_not_found(self, mock_tenant_service, client):
        """Test tenant validation when tenant doesn't exist"""
        # Setup mock to raise TenantNotFoundError
        mock_tenant_service.validate_tenant_config.side_effect = TenantNotFoundError("Tenant not found")
        
        response = client.get("/api/v1/tenants/nonexistent/validate")
        
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()


class TestErrorHandling:
    """Test cases for error handling scenarios"""
    
    def test_invalid_json_body(self, client):
        """Test handling of invalid JSON in request body"""
        response = client.post(
            "/api/v1/tenants",
            data="invalid json",
            headers={"content-type": "application/json"}
        )
        
        assert response.status_code == 422
    
    def test_missing_required_fields(self, client):
        """Test validation of missing required fields"""
        incomplete_data = {
            "tenant_id": "incomplete-tenant"
            # Missing required fields
        }
        
        response = client.post("/api/v1/tenants", json=incomplete_data)
        
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        # Should mention missing required fields
        error_details = str(data["detail"])
        assert any(field in error_details for field in ["log_distribution_role_arn", "log_group_name", "target_region"])
    
    def test_invalid_desired_logs_format(self, client):
        """Test validation of invalid desired_logs format"""
        invalid_data = {
            "tenant_id": "test-tenant",
            "log_distribution_role_arn": "arn:aws:iam::123456789012:role/TestRole",
            "log_group_name": "/aws/logs/test-tenant",
            "target_region": "us-east-1",
            "desired_logs": []  # Empty list is invalid
        }
        
        response = client.post("/api/v1/tenants", json=invalid_data)
        
        assert response.status_code == 422
        data = response.json()
        error_details = str(data["detail"])
        assert "desired_logs" in error_details